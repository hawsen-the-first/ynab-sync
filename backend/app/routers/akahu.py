from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..dependencies import get_db
from ..services.akahu_client import AkahuClient
from ..services.ynab_client import YNABClient
from ..services.dedup import DeduplicationService
from ..services.scheduler import schedule_account_sync, remove_account_schedule, get_scheduled_jobs
from ..models.database import AkahuAccount, SyncLog
from ..schemas.akahu import (
    AkahuAccountResponse,
    AkahuAccountLink,
    AkahuTransaction,
    ScheduleConfig,
    SyncLogResponse,
    ScheduledJobInfo
)
from ..schemas.transaction import TransactionCreate

router = APIRouter(prefix="/akahu", tags=["Akahu"])


@router.get("/test")
async def test_akahu_connection():
    """Test the Akahu API connection."""
    akahu = AkahuClient()
    connected = await akahu.test_connection()
    
    if connected:
        return {"status": "connected", "message": "Successfully connected to Akahu"}
    else:
        raise HTTPException(
            status_code=401,
            detail="Failed to connect to Akahu. Check your tokens."
        )


@router.get("/accounts", response_model=List[AkahuAccountResponse])
async def get_akahu_accounts(db: AsyncSession = Depends(get_db)):
    """Get all connected Akahu bank accounts with their YNAB links."""
    akahu = AkahuClient()
    
    try:
        accounts = await akahu.get_accounts()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch Akahu accounts: {str(e)}")
    
    # Enrich with saved link information
    for account in accounts:
        result = await db.execute(
            select(AkahuAccount).where(AkahuAccount.akahu_account_id == account.id)
        )
        saved = result.scalar_one_or_none()
        
        if saved:
            account.ynab_budget_id = saved.ynab_budget_id
            account.ynab_account_id = saved.ynab_account_id
            account.auto_sync = saved.auto_sync
            account.last_synced_at = saved.last_synced_at
            # Schedule info
            account.schedule_enabled = saved.schedule_enabled
            account.schedule_interval_hours = saved.schedule_interval_hours
            account.schedule_days_to_sync = saved.schedule_days_to_sync
            account.next_sync_at = saved.next_sync_at
            account.last_sync_status = saved.last_sync_status
            account.last_sync_message = saved.last_sync_message
            account.last_sync_imported = saved.last_sync_imported or 0
    
    return accounts


@router.post("/accounts/link")
async def link_akahu_to_ynab(
    link: AkahuAccountLink,
    db: AsyncSession = Depends(get_db)
):
    """Link an Akahu account to a YNAB account."""
    # Check if link already exists
    result = await db.execute(
        select(AkahuAccount).where(
            AkahuAccount.akahu_account_id == link.akahu_account_id
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        # Update existing link
        existing.ynab_budget_id = link.ynab_budget_id
        existing.ynab_account_id = link.ynab_account_id
        existing.auto_sync = link.auto_sync
        existing.updated_at = datetime.utcnow()
    else:
        # Create new link
        # Get account details from Akahu
        akahu = AkahuClient()
        accounts = await akahu.get_accounts()
        account_info = next(
            (a for a in accounts if a.id == link.akahu_account_id),
            None
        )
        
        new_link = AkahuAccount(
            akahu_account_id=link.akahu_account_id,
            account_name=account_info.name if account_info else None,
            account_type=account_info.type if account_info else None,
            institution=account_info.institution if account_info else None,
            ynab_budget_id=link.ynab_budget_id,
            ynab_account_id=link.ynab_account_id,
            auto_sync=link.auto_sync
        )
        db.add(new_link)
    
    await db.commit()
    
    return {"status": "success", "message": "Account linked successfully"}


@router.delete("/accounts/{akahu_account_id}/link")
async def unlink_akahu_account(
    akahu_account_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Remove the link between an Akahu and YNAB account."""
    result = await db.execute(
        select(AkahuAccount).where(
            AkahuAccount.akahu_account_id == akahu_account_id
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        await db.delete(existing)
        await db.commit()
    
    return {"status": "success", "message": "Account unlinked"}


@router.get("/transactions")
async def get_akahu_transactions(
    account_id: Optional[str] = None,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db)
):
    """Get transactions from Akahu for preview."""
    akahu = AkahuClient()
    
    start_date = datetime.now() - timedelta(days=days)
    end_date = datetime.now()
    
    try:
        transactions = await akahu.get_transactions(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transactions: {str(e)}"
        )
    
    # Check for duplicates
    dedup = DeduplicationService(db)
    existing_hashes = await dedup.get_existing_hashes()
    
    result = []
    for tx in transactions:
        tx_hash = dedup.generate_hash(tx.date, tx.amount, tx.merchant or tx.description)
        result.append({
            "id": tx.id,
            "account_id": tx.account_id,
            "date": tx.date.isoformat(),
            "amount": tx.amount,
            "description": tx.description,
            "merchant": tx.merchant,
            "category": tx.category,
            "is_duplicate": tx_hash in existing_hashes,
            "transaction_hash": tx_hash
        })
    
    return result


@router.post("/sync/{akahu_account_id}")
async def sync_akahu_account(
    akahu_account_id: str,
    days: int = Query(default=30, ge=1, le=365),
    skip_duplicates: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """Sync transactions from an Akahu account to its linked YNAB account."""
    # Get the account link
    result = await db.execute(
        select(AkahuAccount).where(
            AkahuAccount.akahu_account_id == akahu_account_id
        )
    )
    link = result.scalar_one_or_none()
    
    if not link or not link.ynab_account_id:
        raise HTTPException(
            status_code=400,
            detail="Akahu account is not linked to a YNAB account"
        )
    
    # Fetch transactions from Akahu
    akahu = AkahuClient()
    start_date = datetime.now() - timedelta(days=days)
    
    try:
        transactions = await akahu.get_account_transactions(
            akahu_account_id,
            start_date=start_date
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch Akahu transactions: {str(e)}"
        )
    
    if not transactions:
        return {
            "imported": 0,
            "skipped_duplicates": 0,
            "message": "No transactions found"
        }
    
    # Check for duplicates
    dedup = DeduplicationService(db)
    existing_hashes = await dedup.get_existing_hashes()
    
    # Convert to YNAB format and filter duplicates
    ynab_transactions = []
    tx_creates = []
    skipped = 0
    
    for tx in transactions:
        tx_hash = dedup.generate_hash(tx.date, tx.amount, tx.merchant or tx.description)
        
        if skip_duplicates and tx_hash in existing_hashes:
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
    
    if not ynab_transactions:
        return {
            "imported": 0,
            "skipped_duplicates": skipped,
            "message": "All transactions were duplicates"
        }
    
    # Import to YNAB
    ynab = YNABClient()
    try:
        import_result = await ynab.import_transactions(
            link.ynab_budget_id,
            link.ynab_account_id,
            ynab_transactions
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to import to YNAB: {str(e)}"
        )
    
    # Record successful imports
    await dedup.record_imports_batch(
        tx_creates,
        link.ynab_budget_id,
        link.ynab_account_id,
        import_result.transaction_ids
    )
    
    # Update last synced timestamp
    link.last_synced_at = datetime.utcnow()
    await db.commit()
    
    return {
        "imported": len(import_result.transaction_ids),
        "ynab_duplicates": len(import_result.duplicate_import_ids),
        "skipped_duplicates": skipped,
        "transaction_ids": import_result.transaction_ids
    }


# Schedule endpoints

@router.get("/accounts/{akahu_account_id}/schedule")
async def get_account_schedule(
    akahu_account_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get the sync schedule for an Akahu account."""
    result = await db.execute(
        select(AkahuAccount).where(AkahuAccount.akahu_account_id == akahu_account_id)
    )
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    return {
        "enabled": account.schedule_enabled,
        "interval_hours": account.schedule_interval_hours,
        "days_to_sync": account.schedule_days_to_sync,
        "next_sync_at": account.next_sync_at.isoformat() if account.next_sync_at else None,
        "last_sync_status": account.last_sync_status,
        "last_sync_message": account.last_sync_message,
        "last_sync_imported": account.last_sync_imported or 0
    }


@router.post("/accounts/{akahu_account_id}/schedule")
async def set_account_schedule(
    akahu_account_id: str,
    config: ScheduleConfig,
    db: AsyncSession = Depends(get_db)
):
    """Set or update the sync schedule for an Akahu account."""
    result = await db.execute(
        select(AkahuAccount).where(AkahuAccount.akahu_account_id == akahu_account_id)
    )
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    if not account.ynab_account_id:
        raise HTTPException(
            status_code=400,
            detail="Account must be linked to YNAB before enabling scheduled sync"
        )
    
    # Validate interval
    valid_intervals = [1, 2, 4, 6, 12, 24]
    if config.interval_hours not in valid_intervals:
        # Round to nearest valid interval
        config.interval_hours = min(valid_intervals, key=lambda x: abs(x - config.interval_hours))
    
    # Update schedule settings
    account.schedule_enabled = config.enabled
    account.schedule_interval_hours = config.interval_hours
    account.schedule_days_to_sync = config.days_to_sync
    
    if config.enabled:
        account.next_sync_at = datetime.utcnow() + timedelta(hours=config.interval_hours)
    else:
        account.next_sync_at = None
    
    account.updated_at = datetime.utcnow()
    await db.commit()
    
    # Update the scheduler
    if config.enabled:
        await schedule_account_sync(account)
    else:
        await remove_account_schedule(akahu_account_id)
    
    return {
        "status": "success",
        "message": f"Schedule {'enabled' if config.enabled else 'disabled'}",
        "next_sync_at": account.next_sync_at.isoformat() if account.next_sync_at else None
    }


@router.delete("/accounts/{akahu_account_id}/schedule")
async def disable_account_schedule(
    akahu_account_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Disable the sync schedule for an Akahu account."""
    result = await db.execute(
        select(AkahuAccount).where(AkahuAccount.akahu_account_id == akahu_account_id)
    )
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    account.schedule_enabled = False
    account.next_sync_at = None
    account.updated_at = datetime.utcnow()
    await db.commit()
    
    # Remove from scheduler
    await remove_account_schedule(akahu_account_id)
    
    return {"status": "success", "message": "Schedule disabled"}


@router.get("/schedules", response_model=List[ScheduledJobInfo])
async def list_scheduled_jobs():
    """List all currently scheduled sync jobs."""
    jobs = get_scheduled_jobs()
    return [ScheduledJobInfo(**job) for job in jobs]


@router.get("/sync-logs", response_model=List[SyncLogResponse])
async def get_sync_logs(
    akahu_account_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
):
    """Get sync logs for all or a specific Akahu account."""
    query = select(SyncLog).order_by(SyncLog.started_at.desc()).limit(limit)
    
    if akahu_account_id:
        query = query.where(SyncLog.akahu_account_id == akahu_account_id)
    
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return logs


@router.get("/sync-logs/{log_id}", response_model=SyncLogResponse)
async def get_sync_log(
    log_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific sync log entry."""
    result = await db.execute(
        select(SyncLog).where(SyncLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    
    if not log:
        raise HTTPException(status_code=404, detail="Sync log not found")
    
    return log
