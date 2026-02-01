from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..dependencies import get_db
from ..models.database import MappingProfile
from ..schemas.mapping import MappingProfileCreate, MappingProfileResponse

router = APIRouter(prefix="/mappings", tags=["Mapping Profiles"])


@router.get("/", response_model=List[MappingProfileResponse])
async def list_mapping_profiles(db: AsyncSession = Depends(get_db)):
    """List all saved mapping profiles."""
    result = await db.execute(
        select(MappingProfile).order_by(MappingProfile.name)
    )
    return list(result.scalars().all())


@router.post("/", response_model=MappingProfileResponse)
async def create_mapping_profile(
    profile: MappingProfileCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new mapping profile."""
    # Check if name already exists
    result = await db.execute(
        select(MappingProfile).where(MappingProfile.name == profile.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Profile with name '{profile.name}' already exists"
        )
    
    new_profile = MappingProfile(
        name=profile.name,
        description=profile.description,
        column_mappings=profile.column_mappings,
        date_format=profile.date_format,
        amount_inverted=profile.amount_inverted,
        skip_rows=profile.skip_rows,
        default_ynab_account_id=profile.default_ynab_account_id
    )
    
    db.add(new_profile)
    await db.commit()
    await db.refresh(new_profile)
    
    return new_profile


@router.get("/{profile_id}", response_model=MappingProfileResponse)
async def get_mapping_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific mapping profile."""
    result = await db.execute(
        select(MappingProfile).where(MappingProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    return profile


@router.put("/{profile_id}", response_model=MappingProfileResponse)
async def update_mapping_profile(
    profile_id: int,
    profile_data: MappingProfileCreate,
    db: AsyncSession = Depends(get_db)
):
    """Update a mapping profile."""
    result = await db.execute(
        select(MappingProfile).where(MappingProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    # Check name uniqueness if changed
    if profile_data.name != profile.name:
        name_check = await db.execute(
            select(MappingProfile).where(MappingProfile.name == profile_data.name)
        )
        if name_check.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Profile with name '{profile_data.name}' already exists"
            )
    
    profile.name = profile_data.name
    profile.description = profile_data.description
    profile.column_mappings = profile_data.column_mappings
    profile.date_format = profile_data.date_format
    profile.amount_inverted = profile_data.amount_inverted
    profile.skip_rows = profile_data.skip_rows
    profile.default_ynab_account_id = profile_data.default_ynab_account_id
    
    await db.commit()
    await db.refresh(profile)
    
    return profile


@router.delete("/{profile_id}")
async def delete_mapping_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a mapping profile."""
    result = await db.execute(
        select(MappingProfile).where(MappingProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    await db.delete(profile)
    await db.commit()
    
    return {"status": "success", "message": "Profile deleted"}


@router.post("/{profile_id}/set-default")
async def set_default_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Set a profile as the default."""
    # Clear existing default
    result = await db.execute(
        select(MappingProfile).where(MappingProfile.is_default == True)
    )
    for existing in result.scalars().all():
        existing.is_default = False
    
    # Set new default
    result = await db.execute(
        select(MappingProfile).where(MappingProfile.id == profile_id)
    )
    profile = result.scalar_one_or_none()
    
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    
    profile.is_default = True
    await db.commit()
    
    return {"status": "success", "message": f"'{profile.name}' is now the default profile"}
