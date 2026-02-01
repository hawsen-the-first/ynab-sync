from datetime import datetime, timedelta
from typing import List, Optional
import httpx

from ..config import get_settings
from ..schemas.akahu import AkahuAccountResponse, AkahuTransaction


class AkahuClient:
    """Client for Akahu API interactions."""
    
    BASE_URL = "https://api.akahu.io/v1"
    
    def __init__(self, app_token: Optional[str] = None, user_token: Optional[str] = None):
        settings = get_settings()
        self.app_token = app_token or settings.akahu_app_token
        self.user_token = user_token or settings.akahu_user_token
        self.headers = {
            "Authorization": f"Bearer {self.user_token}",
            "X-Akahu-Id": self.app_token,
            "Content-Type": "application/json"
        }
    
    async def _request(self, method: str, endpoint: str, params: dict = None) -> dict:
        """Make an authenticated request to Akahu API."""
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.BASE_URL}{endpoint}",
                headers=self.headers,
                params=params,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
    
    async def get_accounts(self) -> List[AkahuAccountResponse]:
        """Get all connected bank accounts."""
        data = await self._request("GET", "/accounts")
        accounts = data.get("items", [])
        
        return [
            AkahuAccountResponse(
                id=acc["_id"],
                name=acc.get("name", "Unknown Account"),
                type=acc.get("type", "unknown"),
                institution=acc.get("connection", {}).get("name", "Unknown"),
                balance=acc.get("balance", {}).get("current")
            )
            for acc in accounts
        ]
    
    async def get_transactions(
        self,
        account_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[AkahuTransaction]:
        """
        Get transactions from Akahu.
        
        Args:
            account_id: Optional account ID to filter by
            start_date: Start date for transactions (default: 30 days ago)
            end_date: End date for transactions (default: today)
        
        Returns:
            List of AkahuTransaction objects
        """
        # Default date range: last 30 days
        if not start_date:
            start_date = datetime.now() - timedelta(days=30)
        if not end_date:
            end_date = datetime.now()
        
        params = {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d")
        }
        
        all_transactions = []
        cursor = None
        
        # Paginate through all transactions
        while True:
            if cursor:
                params["cursor"] = cursor
            
            data = await self._request("GET", "/transactions", params=params)
            items = data.get("items", [])
            
            for tx in items:
                # Filter by account if specified
                if account_id and tx.get("_account") != account_id:
                    continue
                
                all_transactions.append(AkahuTransaction(
                    id=tx["_id"],
                    account_id=tx["_account"],
                    date=datetime.fromisoformat(tx["date"].replace("Z", "+00:00")),
                    amount=tx["amount"],
                    description=tx.get("description", ""),
                    merchant=tx.get("merchant", {}).get("name"),
                    category=tx.get("category", {}).get("name")
                ))
            
            # Check for more pages
            cursor = data.get("cursor", {}).get("next")
            if not cursor:
                break
        
        return all_transactions
    
    async def get_account_transactions(
        self,
        account_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[AkahuTransaction]:
        """Get transactions for a specific account."""
        return await self.get_transactions(
            account_id=account_id,
            start_date=start_date,
            end_date=end_date
        )
    
    def transactions_to_ynab_format(
        self,
        transactions: List[AkahuTransaction]
    ) -> List[dict]:
        """
        Convert Akahu transactions to YNAB import format.
        
        Returns list of dicts with: date, amount, payee, memo, source_transaction_id
        """
        return [
            {
                "date": tx.date,
                "amount": tx.amount,
                "payee": tx.merchant or tx.description[:50] if tx.description else None,
                "memo": tx.description,
                "source_transaction_id": tx.id
            }
            for tx in transactions
        ]
    
    async def test_connection(self) -> bool:
        """Test if the Akahu connection is working."""
        try:
            await self.get_accounts()
            return True
        except Exception:
            return False
