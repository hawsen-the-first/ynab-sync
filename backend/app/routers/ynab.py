from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db
from ..services.ynab_client import YNABClient
from ..services.dedup import DeduplicationService
from ..schemas.ynab import YNABBudget, YNABAccount

router = APIRouter(prefix="/ynab", tags=["YNAB"])


@router.get("/test")
async def test_ynab_connection():
    """Test the YNAB API connection."""
    ynab = YNABClient()
    connected = await ynab.test_connection()
    
    if connected:
        return {"status": "connected", "message": "Successfully connected to YNAB"}
    else:
        raise HTTPException(
            status_code=401,
            detail="Failed to connect to YNAB. Check your access token."
        )


@router.get("/budgets", response_model=List[YNABBudget])
async def get_budgets():
    """Get all YNAB budgets."""
    ynab = YNABClient()
    try:
        budgets = await ynab.get_budgets()
        return budgets
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/budgets/{budget_id}/accounts", response_model=List[YNABAccount])
async def get_accounts(budget_id: str):
    """Get all accounts for a YNAB budget."""
    ynab = YNABClient()
    try:
        accounts = await ynab.get_accounts(budget_id)
        return accounts
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_import_history(
    limit: int = 100,
    source: str = None,
    db: AsyncSession = Depends(get_db)
):
    """Get import history."""
    dedup = DeduplicationService(db)
    history = await dedup.get_import_history(limit=limit, source=source)
    
    return [
        {
            "id": h.id,
            "date": h.date.isoformat(),
            "amount": h.amount,
            "payee": h.payee,
            "memo": h.memo,
            "source": h.source,
            "imported_at": h.imported_at.isoformat() if h.imported_at else None,
            "ynab_transaction_id": h.ynab_transaction_id
        }
        for h in history
    ]


@router.get("/stats")
async def get_import_stats(db: AsyncSession = Depends(get_db)):
    """Get import statistics."""
    dedup = DeduplicationService(db)
    return await dedup.get_import_stats()
