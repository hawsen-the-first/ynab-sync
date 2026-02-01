from datetime import datetime
from typing import Optional, Dict
from pydantic import BaseModel


class ColumnMapping(BaseModel):
    """Mapping of CSV columns to transaction fields."""
    date: str
    amount: str
    payee: Optional[str] = None
    memo: Optional[str] = None


class MappingProfileBase(BaseModel):
    name: str
    description: Optional[str] = None
    column_mappings: Dict[str, str]
    date_format: str = "%d/%m/%Y"
    amount_inverted: bool = False
    skip_rows: int = 0
    default_ynab_account_id: Optional[str] = None


class MappingProfileCreate(MappingProfileBase):
    pass


class MappingProfileResponse(MappingProfileBase):
    id: int
    is_default: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
