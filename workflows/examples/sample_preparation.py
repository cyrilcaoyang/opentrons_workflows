"""Example Prefect flow for OT-2 sample preparation."""

from __future__ import annotations

from typing import Any, Dict, List

from prefect import flow, get_run_logger, task

from opentrons_workflows.control import OT2Control


@task
def run_liquid_handling_step(robot: OT2Control, operation: Dict[str, Any]) -> Dict[str, Any]:
    op_type = operation["type"]
    pipette = operation.get("pipette")
    if op_type in {"pick_up_tip", "aspirate", "dispense"}:
        robot.get_location_from_labware(
            operation["labware"],
            operation["position"],
            top=operation.get("top", 0),
            bottom=operation.get("bottom", 0),
            center=operation.get("center", 0),
        )
    if op_type == "pick_up_tip":
        robot.pick_up_tip(pipette)
    elif op_type == "aspirate":
        robot.aspirate(pipette, operation["volume_ul"])
    elif op_type == "dispense":
        robot.dispense(pipette, operation["volume_ul"])
    elif op_type == "drop_tip":
        robot.drop_tip(pipette)
    else:
        raise ValueError(f"Unsupported operation type: {op_type}")
    return {"type": op_type, "status": "complete"}


@flow
def sample_preparation_flow(
    *,
    host_alias: str,
    labware: List[Dict[str, Any]],
    instruments: List[Dict[str, Any]],
    operations: List[Dict[str, Any]],
    simulation: bool = False,
) -> Dict[str, Any]:
    logger = get_run_logger()
    logger.info("Starting OT-2 sample preparation")
    robot = OT2Control(host_alias=host_alias, simulation=simulation)
    try:
        robot.setup_protocol(labware=labware, instruments=instruments)
        results = [run_liquid_handling_step(robot, operation) for operation in operations]
        return {"status": "complete", "operations": results}
    finally:
        robot.shutdown()
