from datetime import date
from typing import Optional, List
from pydantic import BaseModel


class YNABBudget(BaseModel):
    id: str
    name: str


class YNABAccount(BaseModel):
    id: str
    name: str
    type: str
    on_budget: bool
    closed: bool
    balance: int  # In milliunits


class YNABTransactionCreate(BaseModel):
    """Transaction format for YNAB API."""
    account_id: str
    date: date
    amount: int  # In milliunits (1000 = $1.00)
    payee_name: Optional[str] = None
    memo: Optional[str] = None
    cleared: str = "cleared"
    import_id: Optional[str] = None  # For duplicate detection


class YNABTransactionResponse(BaseModel):
    id: str
    date: date
    amount: int
    payee_name: Optional[str] = None
    memo: Optional[str] = None
    cleared: str
    account_id: str


class YNABImportResult(BaseModel):
    """Result of a YNAB import operation."""
    transaction_ids: List[str]
    duplicate_import_ids: List[str]
