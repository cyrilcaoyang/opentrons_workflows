# Opentrons Control

This project provides a Python interface to control Opentrons robots (OT-2 and Flex) via SSH, with a strong emphasis on creating robust, observable, and repeatable scientific workflows using Prefect.

## Core Philosophy: Prefect for Robust Workflows

This library is designed to be used with [Prefect](https://www.prefect.io/), a modern workflow orchestration tool. While you can use it in a simple Python script, leveraging Prefect provides significant advantages for laboratory automation:
- **Observability**: Get a clear view of your protocol's execution, with detailed logs and a UI.
- **Robustness**: Automatically handle transient errors with retries.
- **Modularity**: Build complex protocols from small, reusable, and testable tasks.

The recommended way to build protocols is with the custom `@robust_task` decorator, which wraps Prefect's standard `@task` to add automated logging and error handling specific to robot operations.

## Key Library Components

This library is organized into a few key modules within the `src/opentrons_workflows` directory:

-   **`opentrons_control.py`**: The main "engine" of the library. It contains the `OpenTronsBase`, `OT2`, and `Flex` classes that manage the robot state, and the `connect()` function which is the primary entry point for establishing a connection.
-   **`sshclient.py`**: Handles the low-level details of the SSH connection, including command execution, file management, and automatic uploading of custom labware.
-   **`deck.py`**, **`pipette.py`**, **`gripper.py`**: These modules represent the physical components of the robot, providing an object-oriented interface to control them.
-   **`prefect_tasks.py`**: Provides a collection of pre-built, robust Prefect tasks for common robot actions (`aspirate_task`, `dispense_task`, etc.).
-   **`labware_generator.py`**: A standalone script to generate new custom labware JSON definitions.

## Web UI

A web-based dashboard is available for real-time robot visualization and control. See [`src/opentrons-ui/README.md`](src/opentrons-ui/README.md) for full documentation.

**Quick Start:**
```bash
python user_scripts/start_ui.py
# Open http://localhost:8080
```

## Project Structure

All user-facing scripts, notebooks, and configurations are located in the `user_scripts/` directory, keeping the `src/` directory clean for core library code.
-   `user_scripts/sshclient_settings.json`: Configure your robot SSH connection details here.
-   `user_scripts/labware_definitions.py`: Define your custom labware here.
-   `user_scripts/generated_labware/`: The output directory for the labware generator.
-   `user_scripts/workflow_dev_notebook.ipynb`: An interactive notebook for protocol development.
-   `logs/`: A top-level directory where all session logs (protocols and results) are automatically saved.

## Installation

This project uses `pyproject.toml` for dependency management. To install the package and all required dependencies, including tools for testing, run the following command from the project root directory:

```bash
# The '-e' flag installs in "editable" mode
# The '[dev]' part installs the testing dependencies (like pytest)
pip install -e ".[dev]"
```

## Connecting to a Robot

The primary entry point for the library is the `connect` function. It reads your configuration from `user_scripts/sshclient_settings.json` and returns a robot object that is ready to use.

```python
from opentrons_workflows import connect

# The 'host_alias' must match a key in your settings file.
# The connection is automatically closed when the 'with' block is exited.
with connect(host_alias="ot2_tailscale", simulation=True) as ot:
    # Your robot commands go here
    ot.deck.load_labware('opentrons_96_tiprack_300ul', '2')
    # ...
```

**Note on Simulation:** Setting `simulation=True` still establishes a live SSH connection to the robot. The simulation is run on the robot's hardware using the `opentrons_simulate` command, providing the most accurate possible simulation.

## SSH Key Setup

Before connecting to a real robot, you must ensure it is configured for SSH key-based authentication.

1.  **Generate a New Key**:
    ```bash
    # When prompted, save the file to ~/.ssh/ot2_ssh_key
    # Enter a secure password when prompted.
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/ot2_ssh_key
    ```

2.  **Add Public Key to Robot**:
    *   Connect to your robot using the Opentrons App.
    *   Navigate to **Device** > **Robot Settings** > **Advanced** > **SSH Keys**.
    *   Copy the content of your `~/.ssh/ot2_ssh_key.pub` file and paste it into the text box.

3.  **Set Up the Password File (for Automation)**: To avoid entering your SSH key password every time, you can store it in `~/.ssh/ot2_ssh_password`.
    ```bash
    # Replace YOUR_SSH_KEY_PASSWORD with the correct password.
    echo "YOUR_SSH_KEY_PASSWORD" > ~/.ssh/ot2_ssh_password
    chmod 600 ~/.ssh/ot2_ssh_password
    ```

4.  **Set Secure File Permissions**:
    ```bash
    chmod 600 ~/.ssh/ot2_ssh_key
    ```

## Custom Labware Workflow

The labware workflow is designed to be simple and automated.

### 1. Define Your Labware
Add a Python dictionary describing your labware to the `LABWARE_DEFINITIONS` list in `user_scripts/labware_definitions.py`.

```python
# In user_scripts/labware_definitions.py
LABWARE_DEFINITIONS = [
    {
      "display_name": "My Custom 96-Well Plate",
      "display_category": "wellPlate",
      "load_name": "my_custom_96_wellplate",
      # ... other parameters
    },
]
```

### 2. Generate the JSON File
Run the generator script from the project's root directory:
```bash
python src/opentrons_workflows/labware_generator.py
```
This will create a new `.json` file in the `user_scripts/generated_labware/` directory.

### 3. Use Your Labware
That's it! When you connect to a robot, the library **automatically uploads all labware** from the `user_scripts/generated_labware/` directory to the robot and makes it available to your protocol. There is no need to manually move or promote files.

```python
with connect(host_alias="ot2_tailscale") as ot:
    # The library automatically finds your custom labware
    my_plate = ot.deck.load_labware("my_custom_96_wellplate", "D2")
```

## Testing

This project uses `pytest` for testing. The tests are located in the `tests/` directory.

1.  **Install Dependencies**: Make sure you have installed the development dependencies:
    ```bash
    pip install -e ".[dev]"
    ```

2.  **Run Tests**: Run the test suite from the project's **root directory**. It is important to use the `python -m pytest` pattern to ensure you are using the `pytest` executable from your activated virtual environment.
    ```bash
    python -m pytest tests/test_ot2_tailscale_connection.py
    ```

## Interactive Development with Jupyter

For interactive protocol development, a Jupyter notebook is provided.

1.  **Start Jupyter**:
    ```bash
    jupyter notebook
    # or
    jupyter lab
    ```

2.  **Open the example notebook**:
    Navigate to `user_scripts/workflow_dev_notebook.ipynb`

## Examples

The `examples/` directory contains several demonstration scripts showing different ways to use the library, from simple scripts to robust Prefect workflows. To run an example:
```bash
python examples/demo_ot2_robust.py
``` 