import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from io import StringIO
import pandas as pd

from ..schemas.transaction import TransactionPreview, TransactionCreate


class CSVParser:
    """Parse CSV files and convert to transaction format."""
    
    # Pre-configured bank profiles for common NZ banks
    BANK_PROFILES = {
        "asb": {
            "name": "ASB Bank",
            "column_mappings": {
                "date": "Date",
                "amount": "Amount",
                "payee": "Payee",
                "memo": "Memo"
            },
            "date_format": "%d/%m/%Y",
            "amount_inverted": False,
            "skip_rows": 0
        },
        "anz": {
            "name": "ANZ Bank",
            "column_mappings": {
                "date": "Date",
                "amount": "Amount",
                "payee": "Description",
                "memo": "Reference"
            },
            "date_format": "%d/%m/%Y",
            "amount_inverted": False,
            "skip_rows": 0
        },
        "westpac": {
            "name": "Westpac",
            "column_mappings": {
                "date": "Date",
                "amount": "Amount",
                "payee": "Other Party",
                "memo": "Particulars"
            },
            "date_format": "%d/%m/%Y",
            "amount_inverted": False,
            "skip_rows": 0
        },
        "bnz": {
            "name": "BNZ",
            "column_mappings": {
                "date": "Date",
                "amount": "Amount",
                "payee": "Payee",
                "memo": "Particulars"
            },
            "date_format": "%d/%m/%Y",
            "amount_inverted": False,
            "skip_rows": 0
        },
        "kiwibank": {
            "name": "Kiwibank",
            "column_mappings": {
                "date": "Date",
                "amount": "Amount",
                "payee": "Description",
                "memo": "Reference"
            },
            "date_format": "%d/%m/%Y",
            "amount_inverted": False,
            "skip_rows": 0
        }
    }
    
    @staticmethod
    def generate_transaction_hash(date: datetime, amount: float, payee: Optional[str], memo: Optional[str] = None) -> str:
        """Generate a unique hash for a transaction."""
        hash_input = f"{date.isoformat()}:{amount}:{payee or ''}:{memo or ''}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:32]
    
    @staticmethod
    def get_available_profiles() -> Dict[str, Dict]:
        """Get all available bank profiles."""
        return CSVParser.BANK_PROFILES
    
    @staticmethod
    def detect_columns(csv_content: str) -> List[str]:
        """Detect columns in a CSV file."""
        df = pd.read_csv(StringIO(csv_content), nrows=0)
        return list(df.columns)
    
    @staticmethod
    def preview_csv(csv_content: str, num_rows: int = 5) -> List[Dict[str, Any]]:
        """Preview the first few rows of a CSV file."""
        df = pd.read_csv(StringIO(csv_content), nrows=num_rows)
        return df.to_dict(orient='records')
    
    def parse_csv(
        self,
        csv_content: str,
        column_mappings: Dict[str, str],
        date_format: str = "%d/%m/%Y",
        amount_inverted: bool = False,
        skip_rows: int = 0,
        source_account: Optional[str] = None
    ) -> List[TransactionPreview]:
        """
        Parse CSV content and return transaction previews.
        
        Args:
            csv_content: Raw CSV string
            column_mappings: Dict mapping transaction fields to CSV columns
                           e.g., {"date": "Transaction Date", "amount": "Amount"}
            date_format: strptime format string for parsing dates
            amount_inverted: If True, multiply amounts by -1
            skip_rows: Number of rows to skip at the start
            source_account: Optional account identifier
        
        Returns:
            List of TransactionPreview objects
        """
        # Read CSV
        df = pd.read_csv(StringIO(csv_content), skiprows=skip_rows)
        
        transactions = []
        for _, row in df.iterrows():
            try:
                # Parse date
                date_str = str(row[column_mappings['date']]).strip()
                date = datetime.strptime(date_str, date_format)
                
                # Parse amount
                amount_str = str(row[column_mappings['amount']]).strip()
                # Remove currency symbols and commas
                amount_str = amount_str.replace('$', '').replace(',', '').replace(' ', '')
                amount = float(amount_str)
                
                if amount_inverted:
                    amount = -amount
                
                # Parse optional fields
                payee = None
                if 'payee' in column_mappings and column_mappings['payee']:
                    payee_val = row.get(column_mappings['payee'])
                    if pd.notna(payee_val):
                        payee = str(payee_val).strip()
                
                memo = None
                if 'memo' in column_mappings and column_mappings['memo']:
                    memo_val = row.get(column_mappings['memo'])
                    if pd.notna(memo_val):
                        memo = str(memo_val).strip()
                
                # Generate hash
                tx_hash = self.generate_transaction_hash(date, amount, payee, memo)
                
                transactions.append(TransactionPreview(
                    date=date,
                    amount=amount,
                    payee=payee,
                    memo=memo,
                    is_duplicate=False,  # Will be updated by dedup service
                    transaction_hash=tx_hash,
                    raw_data=row.to_dict()
                ))
                
            except Exception as e:
                # Log error but continue processing
                print(f"Error parsing row: {e}")
                continue
        
        return transactions
    
    def to_transaction_creates(
        self,
        previews: List[TransactionPreview],
        source_account: Optional[str] = None
    ) -> List[TransactionCreate]:
        """Convert previews to TransactionCreate objects for import."""
        return [
            TransactionCreate(
                date=p.date,
                amount=p.amount,
                payee=p.payee,
                memo=p.memo,
                source="csv",
                source_account=source_account
            )
            for p in previews
            if not p.is_duplicate
        ]
