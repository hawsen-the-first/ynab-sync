from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, Text, Boolean, JSON
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()


class ImportedTransaction(Base):
    """Track imported transactions to prevent duplicates."""
    __tablename__ = "imported_transactions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # Transaction identification
    transaction_hash = Column(String(64), unique=True, index=True, nullable=False)
    
    # Original transaction data
    date = Column(DateTime, nullable=False)
    amount = Column(Float, nullable=False)
    payee = Column(String(255), nullable=True)
    memo = Column(Text, nullable=True)
    
    # Source information
    source = Column(String(50), nullable=False)  # 'csv' or 'akahu'
    source_account = Column(String(255), nullable=True)
    source_transaction_id = Column(String(255), nullable=True)  # Akahu transaction ID
    
    # YNAB information
    ynab_budget_id = Column(String(255), nullable=True)
    ynab_account_id = Column(String(255), nullable=True)
    ynab_transaction_id = Column(String(255), nullable=True)
    
    # Metadata
    imported_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class MappingProfile(Base):
    """Store CSV column mapping profiles."""
    __tablename__ = "mapping_profiles"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    
    # Column mappings as JSON
    # Example: {"date": "Transaction Date", "amount": "Amount", "payee": "Description"}
    column_mappings = Column(JSON, nullable=False)
    
    # Date format string (e.g., "%d/%m/%Y")
    date_format = Column(String(50), default="%d/%m/%Y")
    
    # Whether amount uses negative for debits
    amount_inverted = Column(Boolean, default=False)
    
    # Skip header rows
    skip_rows = Column(Integer, default=0)
    
    # Default YNAB account for this profile
    default_ynab_account_id = Column(String(255), nullable=True)
    
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AkahuAccount(Base):
    """Store linked Akahu account information."""
    __tablename__ = "akahu_accounts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    akahu_account_id = Column(String(255), unique=True, nullable=False)
    account_name = Column(String(255), nullable=True)
    account_type = Column(String(50), nullable=True)
    institution = Column(String(100), nullable=True)
    
    # Link to YNAB account
    ynab_budget_id = Column(String(255), nullable=True)
    ynab_account_id = Column(String(255), nullable=True)
    
    # Sync settings
    auto_sync = Column(Boolean, default=False)
    last_synced_at = Column(DateTime, nullable=True)
    
    # Schedule settings
    schedule_enabled = Column(Boolean, default=False)
    schedule_interval_hours = Column(Integer, default=6)  # 1, 2, 4, 6, 12, or 24
    schedule_days_to_sync = Column(Integer, default=7)  # How many days back to sync
    next_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(50), nullable=True)  # 'success', 'failed', 'running'
    last_sync_message = Column(Text, nullable=True)
    last_sync_imported = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SyncLog(Base):
    """Log of sync operations for history tracking."""
    __tablename__ = "sync_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    akahu_account_id = Column(String(255), nullable=False, index=True)
    
    # Sync details
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String(50), nullable=False)  # 'success', 'failed', 'running'
    
    # Results
    transactions_found = Column(Integer, default=0)
    transactions_imported = Column(Integer, default=0)
    transactions_skipped = Column(Integer, default=0)  # duplicates
    ynab_duplicates = Column(Integer, default=0)
    
    # Error info
    error_message = Column(Text, nullable=True)
    
    # Trigger type
    trigger = Column(String(50), default='manual')  # 'manual', 'scheduled'


# Database setup functions
async def get_engine(database_url: str):
    return create_async_engine(database_url, echo=False)


async def get_session_maker(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(database_url: str):
    engine = await get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine
