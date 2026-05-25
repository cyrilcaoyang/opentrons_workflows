"""Compatibility shims for workflow examples.

Prefect workflows now live in the repository-level ``workflows/`` folder so the
core package can import and the gateway can start without pulling in workflow
orchestration. Import the example flows directly from ``workflows.examples`` in
new code.
"""

from __future__ import annotations


def sample_preparation_workflow(*args, **kwargs):
    from workflows.examples.sample_preparation import sample_preparation_flow

    return sample_preparation_flow(*args, **kwargs)


def analytical_workflow(*args, **kwargs):
    from workflows.examples.analytical_workflow import analytical_workflow as _flow

    return _flow(*args, **kwargs)


def high_throughput_screening_workflow(*args, **kwargs):
    from workflows.examples.high_throughput_screening import high_throughput_screening_flow

    return high_throughput_screening_flow(*args, **kwargs)


def register_ot2_robot(*args, **kwargs):
    raise RuntimeError(
        "register_ot2_robot was removed from the core runtime. Use the OT-2 "
        "gateway or construct OT2Control inside a workflow instead."
    )


def register_instrument(*args, **kwargs):
    raise RuntimeError(
        "register_instrument was removed from the core runtime. Use lab-skills "
        "or workflow-local clients for cross-instrument orchestration."
    )
