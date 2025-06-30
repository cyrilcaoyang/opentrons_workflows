# Opentrons Control

This project provides a Python interface to control Opentrons robots (OT-2 and Flex).

## Core Philosophy: Prefect for Robust Workflows

This library is designed to be used with [Prefect](https://www.prefect.io/), a modern workflow orchestration tool. While you can use it in a simple Python script, leveraging Prefect provides significant advantages for laboratory automation:
- **Observability**: Get a clear view of your protocol's execution, with detailed logs and a UI.
- **Robustness**: Automatically handle transient errors with retries.
- **Modularity**: Build complex protocols from small, reusable, and testable tasks.

### The `@robust_task` Decorator

The recommended way to build protocols is with the custom `@robust_task` decorator. It is a wrapper around Prefect's standard `@task` that adds automated logging and error handling specific to robot operations.

```python
from src.matterlab_opentrons.prefect_tasks import robust_task

@robust_task(retries=2)
def pick_up_tip_task(ot, pip_name):
    ot.pick_up_tip(pip_name)
```

### Other Usage Patterns

While `@robust_task` is recommended, the library supports multiple styles, as shown in the `examples/` directory:
- **Robust Prefect (`@robust_task` decorator)**: The best practice for reliability.
- **Standard Prefect (`@task` decorator)**: For users who want standard Prefect behavior.
- **Simple Scripts (No Prefect)**: For very simple or one-off protocols.

## Key Library Components

This library is organized into a few key modules:

-   **`OpenTronsControl.py`**: This is the low-level "engine" of the library. It contains the `OpenTronsBase`, `OT2`, and `Flex` classes that manage the direct SSH connection and execute commands on the robot. You can use its methods directly for fine-grained control, but the recommended approach is to use the Prefect tasks that wrap them.

-   **`prefect_tasks.py`**: This module provides a collection of pre-built, robust Prefect tasks. It contains the `@robust_task` decorator and ready-to-use tasks like `aspirate_task`, `dispense_task`, etc. This is the high-level, recommended interface for building reliable protocols.

-   **`prefect_setup.py`**: A convenient utility script to help you configure your local environment to connect to a Prefect backend, such as a local server or Prefect Cloud. This is useful for visualizing your workflow runs in the Prefect UI.

## Installation

To use this library, install it in "editable" mode. This is the recommended setup because it ensures your scripts can always access the installed package correctly.

```bash
pip install -e .
```

This command installs all required dependencies including:
- Core robot control libraries (`paramiko`, etc.)
- The Prefect workflow engine
- Jupyter Notebook and JupyterLab for interactive protocol creation

After installing, you can run the examples in simulation mode immediately. To connect to a real robot, you must first complete the SSH Key Setup.

## Connecting to a Robot

The primary entry point for the library is the `connect` function. It reads your configuration from `sshclient_settings.json` and returns a robot object that is ready to use. All connections **must** be pre-configured in the settings file; direct connection via IP address is not supported to ensure that all connection parameters are explicitly managed.

You can interact with the robot directly or use the robot object as a context manager (`with` statement) to ensure resources are automatically cleaned up.

### Basic Connection (Live Mode)
```python
from matterlab_opentrons import connect

# The 'host_alias' must match a key in your settings file.
with connect(host_alias="ot2_lab") as ot:
    # Your robot commands go here
    ot.deck.load_labware('corning_96_wellplate_360ul_flat', 'D2')
    # ...
```

### Simulation Mode

To validate your script against the robot's simulation engine without moving the hardware, set `simulation=True`. The library will connect to the robot and use the `opentrons_simulate` command.

```python
from matterlab_opentrons import connect

# It's recommended to have a separate alias for simulation connections
with connect(host_alias="ot2_sim", simulation=True) as ot_sim:
    # Your robot commands to be simulated go here
    # ...
```

## SSH Key Setup (Recommended)

Before connecting to a real robot, you must ensure it is configured for SSH key-based authentication.

**1. Setting Up the Robot**

If you are setting up a new robot, you must generate an SSH key and add its public key to the robot's list of authorized keys. If your robot has already been set up by an administrator, you must obtain the correct private key (`ot2_ssh_key`) and its password from them. Securely place the key file in your `~/.ssh/` directory.

*   **Generate a New Key**:
    ```bash
    # When prompted, save the file to ~/.ssh/ot2_ssh_key
    # Enter a secure password when prompted.
    ssh-keygen -t rsa -b 4096 -f ~/.ssh/ot2_ssh_key
    ```
    This creates a private key (`~/.ssh/ot2_ssh_key`) and a public key (`~/.ssh/ot2_ssh_key.pub`).

*   **Add Public Key to Robot**:
    *   Connect to your robot using the Opentrons App.
    *   Navigate to **Device** > **Robot Settings** > **Advanced** > **SSH Keys**.
    *   Copy the content of your `~/.ssh/ot2_ssh_key.pub` file and paste it into the text box.

**2. Set Up the Password File (Optional)**

To avoid entering your SSH key password every time, you can store it in `~/.ssh/ot2_ssh_password`.

```bash
# Replace YOUR_SSH_KEY_PASSWORD with the correct password.
echo "YOUR_SSH_KEY_PASSWORD" > ~/.ssh/ot2_ssh_password
```

**3. Set Secure File Permissions**

For security, your private key and password file must not be accessible to other users on your system.

```bash
chmod 600 ~/.ssh/ot2_ssh_key
chmod 600 ~/.ssh/ot2_ssh_password
```

After completing these steps, your system is securely configured to connect to your Opentrons robot.

## Connection Management

You can connect to an Opentrons robot in two ways:

### 1. Using a Host Alias

The recommended method is to use a `host_alias` defined in the `src/matterlab_opentrons/sshclient_settings.json` file. This approach centralizes all your connection settings, making them easy to manage and deploy.

**Configuration (`src/matterlab_opentrons/sshclient_settings.json`):**
```json
{
  "ot2_local": {
    "hostname": "192.168.254.50",
    "username": "root",
    "key_file_path": "~/.ssh/ot2_ssh_key"
  },
  "otflex_tailscale": {
    "hostname": "100.64.254.92",
    "username": "root",
    "key_file_path": "~/.ssh/ot2_ssh_key"
  }
}
```

**Required SSH Files:**
- `~/.ssh/ot2_ssh_key`: Your SSH private key
- `~/.ssh/ot2_ssh_password`: Your SSH key password (optional, for automation)

**Usage:**
```python
from src.matterlab_opentrons.OpenTronsControl import OpenTrons

# Connects using the alias defined in settings.json
ot = OpenTrons(host_alias="ot2_local", password="YOUR_PASSWORD", simulation=True)
```

### 2. Using Direct Parameters

For quick connections or testing, you can provide the connection details directly when instantiating the `OpenTrons` class.

**Usage:**
```python
from src.matterlab_opentrons.OpenTronsControl import OpenTrons

ot = OpenTrons(
    hostname="192.168.254.50",
    username="root",
    key_file_path="~/.ssh/ot2_ssh_key"
)
```

## Password Management

To enhance security, passwords for SSH keys are not stored in the project's source code. The system retrieves the required password using the following priority:

1.  **Direct Argument**: You can pass the password directly using the `password` parameter. This is useful for scripts where security is less of a concern.
    ```python
    ot = OpenTrons(host_alias="ot2_local", password="YOUR_SSH_KEY_PASSWORD")
    ```

2.  **Password File**: If the `password` argument is not provided, the system will look for a file named `ot2_ssh_password` in your `~/.ssh/` directory and read the password from it. This is a secure way to automate scripts without hardcoding credentials.
    ```bash
    # Store your password in the file
    echo "YOUR_SSH_KEY_PASSWORD" > ~/.ssh/ot2_ssh_password
    chmod 600 ~/.ssh/ot2_ssh_password # Set secure permissions
    ```

3.  **Manual Prompt**: If neither a direct argument nor a password file is found, you will be securely prompted to enter the password in your terminal. This is the default and most secure method for interactive use.
    ```
    Enter password for SSH key ~/.ssh/ot2_ssh_key:
    ```
This flexible system allows you to choose the most appropriate method for managing connections and passwords based on your use case and security requirements.

## Custom Labware Workflow

This repository uses a generator-based workflow to create and manage custom labware definitions. This ensures that all labware is version-controlled and easily reproducible.

The process involves three main components:
-   `src/matterlab_opentrons/labware_definitions.py`: A dedicated file where you add the data for all your custom labware.
-   `src/matterlab_opentrons/labware_generator.py`: A script that reads the data from the definitions file and generates the final JSON files.
-   `generated_labware/`: A directory where the generated JSON files are stored.
-   `settings/`: The directory where you place the final, curated JSON files that your protocols will actually use.

### How to Create New Labware

**Step 1: Add a Definition**

Open `src/matterlab_opentrons/labware_definitions.py`. Add a new Python dictionary to the `LABWARE_DEFINITIONS` list. You must include a unique `load_name` for each one.

```python
# In src/matterlab_opentrons/labware_definitions.py

LABWARE_DEFINITIONS = [
    # ... existing definitions
    {
      "display_name": "My Custom 96-Well Plate",
      "display_category": "wellPlate",
      "load_name": "my_custom_96_wellplate",
      # ... other parameters
    },
]
```

**Step 2: Run the Generator**

Execute the generator script from the root of the project.

```bash
python src/matterlab_opentrons/labware_generator.py
```

The script will read your new definition and create a corresponding `my_custom_96_wellplate.json` file inside the `generated_labware/` directory.

**Step 3: Promote the Labware**

Manually move the new JSON file from the `generated_labware/` directory to the `settings/` directory. Only labware files present in `settings/` can be loaded by the `OpenTronsControl` class.

This final step ensures that only validated and approved labware definitions are used in your protocols.

## Interactive Development with Jupyter

This package includes full Jupyter notebook support for interactive protocol development. This allows you to develop, test, and iterate on your robot protocols in real-time.

### Getting Started

1. **Install the package** (includes Jupyter by default):
   ```bash
   pip install -e .
   ```

2. **Start Jupyter**:
   ```bash
   jupyter notebook
   # or
   jupyter lab
   ```

3. **Open the example notebook**:
   Navigate to `user_scripts/interactive_protocol_development.ipynb`

### Key Features

- **Asyncio Compatibility**: Uses `nest_asyncio` to seamlessly integrate Prefect flows with Jupyter
- **Interactive Protocol Development**: Test robot operations step-by-step
- **Safety First**: All examples use `simulation=True` by default
- **Prefect Integration**: Full workflow capabilities within notebooks
- **Robust Error Handling**: Automatic retry logic with `@robust_task` decorator

### Example Usage

```python
# In a Jupyter notebook cell
import nest_asyncio
nest_asyncio.apply()

from prefect import flow
from src.matterlab_opentrons import OpenTrons, robust_task

@flow
def my_protocol(simulation=True):
    ot = OpenTrons(host_alias="ot2_sim_local", simulation=simulation)
    # Your protocol here
    ot.close()

# Run interactively
my_protocol()
```

### Best Practices

1. **Always use `simulation=True`** during development
2. **Test small operations first** before building complex protocols
3. **Use the `@robust_task` decorator** for automatic retry logic
4. **Restart the kernel** if you encounter asyncio-related errors
5. **Check your `sshclient_settings.json`** file for correct robot aliases

## Examples

The `examples/` directory contains several demonstration scripts:

- `demo_ot2_robust.py`: An OT-2 protocol using the recommended `@robust_task` decorator.
- `demo_ot2_standard.py`: An OT-2 protocol using the standard Prefect `@task` decorator.
- `demo_flex_gripper_robust.py`: A Flex protocol demonstrating gripper control.
- `demo_flex_simple.py`: A simple, non-Prefect script for the Flex.

Run any example with:
```bash
python examples/demo_ot2_robust.py
``` 