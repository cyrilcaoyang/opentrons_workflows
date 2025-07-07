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