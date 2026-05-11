# Opentrons Workflows

Python tools for controlling an Opentrons OT-2/Flex over SSH, exposing an
AC-compatible REST gateway, and keeping experiment workflows separate from the
device runtime.

## What This Repo Contains

The project is now split into layers:

```text
opentrons_workflows/
├── src/opentrons_workflows/
│   ├── transport/              # SSH transport/session management
│   │   └── ssh_client.py
│   ├── control/                # OT-2 command wrapper and state readers
│   │   ├── ot2_control.py
│   │   └── state_readers.py
│   ├── gateway/                # FastAPI equipment gateway
│   │   ├── api.py
│   │   ├── service.py
│   │   ├── models.py
│   │   └── claims.py
│   ├── labware/                # Container, well, pipette, and event models
│   │   ├── containers.py
│   │   └── events.py
│   ├── opentrons_control.py    # Backward-compatible import path
│   ├── opentrons_sshclient.py  # Backward-compatible import path
│   ├── opentrons_states.py     # Backward-compatible import path
│   └── ot2_rest_api.py         # Backward-compatible gateway entry point
├── workflows/                  # Prefect examples and helpers
│   ├── examples/
│   ├── prefect_tasks.py
│   └── README.md
├── tests/
│   ├── fixtures/
│   └── unit/
├── demo/                       # Preserved legacy demos
└── backup/                     # Preserved backup/reference code
```

`demo/` and `backup/` are intentionally preserved and are not part of the new
runtime layout.

## Install

For the gateway/core package:

```bash
pip install -e .
```

For API-only deployments:

```bash
pip install -r requirements_api.txt
```

For workflow development with Prefect:

```bash
pip install -e ".[workflows]"
```

For tests/dev tools:

```bash
pip install -e ".[dev]"
```

## Core Concepts

- `transport.SSHClient` owns SSH connection state, shell mode, Python REPL mode,
  retries, batch execution, and closing sessions.
- `control.OT2Control` owns high-level OT-2 verbs such as protocol
  initialization, labware loading, homing, aspirating, dispensing, and moving
  labware.
- `control.state_readers` converts live Opentrons protocol/labware/pipette
  objects into plain dictionaries.
- `gateway.OT2Service` owns the device state machine used by the REST API.
- `gateway.api` exposes the FastAPI app for dashboard/status integration.
- `labware` contains optional stable models for containers, wells, pipette
  state, and append-only events.
- `workflows/` contains Prefect orchestration examples. The core gateway does
  not require Prefect to import or start.

## Basic Python Control

```python
from opentrons_workflows.control import OT2Control

robot = OT2Control(host_alias="ot2_robot", simulation=False)

robot.setup_protocol(
    labware=[
        {
            "nickname": "tips",
            "loadname": "opentrons_96_tiprack_300ul",
            "location": "1",
            "ot_default": True,
        },
        {
            "nickname": "plate",
            "loadname": "corning_96_wellplate_360ul_flat",
            "location": "2",
            "ot_default": True,
        },
    ],
    instruments=[
        {
            "nickname": "p300",
            "instrument_name": "p300_single_gen2",
            "mount": "right",
            "ot_default": True,
        }
    ],
)

robot.get_location_from_labware("tips", "A1")
robot.pick_up_tip("p300")
robot.get_location_from_labware("plate", "A1")
robot.aspirate("p300", 100)
robot.get_location_from_labware("plate", "B1")
robot.dispense("p300", 100)
robot.drop_tip("p300")
robot.shutdown()
```

Legacy imports still work:

```python
from opentrons_workflows import OpentronsControl, SSHClient
```

## SSH Configuration

The SSH client can use either an SSH host alias or explicit environment
variables.

Example `~/.ssh/config`:

```sshconfig
Host ot2_robot
    HostName 192.168.254.50
    User root
    IdentityFile ~/.ssh/ot2_ssh_key
    StrictHostKeyChecking no
```

The AC OT-2 key is expected at `~/.ssh/ot2_ssh_key`. When a control request
passes `host_alias: "ot2_robot"`, the gateway first tries to resolve that alias
from this SSH config block and uses the `IdentityFile` above. If the config file
does not exist, the gateway treats `host_alias` as the hostname and uses
`root` plus `~/.ssh/ot2_ssh_key`.

The AC key is passphrase-protected. Pass the key passphrase in the startup
request body as `password`; do not commit the passphrase to this repository.

On the AC Windows gateway host, the service may run under `systemprofile`.
The SSH client still prefers the `sdl2` profile when it detects that case, so
`~/.ssh/config` and `~/.ssh/ot2_ssh_key` resolve to:

```text
C:\Users\sdl2\.ssh\config
C:\Users\sdl2\.ssh\ot2_ssh_key
```

Override this only if the gateway moves to another account:

```powershell
$env:OT2_SSH_HOME = "C:\Users\sdl2"
$env:OT2_SSH_CONFIG = "C:\Users\sdl2\.ssh\config"
```

Equivalent environment variables:

```bash
export HOSTNAME="192.168.254.50"
export USERNAME="root"
export KEY_FILE_PATH="$HOME/.ssh/ot2_ssh_key"
```

On PowerShell:

```powershell
$env:HOSTNAME = "192.168.254.50"
$env:USERNAME = "root"
$env:KEY_FILE_PATH = "$HOME/.ssh/ot2_ssh_key"
$env:OT2_SSH_COMMAND_TIMEOUT = "120"
```

## REST Gateway

Start the OT-2 gateway:

```bash
uvicorn opentrons_workflows.ot2_rest_api:app --host 0.0.0.0 --port 8020
```

Or with `uv`:

```bash
uv run uvicorn opentrons_workflows.ot2_rest_api:app --host 0.0.0.0 --port 8020
```

The `ot2_rest_api` module is now a compatibility entry point. The implementation
lives in `opentrons_workflows.gateway.api`.

### Required Status Endpoints

The gateway exposes the AC equipment status contract:

- `GET /` - probe response with `equipment_id`, `equipment_name`, and
  `protocol_version`
- `GET /health` - liveness
- `GET /status` - side-effect-free equipment status envelope
- `GET /openapi.json` - generated by FastAPI

Example:

```bash
curl http://127.0.0.1:8020/status
```

Before startup, `/status` should report:

```json
{
  "equipment_id": "ot2",
  "equipment_kind": "liquid_handler",
  "equipment_status": "requires_init",
  "required_actions": ["startup"]
}
```

### Initialising the OT-2

If `/status` reports `equipment_status: "requires_init"` or the AC dashboard
shows **Needs init**, the gateway is running but has not opened an SSH/protocol
session to the OT-2 in this gateway process.

For this gateway, `ready` means `POST /control/startup` completed successfully:

- the gateway created an `OT2Control` instance;
- SSH to the OT-2 is connected;
- a robot-side Python session is running;
- the Opentrons protocol API was imported;
- a protocol context was created with `execute.get_protocol_api('2.21')`
  (`simulate.get_protocol_api('2.21')` when `simulation: true`).

Startup does not load labware, load instruments, or home the robot. Those are
separate `setup` / `home` actions once the gateway is ready.

On the current OT-2, creating the protocol context can take about a minute.
The gateway uses `OT2_SSH_COMMAND_TIMEOUT=120` by default so startup has enough
time to complete.

On the AC HTE deployment, the OT-2 gateway is reachable at:

```text
http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8020
```

Use `Invoke-RestMethod` on Windows when possible; it avoids `curl.exe` JSON
quoting issues.

```powershell
$base = "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8020"

# 1. Check current state.
Invoke-RestMethod "$base/status"

# 2. Claim the OT-2 before sending control commands.
$claim = Invoke-RestMethod `
  -Method Post "$base/control/claim" `
  -ContentType "application/json" `
  -Body (@{
    owner = $env:USERNAME
    session_id = "manual-$([guid]::NewGuid())"
    ttl_s = 60
  } | ConvertTo-Json -Compress)

# 3. Initialise the gateway's OT-2 session. `host_alias` maps to a `Host`
#    entry in $HOME\.ssh\config, which should use ~/.ssh/ot2_ssh_key.
Invoke-RestMethod `
  -Method Post "$base/control/startup" `
  -Headers @{ "X-Claim-Token" = $claim.claim_token } `
  -ContentType "application/json" `
  -Body (@{
    simulation = $false
    host_alias = "192.168.254.50"
    password = "<key-passphrase>"
  } | ConvertTo-Json -Compress)

# 4. Confirm the gateway is ready.
Invoke-RestMethod "$base/status"

# 5. Release the claim when finished.
Invoke-RestMethod `
  -Method Post "$base/control/release" `
  -Headers @{ "X-Claim-Token" = $claim.claim_token }
```

If the OT-2 uses a different SSH `Host` alias, replace `ot2_robot` with that
alias. The alias must point at the OT-2 and use the AC key:

```sshconfig
Host ot2_robot
    HostName <OT2_IP_OR_HOSTNAME>
    User root
    IdentityFile ~/.ssh/ot2_ssh_key
    StrictHostKeyChecking no
```

The equivalent startup body is:

```powershell
Invoke-RestMethod `
  -Method Post "$base/control/startup" `
  -Headers @{ "X-Claim-Token" = $claim.claim_token } `
  -ContentType "application/json" `
  -Body (@{
    simulation = $false
    host_alias = "192.168.254.50"
    password = "<key-passphrase>"
  } | ConvertTo-Json -Compress)
```

PowerShell aliases `curl` and treats quotes differently from bash. If you need
`curl.exe`, build JSON with `ConvertTo-Json` and avoid trailing spaces after
backticks.

```powershell
$base = "http://sdl2-pc-03-cytation.tail6a1dd7.ts.net:8020"

$claimBody = @{
  owner = $env:USERNAME
  session_id = "manual-curl-$([guid]::NewGuid())"
  ttl_s = 60
} | ConvertTo-Json -Compress

$claim = curl.exe -s -X POST "$base/control/claim" `
  -H "Content-Type: application/json" `
  --data-raw $claimBody | ConvertFrom-Json

$startupBody = @{
  simulation = $false
  host_alias = "192.168.254.50"
  password = "<key-passphrase>"
} | ConvertTo-Json -Compress

curl.exe -X POST "$base/control/startup" `
  -H "Content-Type: application/json" `
  -H "X-Claim-Token: $($claim.claim_token)" `
  --data-raw $startupBody

curl.exe "$base/status"

curl.exe -X POST "$base/control/release" `
  -H "X-Claim-Token: $($claim.claim_token)"
```

If `curl.exe` returns a FastAPI `json_invalid` error, the request body reached
the gateway without valid JSON quoting. Re-run the `ConvertTo-Json` version
above, or switch to `Invoke-RestMethod`.

After a successful startup, `/status` may include a `snapshot_failed` warning if
the robot-side Python environment does not have `opentrons_workflows` installed.
That warning is non-blocking: `equipment_status: "ready"` and
`components.protocol.connected: true` are the readiness signals.

## Claim Then Control

Control endpoints use the v1.1 claim protocol. First claim the device:

```bash
curl -X POST http://127.0.0.1:8020/control/claim \
  -H "Content-Type: application/json" \
  -d '{"owner":"sdl2","session_id":"manual-cli","ttl_s":60}'
```

Use the returned `claim_token` on control calls:

```bash
curl -X POST http://127.0.0.1:8020/control/startup \
  -H "Content-Type: application/json" \
  -H "X-Claim-Token: <claim_token>" \
  -d '{"simulation": false}'
```

Heartbeat before the token expires:

```bash
curl -X POST http://127.0.0.1:8020/control/heartbeat \
  -H "X-Claim-Token: <claim_token>"
```

Release when finished:

```bash
curl -X POST http://127.0.0.1:8020/control/release \
  -H "X-Claim-Token: <claim_token>"
```

Useful control endpoints:

- `POST /control/startup`
- `POST /control/shutdown`
- `POST /control/setup`
- `POST /control/home`
- `POST /control/pause`
- `POST /control/resume`
- `POST /control/pick-up-tip`
- `POST /control/aspirate`
- `POST /control/dispense`
- `POST /control/drop-tip`
- `POST /control/move-labware`
- `POST /control/reconcile`

## AC Organic Lab Dashboard Integration

In `ac-organic-lab/equipment.yaml`, configure the OT-2 as an HTTP device:

```yaml
- id: ot2
  name: Opentrons OT-2
  platform: hte
  kind: liquid_handler
  adapter: http
  protocol: "1.1"
  base_url: http://<gateway-host>:8020
  status_path: /status
  poll_timeout_seconds: 2.0
  do_not_call_connect: true
  tile: { w: 2, h: 2 }
```

Use `http://127.0.0.1:8020` only when the dashboard API and this gateway run on
the same host. If the dashboard is on a Linux server and this gateway is on a
Windows/Tailscale host, use the Windows host's Tailscale MagicDNS name or
Tailnet IP.

After changing `equipment.yaml`, restart the dashboard API on the Linux host:

```bash
sudo systemctl restart ac-dashboard-api.service
sudo systemctl status ac-dashboard-api.service --no-pager
```

## SSH Failure Policy

The gateway treats SSH failures differently depending on the operation:

- Read-only or idempotent actions may be retried or restarted.
- Non-idempotent liquid/physical operations are not blindly retried.
- If SSH fails during `aspirate`, `dispense`, `pick_up_tip`, `drop_tip`, or
  `move_labware`, the service enters `unknown_outcome`.
- `unknown_outcome` requires manual inspection/reconciliation before normal
  operation resumes.

The service exposes this through `/status` as `equipment_status: "unknown"` with
details in `last_error` and `details.service_state`.

## State and Labware Tracking

`control.state_readers` extracts live Opentrons state from protocol objects:

- deck slots
- loaded labware
- loaded modules
- mounted instruments
- pipette `has_tip` and `current_volume`
- tip-rack well `has_tip`
- well geometry and reported liquid volume when available

The `labware` package provides stable models for plate/tip-rack identity and
event tracking. These models are intentionally separate from the device gateway:
the gateway can summarize current deck/pipette state, while full sample
provenance should live in workflow state or a future inventory service.

## Workflows

Prefect code now lives outside the runtime package:

```text
workflows/
├── examples/
│   ├── sample_preparation.py
│   ├── analytical_workflow.py
│   └── high_throughput_screening.py
└── prefect_tasks.py
```

Install workflow dependencies with:

```bash
pip install -e ".[workflows]"
```

The workflow examples are for experiment orchestration across one or more
devices. They should call the gateway or `OT2Control`; they should not be needed
to start the gateway.

## Testing

Run focused gateway tests:

```bash
uv run --extra dev pytest tests/unit/test_gateway_service.py -q
```

Run all tests:

```bash
uv run --extra dev pytest tests -q
```

Compile/import sanity check:

```bash
python -m compileall src workflows tests/unit
```

## Notes

- This repo's OT-2 gateway conforms to the AC lab equipment status contract
  shape for `liquid_handler` devices.
- `requirements_api.txt` is intended for gateway/API runtime dependencies.
- Prefect is optional and belongs to workflow development, not gateway startup.
# Opentrons Workflows

A Python package for controlling Opentrons OT-2 and Flex robots via SSH with Prefect workflow orchestration integration.

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.0-orange.svg)](pyproject.toml)

## 🚀 Features

- **SSH-based Robot Control**: Direct SSH connection to OT-2 robots with explicit session management
- **Workflow Orchestration**: Integration with Prefect for complex, multi-step laboratory workflows
- **Real-time State Tracking**: Monitor robot state, labware, pipettes, and wells in real-time
- **REST API**: HTTP endpoints for remote robot control and workflow management
- **Custom Labware Generator**: Create custom labware definitions programmatically
- **Batch Operations**: Execute multiple commands with progress tracking and error handling
- **Simulation Support**: Full compatibility with Opentrons simulation mode for development and testing

## 📦 Installation

### From Source
```bash
git clone https://github.com/cyrilcaoyang/opentrons_workflows.git
cd opentrons_workflows
pip install -e .
```

### Dependencies
```bash
pip install -r requirements_api.txt
```

## 🏗️ Project Structure

```
opentrons_workflows/
├── src/opentrons_workflows/          # Core package
│   ├── opentrons_control.py          # Main robot control interface
│   ├── opentrons_sshclient.py        # SSH client with session management
│   ├── ot2_rest_api.py               # FastAPI REST endpoints
│   ├── workflow_orchestrator.py      # Prefect workflow definitions
│   ├── prefect_tasks.py              # Reusable Prefect tasks
│   └── labware_generator.py          # Custom labware creation
├── demo/                             # Example scripts and demos
├── users/                            # User-specific configurations
├── tests/                            # Test suites
└── backup/                           # Legacy/backup implementations
```

## 🚦 Quick Start

### Basic Robot Control

```python
from opentrons_workflows import OpentronsControl

# Connect to robot (simulation mode)
robot = OpentronsControl(host_alias="ot2_sim", simulation=True)

# Load labware and instruments
tip_rack = {"nickname": "tips", "loadname": "opentrons_96_tiprack_300ul", "location": "1", "ot_default": True}
plate = {"nickname": "plate", "loadname": "corning_96_wellplate_360ul_flat", "location": "2", "ot_default": True}
pipette = {"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "right", "ot_default": True}

robot.load_labware(tip_rack)
robot.load_labware(plate)
robot.load_instrument(pipette)

# Execute liquid handling operations
robot.pick_up_tip("tips", "A1", "p300")
robot.aspirate("plate", "A1", 100, "p300")
robot.dispense("plate", "B1", 100, "p300")
robot.drop_tip("p300")

robot.close_session()
```

### SSH Client with Session Management

```python
from opentrons_workflows import SSHClient

# Connect with explicit session control
client = SSHClient(host_alias="ot2_robot")
client.connect()

# Execute shell commands
result = client.execute_shell_command("hostname")
print(f"Robot hostname: {result}")

# Switch to Python mode for protocol execution
client.start_python_session()
response = client.execute_python_command("import opentrons; print(opentrons.__version__)")

# Batch operations with progress tracking
commands = [
    ("Import protocol API", "from opentrons import protocol_api"),
    ("Get protocol", "protocol = protocol_api.ProtocolContext(api_version='2.18')"),
    ("Load labware", "tips = protocol.load_labware('opentrons_96_tiprack_300ul', 1)")
]

results = client.execute_python_batch(commands)
client.close()
```

### Real-time State Tracking

```python
from opentrons_workflows import get_deck_state, get_pipette_state, get_labware_state

# Get complete deck overview
deck_state = get_deck_state(protocol)
print(f"Occupied slots: {deck_state['occupied_slots']}/12")

# Monitor pipette status
pipette_state = get_pipette_state(p300)
print(f"Pipette has tip: {pipette_state['has_tip']}")
print(f"Current volume: {pipette_state['current_volume']} μL")

# Track labware state (great for tip tracking)
tip_rack_state = get_labware_state(tip_rack)
print(f"Available tips: {tip_rack_state['summary']['available_tips']}")
```

### Prefect Workflow Orchestration

```python
from prefect import flow
from opentrons_workflows.workflow_orchestrator import sample_preparation_workflow

@flow
def my_lab_workflow():
    samples = [{"id": "sample_001", "volume": 100}]
    
    preparation_steps = [{
        "labware": [
            {"nickname": "tips", "loadname": "opentrons_96_tiprack_300ul", "location": "1"},
            {"nickname": "source", "loadname": "corning_96_wellplate_360ul_flat", "location": "2"}
        ],
        "instruments": [
            {"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "right"}
        ],
        "operations": [
            {"type": "pick_up_tip", "labware": "tips", "position": "A1", "pipette": "p300"},
            {"type": "aspirate", "labware": "source", "position": "A1", "pipette": "p300", "volume": 100}
        ]
    }]
    
    return sample_preparation_workflow("ot2_main", samples, preparation_steps)

# Run workflow
if __name__ == "__main__":
    my_lab_workflow()
```

## 🌐 REST API

Start the API server:

```bash
python -m opentrons_workflows.ot2_rest_api
```

### API Endpoints

- `GET /robots` - List connected robots
- `POST /robots/{robot_id}/connect` - Connect to a robot
- `POST /robots/{robot_id}/execute` - Execute commands
- `GET /robots/{robot_id}/state` - Get robot state
- `POST /workflows/liquid-handling` - Start liquid handling workflow
- `GET /workflows/{workflow_id}/status` - Check workflow status

### Example API Usage

```python
import requests

# Connect to robot
response = requests.post("http://localhost:8000/robots/ot2_main/connect", 
                        json={"host_alias": "ot2_sim"})

# Execute command
response = requests.post("http://localhost:8000/robots/ot2_main/execute",
                        json={"command": "print('Hello from robot!')", "session_type": "python"})

# Check robot state
response = requests.get("http://localhost:8000/robots/ot2_main/state")
print(response.json())
```

## 🔧 Configuration

### SSH Configuration

Create `~/.ssh/config` entry for your robot:

```
Host ot2_robot
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/ot2_ssh_key
    StrictHostKeyChecking no
```

### Environment Variables

```bash
export HOSTNAME="192.168.1.100"
export USERNAME="root"
export KEY_FILE_PATH="~/.ssh/ot2_ssh_key"
```

## 📋 Examples

The `demo/` directory contains comprehensive examples:

- **`demo_simple.py`** - Basic robot control
- **`demo_ot2_control.py`** - Advanced OT-2 operations
- **`demo_flex_control.py`** - Flex robot control
- **`demo_rest_api_workflow.py`** - REST API and Prefect workflows
- **`pdb_samp_prep.py`** - Protein sample preparation
- **`snar_test.py`** - SNAR assay automation

## 🧪 Testing

```bash
# Run basic tests
python -m pytest tests/

# Test SSH connection
python tests/test_ssh_methods.py

# Test robot states
python tests/test_states.py
```

## 📚 Advanced Features

### Custom Labware Generation

```python
from opentrons_workflows import LabwareGenerator

definition = {
    "load_name": "custom_plate_96_wellplate_200ul",
    "display_name": "Custom 96-Well Plate",
    "well_count": 96,
    "well_volume": 200,
    "well_depth": 10.5,
    "well_diameter": 6.85
}

generator = LabwareGenerator(definition)
labware_def = generator.generate_definition()
```

### Batch Command Execution

```python
# Execute multiple commands with error handling
commands = [
    ("Setup", "protocol = simulate.get_protocol_api('2.18')"),
    ("Load tips", "tips = protocol.load_labware('opentrons_96_tiprack_300ul', 1)"),
    ("Load pipette", "p300 = protocol.load_instrument('p300_single_gen2', 'right')")
]

results = client.execute_python_batch(
    commands,
    command_delay=0.5,
    show_progress=True,
    stop_on_error=True
)
```

### High-Throughput Workflows

```python
from opentrons_workflows.workflow_orchestrator import high_throughput_screening_workflow

# Screen 96 compounds across multiple robots
compound_library = [{"id": f"compound_{i:03d}", "concentration": 10.0} for i in range(96)]
robot_ids = ["ot2_main", "ot2_backup"]

result = high_throughput_screening_workflow(robot_ids, compound_library, assay_parameters)
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: Check the `users/` directory for detailed guides
- **Issues**: Report bugs on [GitHub Issues](https://github.com/cyrilcaoyang/opentrons_workflows/issues)
- **Examples**: See `demo/` directory for working examples

## 🙏 Acknowledgments

- Built for the [Opentrons](https://opentrons.com/) ecosystem
- Workflow orchestration powered by [Prefect](https://www.prefect.io/)
- SSH connectivity via [Paramiko](https://www.paramiko.org/)

---

**Version 0.2.0** - Ready for API integration and production use 