import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..config import get_settings
from ..models.database import AkahuAccount, SyncLog
from .akahu_client import AkahuClient
from .ynab_client import YNABClient
from .dedup import DeduplicationService
from ..schemas.transaction import TransactionCreate

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None
_engine = None
_session_factory = None


async def get_scheduler_session() -> AsyncSession:
    """Get a database session for the scheduler."""
    global _engine, _session_factory
    
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, echo=False)
        _session_factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    
    return _session_factory()


async def sync_akahu_account_job(akahu_account_id: str):
    """
    Background job to sync an Akahu account.
    This runs in the scheduler context.
    """
    logger.info(f"Starting scheduled sync for Akahu account: {akahu_account_id}")
    
    session = await get_scheduler_session()
    
    try:
        # Get the account link
        result = await session.execute(
            select(AkahuAccount).where(AkahuAccount.akahu_account_id == akahu_account_id)
        )
        link = result.scalar_one_or_none()
        
        if not link or not link.ynab_account_id:
            logger.error(f"Account {akahu_account_id} not linked to YNAB")
            return
        
        # Create sync log entry
        sync_log = SyncLog(
            akahu_account_id=akahu_account_id,
            status='running',
            trigger='scheduled'
        )
        session.add(sync_log)
        await session.commit()
        
        # Update account status
        link.last_sync_status = 'running'
        await session.commit()
        
        # Fetch transactions from Akahu
        akahu = AkahuClient()
        days_to_sync = link.schedule_days_to_sync or 7
        start_date = datetime.now() - timedelta(days=days_to_sync)
        
        try:
            transactions = await akahu.get_account_transactions(
                akahu_account_id,
                start_date=start_date
            )
        except Exception as e:
            logger.error(f"Failed to fetch Akahu transactions: {e}")
            sync_log.status = 'failed'
            sync_log.error_message = str(e)
            sync_log.completed_at = datetime.utcnow()
            link.last_sync_status = 'failed'
            link.last_sync_message = str(e)
            await session.commit()
            return
        
        sync_log.transactions_found = len(transactions)
        
        if not transactions:
            sync_log.status = 'success'
            sync_log.completed_at = datetime.utcnow()
            link.last_sync_status = 'success'
            link.last_sync_message = 'No transactions found'
            link.last_sync_imported = 0
            link.last_synced_at = datetime.utcnow()
            link.next_sync_at = datetime.utcnow() + timedelta(hours=link.schedule_interval_hours)
            await session.commit()
            logger.info(f"Scheduled sync complete for {akahu_account_id}: no transactions")
            return
        
        # Check for duplicates
        dedup = DeduplicationService(session)
        existing_hashes = await dedup.get_existing_hashes()
        
        # Convert to YNAB format and filter duplicates
        ynab_transactions = []
        tx_creates = []
        skipped = 0
        
        for tx in transactions:
            tx_hash = dedup.generate_hash(tx.date, tx.amount, tx.merchant or tx.description)
            
            if tx_hash in existing_hashes:
                skipped += 1
                continue
            
            ynab_transactions.append({
                "date": tx.date,
                "amount": tx.amount,
                "payee": tx.merchant or tx.description[:50] if tx.description else None,
                "memo": tx.description
            })
            
            tx_creates.append(TransactionCreate(
                date=tx.date,
                amount=tx.amount,
                payee=tx.merchant or tx.description[:50] if tx.description else None,
                memo=tx.description,
                source="akahu",
                source_account=akahu_account_id,
                source_transaction_id=tx.id
            ))
        
        sync_log.transactions_skipped = skipped
        
        if not ynab_transactions:
            sync_log.status = 'success'
            sync_log.completed_at = datetime.utcnow()
            sync_log.transactions_imported = 0
            link.last_sync_status = 'success'
            link.last_sync_message = f'All {skipped} transactions were duplicates'
            link.last_sync_imported = 0
            link.last_synced_at = datetime.utcnow()
            link.next_sync_at = datetime.utcnow() + timedelta(hours=link.schedule_interval_hours)
            await session.commit()
            logger.info(f"Scheduled sync complete for {akahu_account_id}: all duplicates")
            return
        
        # Import to YNAB
        ynab = YNABClient()
        try:
            import_result = await ynab.import_transactions(
                link.ynab_budget_id,
                link.ynab_account_id,
                ynab_transactions
            )
        except Exception as e:
            logger.error(f"Failed to import to YNAB: {e}")
            sync_log.status = 'failed'
            sync_log.error_message = str(e)
            sync_log.completed_at = datetime.utcnow()
            link.last_sync_status = 'failed'
            link.last_sync_message = f'YNAB import failed: {str(e)}'
            await session.commit()
            return
        
        # Record successful imports
        await dedup.record_imports_batch(
            tx_creates,
            link.ynab_budget_id,
            link.ynab_account_id,
            import_result.transaction_ids
        )
        
        # Update sync log
        sync_log.status = 'success'
        sync_log.completed_at = datetime.utcnow()
        sync_log.transactions_imported = len(import_result.transaction_ids)
        sync_log.ynab_duplicates = len(import_result.duplicate_import_ids)
        
        # Update account
        link.last_sync_status = 'success'
        link.last_sync_message = f'Imported {len(import_result.transaction_ids)} transactions'
        link.last_sync_imported = len(import_result.transaction_ids)
        link.last_synced_at = datetime.utcnow()
        link.next_sync_at = datetime.utcnow() + timedelta(hours=link.schedule_interval_hours)
        
        await session.commit()
        logger.info(f"Scheduled sync complete for {akahu_account_id}: imported {len(import_result.transaction_ids)}")
        
    except Exception as e:
        logger.exception(f"Error in scheduled sync for {akahu_account_id}: {e}")
        try:
            link.last_sync_status = 'failed'
            link.last_sync_message = str(e)
            await session.commit()
        except:
            pass
    finally:
        await session.close()


def get_scheduler() -> AsyncIOScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            jobstores={'default': MemoryJobStore()},
            job_defaults={
                'coalesce': True,  # Combine multiple pending executions
                'max_instances': 1,  # Only one instance per job
                'misfire_grace_time': 60 * 30  # 30 minute grace period
            }
        )
    return _scheduler


async def schedule_account_sync(account: AkahuAccount):
    """
    Schedule or update the sync job for an Akahu account.
    """
    scheduler = get_scheduler()
    job_id = f"sync_{account.akahu_account_id}"
    
    # Remove existing job if any
    try:
        scheduler.remove_job(job_id)
    except:
        pass
    
    if not account.schedule_enabled:
        logger.info(f"Schedule disabled for account {account.akahu_account_id}")
        return
    
    # Add new job with interval trigger
    scheduler.add_job(
        sync_akahu_account_job,
        trigger=IntervalTrigger(hours=account.schedule_interval_hours),
        args=[account.akahu_account_id],
        id=job_id,
        name=f"Sync {account.account_name or account.akahu_account_id}",
        replace_existing=True
    )
    
    logger.info(f"Scheduled sync for account {account.akahu_account_id} every {account.schedule_interval_hours} hours")


async def remove_account_schedule(akahu_account_id: str):
    """Remove the scheduled sync job for an account."""
    scheduler = get_scheduler()
    job_id = f"sync_{akahu_account_id}"
    
    try:
        scheduler.remove_job(job_id)
        logger.info(f"Removed schedule for account {akahu_account_id}")
    except:
        pass


async def initialize_scheduler():
    """
    Initialize the scheduler and load existing schedules from the database.
    """
    scheduler = get_scheduler()
    
    if scheduler.running:
        return
    
    # Start the scheduler
    scheduler.start()
    logger.info("Scheduler started")
    
    # Load existing schedules from database
    session = await get_scheduler_session()
    try:
        result = await session.execute(
            select(AkahuAccount).where(AkahuAccount.schedule_enabled == True)
        )
        accounts = result.scalars().all()
        
        for account in accounts:
            if account.ynab_account_id:  # Only schedule if linked to YNAB
                await schedule_account_sync(account)
        
        logger.info(f"Loaded {len(accounts)} scheduled accounts")
    finally:
        await session.close()


async def shutdown_scheduler():
    """Shutdown the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")


def get_scheduled_jobs():
    """Get list of currently scheduled jobs."""
    scheduler = get_scheduler()
    jobs = []
    
    for job in scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'name': job.name,
            'next_run_time': job.next_run_time.isoformat() if job.next_run_time else None,
            'trigger': str(job.trigger)
        })
    
    return jobs
