"""Persistent creative job models and execution service."""

from nimbledesk.jobs.service import (
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
