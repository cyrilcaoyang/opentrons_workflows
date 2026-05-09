"""Reusable Prefect task helpers for workflow code.

This module intentionally lives outside ``src/opentrons_workflows`` so the core
device package and gateway can import without requiring workflow orchestration.
"""

import functools

from prefect import get_run_logger, task


def robust_task(**task_kwargs):
    """Wrap a robot operation as a Prefect task with standard logging."""

    def decorator(func):
        @task(**task_kwargs)
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_run_logger()
            task_name = func.__name__
            logger.info("Executing: %s", task_name)
            try:
                result = func(*args, **kwargs)
                logger.info("Success: %s completed", task_name)
                return result
            except RuntimeError as exc:
                logger.error("Failure in %s: robot reported an error", task_name)
                logger.error("Details: %s", exc)
                raise
            except Exception:
                logger.error("Failure in %s: unexpected error", task_name, exc_info=True)
                raise

        return wrapper

    return decorator
