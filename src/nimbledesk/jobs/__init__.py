"""Persistent creative job models and execution service."""

from nimbledesk.jobs.service import (
    JOB_SERVICE,
    CreateJobRequest,
    JobRecord,
    JobService,
    PersistedJob,
    PhotoJobRequest,
    ReviseJobRequest,
    RevisionResult,
    RevisionSubmission,
    VariantSelectionRequest,
    artifact_paths,
)

__all__ = [
    "CreateJobRequest",
    "JOB_SERVICE",
    "JobRecord",
    "JobService",
    "PersistedJob",
    "PhotoJobRequest",
    "ReviseJobRequest",
    "RevisionResult",
    "RevisionSubmission",
    "VariantSelectionRequest",
    "artifact_paths",
]
