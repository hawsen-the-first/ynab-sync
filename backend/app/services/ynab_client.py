from datetime import date, datetime
from typing import List, Optional, Tuple
import httpx

from ..config import get_settings
from ..schemas.ynab import YNABBudget, YNABAccount, YNABTransactionCreate, YNABImportResult


class YNABClient:
    """Client for YNAB API interactions."""
    
    BASE_URL = "https://api.ynab.com/v1"
    
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or get_settings().ynab_access_token
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    async def _request(self, method: str, endpoint: str, json_data: dict = None) -> dict:
        """Make an authenticated request to YNAB API."""
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.BASE_URL}{endpoint}",
                headers=self.headers,
                json=json_data,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
    
    async def get_budgets(self) -> List[YNABBudget]:
        """Get all budgets for the authenticated user."""
        data = await self._request("GET", "/budgets")
        budgets = data.get("data", {}).get("budgets", [])
        return [
            YNABBudget(id=b["id"], name=b["name"])
            for b in budgets
        ]
    
    async def get_accounts(self, budget_id: str) -> List[YNABAccount]:
        """Get all accounts for a budget."""
        data = await self._request("GET", f"/budgets/{budget_id}/accounts")
        accounts = data.get("data", {}).get("accounts", [])
        return [
            YNABAccount(
                id=a["id"],
                name=a["name"],
                type=a["type"],
                on_budget=a["on_budget"],
                closed=a["closed"],
                balance=a["balance"]
            )
            for a in accounts
            if not a["deleted"]
        ]
    
    @staticmethod
    def dollars_to_milliunits(amount: float) -> int:
        """Convert dollar amount to YNAB milliunits."""
        return int(round(amount * 1000))
    
    @staticmethod
    def milliunits_to_dollars(milliunits: int) -> float:
        """Convert YNAB milliunits to dollar amount."""
        return milliunits / 1000
    
    @staticmethod
    def generate_import_id(date: date, amount: int, occurrence: int = 1) -> str:
        """
        Generate a YNAB import_id for duplicate detection.
        Format: YNAB:amount:date:occurrence
        """
        return f"YNAB:{amount}:{date.isoformat()}:{occurrence}"
    
    async def create_transactions(
        self,
        budget_id: str,
        account_id: str,
        transactions: List[dict]
    ) -> Tuple[List[str], List[str]]:
        """
        Create transactions in YNAB.
        
        Args:
            budget_id: YNAB budget ID
            account_id: YNAB account ID
            transactions: List of transaction dicts with date, amount, payee, memo
        
        Returns:
            Tuple of (created_transaction_ids, duplicate_import_ids)
        """
        # Convert transactions to YNAB format
        ynab_transactions = []
        import_id_counts = {}  # Track occurrences for same amount/date
        
        for tx in transactions:
            # Parse date
            tx_date = tx['date']
            if isinstance(tx_date, datetime):
                tx_date = tx_date.date()
            elif isinstance(tx_date, str):
                tx_date = datetime.fromisoformat(tx_date).date()
            
            # Convert amount to milliunits
            amount_milliunits = self.dollars_to_milliunits(tx['amount'])
            
            # Generate import_id with occurrence tracking
            base_key = f"{amount_milliunits}:{tx_date.isoformat()}"
            occurrence = import_id_counts.get(base_key, 0) + 1
            import_id_counts[base_key] = occurrence
            
            import_id = self.generate_import_id(tx_date, amount_milliunits, occurrence)
            
            ynab_tx = {
                "account_id": account_id,
                "date": tx_date.isoformat(),
                "amount": amount_milliunits,
                "payee_name": tx.get('payee'),
                "memo": tx.get('memo'),
                "cleared": "cleared",
                "import_id": import_id
            }
            ynab_transactions.append(ynab_tx)
        
        # Send to YNAB
        data = await self._request(
            "POST",
            f"/budgets/{budget_id}/transactions",
            json_data={"transactions": ynab_transactions}
        )
        
        result = data.get("data", {})
        
        # Extract created and duplicate IDs
        created_ids = [tx["id"] for tx in result.get("transactions", [])]
        duplicate_ids = result.get("duplicate_import_ids", [])
        
        return created_ids, duplicate_ids
    
    async def import_transactions(
        self,
        budget_id: str,
        account_id: str,
        transactions: List[dict]
    ) -> YNABImportResult:
        """
        Import transactions to YNAB with proper handling.
        
        Args:
            budget_id: YNAB budget ID
            account_id: YNAB account ID  
            transactions: List of transaction dicts
        
        Returns:
            YNABImportResult with created and duplicate counts
        """
        if not transactions:
            return YNABImportResult(transaction_ids=[], duplicate_import_ids=[])
        
        created_ids, duplicate_ids = await self.create_transactions(
            budget_id, account_id, transactions
        )
        
        return YNABImportResult(
            transaction_ids=created_ids,
            duplicate_import_ids=duplicate_ids
        )
    
    async def test_connection(self) -> bool:
        """Test if the YNAB connection is working."""
        try:
            await self.get_budgets()
            return True
        except Exception:
            return False
