"""Candidate profile, preferences, onboarding and user corrections to extracted data."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_profile
from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.session import get_db
from app.models import CandidateProfile, ExtractedSkill, WorkExperience
from app.models.enums import EvidenceLevel, SkillSource
from app.repositories.career import get_or_create_preference
from app.schemas.api import (
    ExperienceOut,
    ExperiencePatch,
    OnboardingIn,
    PreferencesIn,
    PreferencesOut,
    ProfileIn,
    ProfileOut,
    SkillIn,
    SkillOut,
    SkillPatch,
)
from app.services import skills as tax

router = APIRouter(prefix="/profile", tags=["profile"])


def profile_out(db: Session, profile_id: int) -> ProfileOut:
    p = db.scalar(
        select(CandidateProfile)
        .options(
            selectinload(CandidateProfile.skills),
            selectinload(CandidateProfile.experiences),
            selectinload(CandidateProfile.projects),
        )
        .where(CandidateProfile.id == profile_id)
        .execution_options(populate_existing=True)
    )
    out = ProfileOut.model_validate(p)
    out.skills.sort(key=lambda s: (s.source == "inferred", s.skill))
    out.preferences = PreferencesOut.model_validate(get_or_create_preference(db, p.user_id))
    return out


@router.get("", response_model=ProfileOut)
def get_profile(profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)) -> ProfileOut:
    return profile_out(db, profile.id)


@router.put("", response_model=ProfileOut)
def update_profile(
    body: ProfileIn, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> ProfileOut:
    for k, v in body.model_dump().items():
        setattr(profile, k, v)
    db.commit()
    return profile_out(db, profile.id)


@router.put("/preferences", response_model=PreferencesOut)
def update_preferences(
    body: PreferencesIn, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> PreferencesOut:
    pref = get_or_create_preference(db, profile.user_id)
    data = body.model_dump()
    data["work_arrangements"] = [a.value for a in body.work_arrangements]
    data["interview_categories"] = [c.value for c in body.interview_categories]
    data["preferred_locations"] = [loc.strip()[:120] for loc in body.preferred_locations if loc.strip()]
    for k, v in data.items():
        setattr(pref, k, v)
    db.commit()
    return PreferencesOut.model_validate(pref)


@router.post("/onboarding", response_model=ProfileOut)
def complete_onboarding(
    body: OnboardingIn, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> ProfileOut:
    if not body.profile.target_role.strip():
        raise AppError("A target role is required to complete onboarding", code="validation_error")
    update_profile(body.profile, profile, db)
    update_preferences(body.preferences, profile, db)
    profile.onboarding_completed = True
    db.commit()
    return profile_out(db, profile.id)


@router.post("/skills", response_model=SkillOut, status_code=status.HTTP_201_CREATED)
def add_skill(
    body: SkillIn, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> SkillOut:
    canonical = tax.normalize_skill(body.skill)
    if canonical is None:
        suggestions = sorted({m.skill for m in tax.find_skills(body.skill, require_context_for_ambiguous=False)})
        raise AppError(
            "Unknown skill. Use a name from the skill taxonomy.",
            code="unknown_skill",
            details={"suggestions": suggestions},
        )
    existing = db.scalar(
        select(ExtractedSkill).where(ExtractedSkill.profile_id == profile.id, ExtractedSkill.skill == canonical)
    )
    if existing is not None:
        raise ConflictError(f"{canonical} is already on your profile")
    s = ExtractedSkill(
        profile_id=profile.id,
        skill=canonical,
        category=tax.category_of(canonical),
        source=SkillSource.user,
        evidence_level=EvidenceLevel.user_asserted,
        evidence=[body.evidence.strip()] if body.evidence.strip() else ["Added by you"],
        confidence=0.7,
        user_confirmed=True,
    )
    db.add(s)
    db.commit()
    return SkillOut.model_validate(s)


def _owned_skill(db: Session, profile_id: int, skill_id: int) -> ExtractedSkill:
    s = db.get(ExtractedSkill, skill_id)
    if s is None or s.profile_id != profile_id:
        raise NotFoundError("Skill not found")
    return s


@router.patch("/skills/{skill_id}", response_model=SkillOut)
def patch_skill(
    skill_id: int, body: SkillPatch, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> SkillOut:
    s = _owned_skill(db, profile.id, skill_id)
    if body.user_confirmed is not None:
        s.user_confirmed = body.user_confirmed
    if body.evidence_level is not None:
        # Users can downgrade or assert; "demonstrated" requires evidence text.
        if body.evidence_level == "demonstrated" and not (body.evidence or s.evidence):
            raise AppError("Add evidence before marking a skill as demonstrated", code="validation_error")
        s.evidence_level = EvidenceLevel(body.evidence_level)
        s.user_confirmed = True
    if body.evidence is not None:
        s.evidence = [e.strip()[:300] for e in body.evidence if e.strip()]
    db.commit()
    return SkillOut.model_validate(s)


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(
    skill_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> None:
    db.delete(_owned_skill(db, profile.id, skill_id))
    db.commit()


@router.patch("/experiences/{exp_id}", response_model=ExperienceOut)
def patch_experience(
    exp_id: int,
    body: ExperiencePatch,
    profile: CandidateProfile = Depends(current_profile),
    db: Session = Depends(get_db),
) -> ExperienceOut:
    e = db.get(WorkExperience, exp_id)
    if e is None or e.profile_id != profile.id:
        raise NotFoundError("Experience not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(e, k, v)
    db.commit()
    return ExperienceOut.model_validate(e)


@router.delete("/experiences/{exp_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experience(
    exp_id: int, profile: CandidateProfile = Depends(current_profile), db: Session = Depends(get_db)
) -> None:
    e = db.get(WorkExperience, exp_id)
    if e is None or e.profile_id != profile.id:
        raise NotFoundError("Experience not found")
    db.delete(e)
    db.commit()


@router.get("/skill-taxonomy", response_model=list[dict])
def skill_taxonomy() -> list[dict]:
    return [{"name": s.name, "category": s.category} for s in tax.TAXONOMY]
