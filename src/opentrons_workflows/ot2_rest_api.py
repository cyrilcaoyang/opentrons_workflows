"""Backward-compatible entry point for the OT-2 FastAPI gateway.

The implementation moved to ``opentrons_workflows.gateway.api`` so the gateway
can follow the AC equipment status contract and stay separate from Prefect
workflow orchestration.
"""

from .gateway.api import app, create_app

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)