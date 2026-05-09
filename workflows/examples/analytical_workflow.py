"""Example cross-instrument analytical workflow skeleton."""

from __future__ import annotations

from typing import Any, Dict, List

from prefect import flow, get_run_logger, task


@task
def transfer_plate(source: str, destination: str, plate_id: str) -> Dict[str, str]:
    return {"source": source, "destination": destination, "plate_id": plate_id, "status": "transferred"}


@task
def collect_results(instrument_id: str, plate_id: str) -> Dict[str, Any]:
    return {"instrument_id": instrument_id, "plate_id": plate_id, "results": {}}


@flow
def analytical_workflow(
    *,
    plate_ids: List[str],
    prep_device: str = "ot2",
    analysis_device: str = "plate_reader",
) -> Dict[str, Any]:
    logger = get_run_logger()
    logger.info("Starting analytical workflow for %d plates", len(plate_ids))
    transfers = [transfer_plate(prep_device, analysis_device, plate_id) for plate_id in plate_ids]
    results = [collect_results(analysis_device, plate_id) for plate_id in plate_ids]
    return {"transfers": transfers, "results": results}
