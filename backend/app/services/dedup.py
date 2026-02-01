import hashlib
from datetime import datetime
from typing import List, Optional, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.database import ImportedTransaction
from ..schemas.transaction import TransactionPreview, TransactionCreate


class DeduplicationService:
    """Service for detecting and preventing duplicate transaction imports."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    @staticmethod
    def generate_hash(
        date: datetime,
        amount: float,
        payee: Optional[str],
        memo: Optional[str] = None
    ) -> str:
        """Generate a unique hash for a transaction."""
        hash_input = f"{date.isoformat()}:{amount}:{payee or ''}:{memo or ''}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]
    
    async def get_existing_hashes(self) -> Set[str]:
        """Get all existing transaction hashes from the database."""
        result = await self.session.execute(
            select(ImportedTransaction.transaction_hash)
        )
        return {row[0] for row in result.fetchall()}
    
    async def check_duplicates(
        self,
        transactions: List[TransactionPreview]
    ) -> List[TransactionPreview]:
        """
        Check transactions for duplicates and mark them.
        
        Returns the same list with is_duplicate flag updated.
        """
        existing_hashes = await self.get_existing_hashes()
        
        for tx in transactions:
            tx.is_duplicate = tx.transaction_hash in existing_hashes
        
        return transactions
    
    async def is_duplicate(self, transaction_hash: str) -> bool:
        """Check if a specific transaction hash already exists."""
        result = await self.session.execute(
            select(ImportedTransaction).where(
                ImportedTransaction.transaction_hash == transaction_hash
            )
        )
        return result.scalar_one_or_none() is not None
    
    async def record_import(
        self,
        transaction: TransactionCreate,
        transaction_hash: str,
        ynab_budget_id: str,
        ynab_account_id: str,
        ynab_transaction_id: Optional[str] = None
    ) -> ImportedTransaction:
        """Record a successfully imported transaction."""
        imported = ImportedTransaction(
            transaction_hash=transaction_hash,
            date=transaction.date,
            amount=transaction.amount,
            payee=transaction.payee,
            memo=transaction.memo,
            source=transaction.source,
            source_account=transaction.source_account,
            source_transaction_id=transaction.source_transaction_id,
            ynab_budget_id=ynab_budget_id,
            ynab_account_id=ynab_account_id,
            ynab_transaction_id=ynab_transaction_id,
            imported_at=datetime.utcnow()
        )
        
        self.session.add(imported)
        await self.session.commit()
        await self.session.refresh(imported)
        
        return imported
    
    async def record_imports_batch(
        self,
        transactions: List[TransactionCreate],
        ynab_budget_id: str,
        ynab_account_id: str,
        ynab_transaction_ids: Optional[List[str]] = None
    ) -> int:
        """
        Record multiple imported transactions at once.
        
        Returns the number of transactions recorded.
        """
        if not transactions:
            return 0
        
        ynab_ids = ynab_transaction_ids or [None] * len(transactions)
        
        for tx, ynab_id in zip(transactions, ynab_ids):
            tx_hash = self.generate_hash(tx.date, tx.amount, tx.payee, tx.memo)
            
            imported = ImportedTransaction(
                transaction_hash=tx_hash,
                date=tx.date,
                amount=tx.amount,
                payee=tx.payee,
                memo=tx.memo,
                source=tx.source,
                source_account=tx.source_account,
                source_transaction_id=tx.source_transaction_id,
                ynab_budget_id=ynab_budget_id,
                ynab_account_id=ynab_account_id,
                ynab_transaction_id=ynab_id,
                imported_at=datetime.utcnow()
            )
            self.session.add(imported)
        
        await self.session.commit()
        return len(transactions)
    
    async def get_import_history(
        self,
        limit: int = 100,
        source: Optional[str] = None
    ) -> List[ImportedTransaction]:
        """Get recent import history."""
        query = select(ImportedTransaction).order_by(
            ImportedTransaction.imported_at.desc()
        ).limit(limit)
        
        if source:
            query = query.where(ImportedTransaction.source == source)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_import_stats(self) -> dict:
        """Get import statistics."""
        from sqlalchemy import func
        
        # Total count
        total_result = await self.session.execute(
            select(func.count(ImportedTransaction.id))
        )
        total = total_result.scalar()
        
        # Count by source
        source_result = await self.session.execute(
            select(
                ImportedTransaction.source,
                func.count(ImportedTransaction.id)
            ).group_by(ImportedTransaction.source)
        )
        by_source = {row[0]: row[1] for row in source_result.fetchall()}
        
        return {
            "total_imported": total,
            "by_source": by_source
        }
