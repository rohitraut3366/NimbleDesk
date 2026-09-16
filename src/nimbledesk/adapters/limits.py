from __future__ import annotations

import math
import os
import sys
from importlib import import_module
from typing import Any

MAXIMUM_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
MAXIMUM_WORKER_FILE_BYTES = 64 * 1024 * 1024
MAXIMUM_WORKER_OPEN_FILES = 64
MAXIMUM_WORKER_PROCESSES = 1


def apply_worker_limits(timeout_seconds: float) -> None:
    """Lower irreversible resource ceilings before third-party code is imported."""
    if os.name != "posix":
        return
    resource: Any = import_module("resource")
    limits = [
        (resource.RLIMIT_CPU, max(1, math.ceil(timeout_seconds) + 2)),
        (resource.RLIMIT_FSIZE, MAXIMUM_WORKER_FILE_BYTES),
        (resource.RLIMIT_NOFILE, MAXIMUM_WORKER_OPEN_FILES),
        (resource.RLIMIT_NPROC, MAXIMUM_WORKER_PROCESSES),
    ]
    if sys.platform != "darwin":
        limits.append((resource.RLIMIT_AS, MAXIMUM_WORKER_MEMORY_BYTES))
    for kind, ceiling in limits:
        _lower_limit(resource, kind, ceiling)


def _lower_limit(resource: Any, kind: int, ceiling: int) -> None:
    soft, hard = resource.getrlimit(kind)
    infinity = resource.RLIM_INFINITY
    hard_limit = ceiling if hard == infinity else min(hard, ceiling)
    soft_limit = hard_limit if soft == infinity else min(soft, hard_limit)
    resource.setrlimit(kind, (soft_limit, hard_limit))
