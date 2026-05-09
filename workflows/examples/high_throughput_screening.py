"""Example high-throughput screening flow skeleton."""

from __future__ import annotations

from typing import Any, Dict, List

from prefect import flow, get_run_logger, task


@task
def assign_compounds(robot_id: str, compounds: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"robot_id": robot_id, "compound_count": len(compounds), "compounds": compounds}


@flow
def high_throughput_screening_flow(
    *,
    robot_ids: List[str],
    compound_library: List[Dict[str, Any]],
) -> Dict[str, Any]:
    logger = get_run_logger()
    logger.info("Planning HTS run across %d robots", len(robot_ids))
    if not robot_ids:
        raise ValueError("At least one robot is required")

    assignments = []
    for index, robot_id in enumerate(robot_ids):
        assigned = compound_library[index:: len(robot_ids)]
        assignments.append(assign_compounds(robot_id, assigned))
    return {"assignments": assignments}
