from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class TransactionBase(BaseModel):
    date: datetime
    amount: float
    payee: Optional[str] = None
    memo: Optional[str] = None


class TransactionCreate(TransactionBase):
    source: str = "csv"
    source_account: Optional[str] = None
    source_transaction_id: Optional[str] = None


class TransactionResponse(TransactionBase):
    id: int
    transaction_hash: str
    source: str
    source_account: Optional[str] = None
    ynab_transaction_id: Optional[str] = None
    imported_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TransactionPreview(TransactionBase):
    """Preview of a transaction before import."""
    is_duplicate: bool = False
    transaction_hash: str
    raw_data: Optional[dict] = None


class TransactionImportRequest(BaseModel):
    """Request to import transactions to YNAB."""
    transactions: List[TransactionCreate]
    ynab_budget_id: str
    ynab_account_id: str
    skip_duplicates: bool = True
