from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..services.csv_parser import CSVParser
from ..services.dedup import DeduplicationService
from ..services.ynab_client import YNABClient
from ..schemas.transaction import TransactionPreview, TransactionCreate

router = APIRouter(prefix="/csv", tags=["CSV Import"])


@router.get("/profiles")
async def get_bank_profiles():
    """Get available pre-configured bank profiles."""
    return CSVParser.get_available_profiles()


@router.post("/detect-columns")
async def detect_csv_columns(file: UploadFile = File(...)):
    """Detect columns in an uploaded CSV file."""
    content = await file.read()
    try:
        csv_content = content.decode('utf-8')
    except UnicodeDecodeError:
        csv_content = content.decode('latin-1')
    
    columns = CSVParser.detect_columns(csv_content)
    preview = CSVParser.preview_csv(csv_content, num_rows=5)
    
    return {
        "columns": columns,
        "preview": preview
    }


@router.post("/parse", response_model=List[TransactionPreview])
async def parse_csv(
    file: UploadFile = File(...),
    date_column: str = Form(...),
    amount_column: str = Form(...),
    payee_column: Optional[str] = Form(None),
    memo_column: Optional[str] = Form(None),
    date_format: str = Form("%d/%m/%Y"),
    amount_inverted: bool = Form(False),
    skip_rows: int = Form(0),
    db: AsyncSession = Depends(get_db)
):
    """
    Parse a CSV file and return transaction previews with duplicate detection.
    """
    content = await file.read()
    try:
        csv_content = content.decode('utf-8')
    except UnicodeDecodeError:
        csv_content = content.decode('latin-1')
    
    column_mappings = {
        "date": date_column,
        "amount": amount_column,
        "payee": payee_column,
        "memo": memo_column
    }
    
    parser = CSVParser()
    transactions = parser.parse_csv(
        csv_content=csv_content,
        column_mappings=column_mappings,
        date_format=date_format,
        amount_inverted=amount_inverted,
        skip_rows=skip_rows
    )
    
    # Check for duplicates
    dedup = DeduplicationService(db)
    transactions = await dedup.check_duplicates(transactions)
    
    return transactions


@router.post("/parse-with-profile", response_model=List[TransactionPreview])
async def parse_csv_with_profile(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Parse a CSV file using a pre-configured bank profile.
    """
    profiles = CSVParser.get_available_profiles()
    if profile_id not in profiles:
        raise HTTPException(status_code=400, detail=f"Unknown profile: {profile_id}")
    
    profile = profiles[profile_id]
    
    content = await file.read()
    try:
        csv_content = content.decode('utf-8')
    except UnicodeDecodeError:
        csv_content = content.decode('latin-1')
    
    parser = CSVParser()
    transactions = parser.parse_csv(
        csv_content=csv_content,
        column_mappings=profile["column_mappings"],
        date_format=profile["date_format"],
        amount_inverted=profile["amount_inverted"],
        skip_rows=profile["skip_rows"]
    )
    
    # Check for duplicates
    dedup = DeduplicationService(db)
    transactions = await dedup.check_duplicates(transactions)
    
    return transactions


@router.post("/import")
async def import_csv_transactions(
    transactions: List[dict],
    ynab_budget_id: str,
    ynab_account_id: str,
    skip_duplicates: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """
    Import parsed CSV transactions to YNAB.
    """
    if not transactions:
        raise HTTPException(status_code=400, detail="No transactions to import")
    
    # Convert to TransactionCreate objects
    tx_creates = [
        TransactionCreate(
            date=tx['date'],
            amount=tx['amount'],
            payee=tx.get('payee'),
            memo=tx.get('memo'),
            source="csv"
        )
        for tx in transactions
        if not skip_duplicates or not tx.get('is_duplicate', False)
    ]
    
    if not tx_creates:
        return {
            "imported": 0,
            "skipped_duplicates": len(transactions),
            "message": "All transactions were duplicates"
        }
    
    # Import to YNAB
    ynab = YNABClient()
    tx_dicts = [
        {
            "date": tx.date,
            "amount": tx.amount,
            "payee": tx.payee,
            "memo": tx.memo
        }
        for tx in tx_creates
    ]
    
    result = await ynab.import_transactions(ynab_budget_id, ynab_account_id, tx_dicts)
    
    # Record successful imports
    dedup = DeduplicationService(db)
    await dedup.record_imports_batch(
        tx_creates,
        ynab_budget_id,
        ynab_account_id,
        result.transaction_ids
    )
    
    return {
        "imported": len(result.transaction_ids),
        "ynab_duplicates": len(result.duplicate_import_ids),
        "transaction_ids": result.transaction_ids
    }
