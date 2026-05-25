"""Compatibility wrapper for Prefect task helpers.

The implementation lives in the repository-level ``workflows/`` folder. This
module avoids importing Prefect until callers explicitly request the helper.
"""

from __future__ import annotations


def robust_task(**task_kwargs):
    from workflows.prefect_tasks import robust_task as _robust_task

    return _robust_task(**task_kwargs)


__all__ = ["robust_task"]
