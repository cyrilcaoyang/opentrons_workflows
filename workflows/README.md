# Workflow Examples

This folder contains Prefect-based experiment orchestration examples. The core
`opentrons_workflows` package should not depend on these files to import or to
start the OT-2 gateway.

Use these examples as workflow-layer code that calls the device gateway or the
high-level `OT2Control` wrapper. Keep hardware status and `/control/*` endpoint
logic in `src/opentrons_workflows/gateway/`.
