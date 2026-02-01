from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class AkahuAccountResponse(BaseModel):
    id: str
    name: str
    type: str
    institution: str
    balance: Optional[float] = None
    # Linked YNAB account
    ynab_budget_id: Optional[str] = None
    ynab_account_id: Optional[str] = None
    auto_sync: bool = False
    last_synced_at: Optional[datetime] = None
    # Schedule info
    schedule_enabled: bool = False
    schedule_interval_hours: int = 6
    schedule_days_to_sync: int = 7
    next_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_message: Optional[str] = None
    last_sync_imported: int = 0


class AkahuTransaction(BaseModel):
    id: str
    account_id: str
    date: datetime
    amount: float
    description: str
    merchant: Optional[str] = None
    category: Optional[str] = None


class AkahuAccountLink(BaseModel):
    """Request to link an Akahu account to a YNAB account."""
    akahu_account_id: str
    ynab_budget_id: str
    ynab_account_id: str
    auto_sync: bool = False


class ScheduleConfig(BaseModel):
    """Configuration for scheduled sync."""
    enabled: bool = True
    interval_hours: int = Field(default=6, ge=1, le=24)  # 1-24 hours
    days_to_sync: int = Field(default=7, ge=1, le=90)  # 1-90 days


class SyncLogResponse(BaseModel):
    """Response for sync log entry."""
    id: int
    akahu_account_id: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str
    transactions_found: int = 0
    transactions_imported: int = 0
    transactions_skipped: int = 0
    ynab_duplicates: int = 0
    error_message: Optional[str] = None
    trigger: str = 'manual'

    class Config:
        from_attributes = True


class ScheduledJobInfo(BaseModel):
    """Information about a scheduled job."""
    id: str
    name: str
    next_run_time: Optional[str] = None
    trigger: str
