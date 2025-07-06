"""
prefect_tasks.py

A factory for creating robust, observable Opentrons tasks with Prefect.

This module provides a custom decorator, `@robust_task`, which is designed
to wrap functions that call methods on a connected robot object. It provides
standardized logging and error handling for use within Prefect flows.

---
How to Use
---
In your protocol file, import the decorator and create your own tasks.

Example:
```python
from prefect import flow
from opentrons_workflows import OpentronsControl
from opentrons_workflows.prefect_tasks import robust_task

@robust_task(retries=2)
def pick_up_tip(pipette):
    pipette.pick_up_tip()

@flow
def my_protocol():
    with connect(host_alias="ot2_sim", simulation=True) as ot:
        p1000 = ot.load_pipette('p1000_single_flex', 'left')
        pick_up_tip(p1000)
```

---
Guidance on Retries
---
The `retries` parameter is powerful but should be used carefully.

**GOOD candidates for retries:**
- `pick_up_tip`, `drop_tip`
- These actions are generally *idempotent* (safe to repeat) and can fail due
  to transient physical issues. Retrying is often the correct response.

**POOR candidates for retries:**
- `aspirate`, `dispense`
- These actions are *non-idempotent*. If a dispense task appears to fail but the
  liquid was actually dispensed, a retry would corrupt the experiment. These tasks
  should fail immediately to allow for manual inspection.
"""

import functools
from prefect import task, get_run_logger

def robust_task(**task_kwargs):
    """
    A custom decorator factory for creating robust Prefect tasks for robot operations.

    This decorator wraps a function with standardized logging and error handling,
    then applies the `@prefect.task` decorator. It simplifies task creation by
    automating the following:
    - Logging the start and successful completion of the task.
    - Catching and logging `RuntimeError` from the robot with specific details.
    - Re-raising errors to allow Prefect to manage failure states and retries.

    Args:
        **task_kwargs: Keyword arguments (e.g., `retries=2`, `name="my-task"`)
                       that are passed directly to the `prefect.task` decorator.
    """
    def decorator(func):
        @task(**task_kwargs)
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_run_logger()
            task_name = func.__name__
            logger.info(f"Executing: {task_name}...")
            try:
                result = func(*args, **kwargs)
                logger.info(f"Success: {task_name} completed.")
                return result
            except RuntimeError as e:
                logger.error(f"Failure in {task_name}: Robot reported an error.")
                # The full traceback from the robot is included in the exception message
                logger.error(f"  Details: {e}")
                raise  # Re-raise for Prefect
            except Exception as e:
                logger.error(f"Failure in {task_name}: An unexpected error occurred.", exc_info=True)
                raise
        return wrapper
    return decorator 