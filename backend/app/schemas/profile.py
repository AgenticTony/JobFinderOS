"""Profile schemas for JobFinderOS."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.common import parse_json_list


class OnboardingRequest(BaseModel):
    """The onboarding wizard's final payload."""
    country: str  # ISO code: SE, GB
    region: Optional[str] = None
    municipality: Optional[str] = None
    remote_only: bool = False
    search_queries: List[str] = []
    languages: List[str] = []


class ProfilePreferencesUpdate(BaseModel):
    """User-editable preferences (not AI-extracted)."""
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    preferred_roles: Optional[List[str]] = None
    preferred_locations: Optional[str] = None
    remote_ok: Optional[bool] = None
    min_salary: Optional[str] = None
    exclude_keywords: Optional[List[str]] = None


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    professional_title: Optional[str] = None
    cv_file_name: Optional[str] = None
    experience_years: Optional[int] = None
    ai_summary: Optional[str] = None
    preferred_roles: List[str] = []
    preferred_locations: Optional[str] = None
    remote_ok: bool = True
    min_salary: Optional[str] = None
    exclude_keywords: List[str] = []
    onboarded: bool = False
    country: Optional[str] = None
    region: Optional[str] = None
    municipality: Optional[str] = None
    remote_only: bool = False
    search_queries: List[str] = []
    languages: List[str] = []
    created_at: datetime
    updated_at: datetime

    # AI-extracted JSON lists (parsed from Text columns)
    skills: List[dict] = []
    recent_roles: List[dict] = []
    education: List[dict] = []
    certifications: List[str] = []
    keywords: List[str] = []

    @classmethod
    def from_orm_profile(cls, profile) -> "ProfileResponse":
        return cls(
            id=profile.id,
            full_name=profile.full_name,
            email=profile.email,
            phone=profile.phone,
            location=profile.location,
            professional_title=profile.professional_title,
            cv_file_name=profile.cv_file_name,
            experience_years=profile.experience_years,
            ai_summary=profile.ai_summary,
            preferred_roles=parse_json_list(profile.preferred_roles),
            preferred_locations=profile.preferred_locations,
            remote_ok=bool(profile.remote_ok),
            min_salary=profile.min_salary,
            exclude_keywords=parse_json_list(profile.exclude_keywords),
            onboarded=bool(profile.onboarded),
            country=profile.country,
            region=profile.region,
            municipality=profile.municipality,
            remote_only=bool(profile.remote_only),
            search_queries=parse_json_list(profile.search_queries),
            languages=parse_json_list(profile.languages),
            created_at=profile.created_at,
            updated_at=profile.updated_at,
            skills=parse_json_list(profile.skills),
            recent_roles=parse_json_list(profile.recent_roles),
            education=parse_json_list(profile.education),
            certifications=parse_json_list(profile.certifications),
            keywords=parse_json_list(profile.keywords),
        )
