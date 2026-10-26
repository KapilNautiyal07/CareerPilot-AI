"""Import every model so Base.metadata is complete (Alembic autogenerate, tests)."""

from app.models.interview import AssessmentResult, InterviewQuestion, InterviewResponse, InterviewSession
from app.models.job import JobApplication, JobListing, JobMatch, JobSkillRequirement
from app.models.learning import LearningResource, LearningRoadmap, RoadmapTask, SkillGap, roadmap_task_resources
from app.models.resume import ExtractedSkill, ProjectExperience, Resume, WorkExperience
from app.models.user import CandidateProfile, User, UserPreference
from app.models.workflow import WorkflowExecution

__all__ = [
    "AssessmentResult",
    "CandidateProfile",
    "ExtractedSkill",
    "InterviewQuestion",
    "InterviewResponse",
    "InterviewSession",
    "JobApplication",
    "JobListing",
    "JobMatch",
    "JobSkillRequirement",
    "LearningResource",
    "LearningRoadmap",
    "ProjectExperience",
    "Resume",
    "RoadmapTask",
    "SkillGap",
    "User",
    "UserPreference",
    "WorkExperience",
    "WorkflowExecution",
    "roadmap_task_resources",
]
