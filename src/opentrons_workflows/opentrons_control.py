import getpass
import json
import logging
import os
import datetime
from abc import ABC, abstractmethod
from .deck import Deck
from .gripper import Gripper
from .pipette import Pipette
from .sshclient import SSHClient

# Constants for protocol templates
FLEX_PROTOCOL_TEMPLATE = """
from opentrons import protocol_api

metadata = {'apiLevel': '2.15'}

def run(ctx: protocol_api.ProtocolContext):
    # COMMANDS_PLACEHOLDER
    # Gripper should be loaded and assigned if used
    pass
"""

OT2_PROTOCOL_TEMPLATE = """
from opentrons import protocol_api

metadata = {'apiLevel': '2.15'}

def run(ctx: protocol_api.ProtocolContext):
    # COMMANDS_PLACEHOLDER
    pass
"""


class OpenTronsBase(ABC):
    """
    Base class for controlling Opentrons robots.

    This class manages the SSH connection, protocol file handling, and command execution.
    It should not be instantiated directly. Use the `connect` factory function or the
    `OT2` and `Flex` subclasses instead.
    """

    def __init__(self, host_alias=None, robot_type=None, simulation=False, log_dir="logs", **kwargs):
        """
        Initializes the robot connection and sets up the protocol context.

        :param host_alias: The alias of the robot from sshclient_settings.json.
        :param robot_type: The type of robot ('OT-2' or 'Flex').
        :param simulation: If True, runs in simulation mode. Note: simulation still occurs on the robot via SSH.
        :param log_dir: The directory to store local log files. Defaults to "logs".
        :param kwargs: Additional arguments for the SSHClient.
        """
        self.simulation = simulation
        self.ssh_client = self._connect_to_robot(host_alias, robot_type, **kwargs)

        # --- Path Definitions ---
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = self.ssh_client.session_id
        
        # Remote paths for execution on the robot's filesystem
        self.remote_protocol_path = f"/tmp/{timestamp}_{session_id}_protocol.py"
        self.remote_results_path = f"/tmp/{timestamp}_{session_id}_results.json"

        # Local paths for logging and archiving
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        self.local_protocol_log_path = os.path.join(log_dir, f"{timestamp}_{session_id}_protocol.py")
        self.local_results_log_path = os.path.join(log_dir, f"{timestamp}_{session_id}_results.json")

        self.deck = Deck(self)
        self.instruments = {}  # Holds instrument objects, keyed by mount
        self._initialize_protocol(robot_type)

    def _connect_to_robot(self, host_alias, robot_type, **kwargs):
        """Establishes the SSH connection."""
        if self.simulation:
            logging.info("Running in SIMULATION mode. All commands will be simulated on the robot.")
        else:
            logging.info("Running in LIVE mode.")
        return SSHClient(host_alias=host_alias, simulation=self.simulation, **kwargs)

    @staticmethod
    def _find_simulation_alias(robot_type):
        """Finds the first alias in the settings file that matches the robot type and contains 'sim'."""
        try:
            settings_path = os.path.join(os.path.dirname(__file__), 'sshclient_settings.json')
            with open(settings_path, 'r') as f:
                settings = json.load(f)
            for alias, config in settings.items():
                if config.get('robot_type') == robot_type and 'sim' in alias:
                    return alias
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logging.error(f"Could not find a suitable simulation alias: {e}")
            return None
        return None

    def _initialize_protocol(self, robot_type):
        """Initializes the protocol file on the robot with a template."""
        if robot_type is None:
            raise ValueError("robot_type must be specified ('OT-2' or 'Flex')")

        template = self._get_protocol_template(robot_type)
        # Always write the initial protocol to the remote path, for both live and sim modes.
        self.ssh_client.write_file(self.remote_protocol_path, template)
        self.protocol_lines = template.splitlines()  # Store lines for local manipulation

    def _get_protocol_template(self, robot_type):
        """Gets the appropriate protocol template based on the robot type."""
        if robot_type == 'Flex':
            return FLEX_PROTOCOL_TEMPLATE
        elif robot_type == 'OT-2':
            return OT2_PROTOCOL_TEMPLATE
        else:
            raise ValueError(f"Unknown robot type: {robot_type}")

    def _add_command(self, command, json_response=False):
        """
        Adds a command to the protocol script, executes it, and returns the result.
        If json_response is True, it wraps the command to get a JSON output.
        """
        # Create a copy of protocol_lines to modify
        updated_lines = self.protocol_lines[:]

        # Find the line with "COMMANDS_PLACEHOLDER" and insert the command
        try:
            insert_index = updated_lines.index("    # COMMANDS_PLACEHOLDER")
            if json_response:
                # Wrap the command to capture its output and any errors in JSON format
                command_to_insert = f"""
    try:
        {command}  # Execute the command, ignore the complex return object
        output = {{'status': 'success', 'data': 'Command executed successfully', 'error': None, 'traceback': None}}
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        output = {{'status': 'error', 'data': None, 'error': str(e), 'traceback': tb_str}}
    
    import json
    with open('{self.remote_results_path}', 'w') as f:
        json.dump(output, f)
"""
            else:
                command_to_insert = f"    {command}"

            updated_lines.insert(insert_index, command_to_insert)

        except ValueError:
            logging.error("Could not find the '# COMMANDS_PLACEHOLDER' in the protocol template.")
            return None

        # After inserting, update the main protocol_lines for the next command
        self.protocol_lines = updated_lines

        # Write the updated protocol to the robot and to a local log file
        final_protocol_script = "\n".join(updated_lines)
        self.ssh_client.write_file(self.remote_protocol_path, final_protocol_script)
        with open(self.local_protocol_log_path, "w") as f:
            f.write(final_protocol_script)

        # Execute the script on the robot
        result = self.ssh_client.execute_protocol(
            self.remote_protocol_path,
            self.remote_results_path,
            simulation=self.simulation
        )

        # Save the results to a local log file
        with open(self.local_results_log_path, 'w') as f:
            json.dump(result, f, indent=2)

        # Log the output from the robot
        logging.info(f"Robot execution output for command '{command}':\n{result}")

        return result

    def execute_command(self, command):
        """
        Executes a raw python command on the robot and returns the JSON result.
        This is a low-level method intended for use by the Instrument/Deck classes.
        """
        return self._add_command(command, json_response=True)

    def load_pipette(self, pipette_name, mount):
        """
        Factory method to load a pipette onto the robot.

        :param pipette_name: The API name of the pipette (e.g., 'p1000_single_flex').
        :param mount: The mount to attach the pipette to ('left' or 'right').
        :return: A `Pipette` object.
        """
        if mount in self.instruments:
            raise ValueError(f"An instrument is already loaded on mount '{mount}'.")
        pipette = Pipette(self, pipette_name, mount)
        self.instruments[mount] = pipette
        return pipette

    def _load_pipette_on_robot(self, pipette_name: str, mount: str):
        """Internal method called by a Pipette object to execute its loading command."""
        command = f"ctx.load_instrument('{pipette_name}', '{mount}')"
        response = self.execute_command(command)
        if response.get('status') != 'success':
            error_msg = response.get('error', 'Unknown error')
            tb = response.get('traceback', 'No traceback available')
            raise RuntimeError(f"Failed to load pipette '{pipette_name}' on robot: {error_msg}\n{tb}")
        logging.info(f"Successfully loaded pipette '{pipette_name}' on mount '{mount}'.")
        return response

    def close(self):
        """
        Closes the SSH connection and cleans up remote temp files.
        The remote protocol and results files are kept as logs locally.
        """
        # Clean up remote files if the connection is still active
        if self.ssh_client and self.ssh_client.client and self.ssh_client.client.get_transport().is_active():
            try:
                # Construct path for the labware directory created during the session
                remote_labware_dir = f"/tmp/{self.ssh_client.session_id}_labware"
                cleanup_command = f"rm -rf {self.remote_protocol_path} {self.remote_results_path} {remote_labware_dir}"
                self.ssh_client.run_command(cleanup_command, check_error=False)
                logging.info("Cleaned up remote temporary files.")
            except Exception as e:
                logging.warning(f"Could not clean up remote files: {e}")

        self.ssh_client.close()
        logging.info(f"Connection closed for session {self.ssh_client.session_id}. Log files are preserved locally.")

    def __enter__(self):
        """Enter the runtime context and return the robot object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context and clean up resources."""
        self.close()


class OT2(OpenTronsBase):
    """
    Represents an OT-2 robot. Provides the main interface for OT-2 control.
    """

    def __init__(self, host_alias=None, simulation=False, **kwargs):
        """
        Initializes the OT-2 connection.

        :param host_alias: The alias of the robot from sshclient_settings.json.
        :param simulation: If True, runs in simulation mode without a real robot.
        :param kwargs: Additional arguments for the SSHClient.
        """
        super().__init__(host_alias=host_alias, robot_type='OT-2', simulation=simulation, **kwargs)


class Flex(OpenTronsBase):
    """
    Represents an Opentrons Flex robot. Provides the main interface for Flex control.
    """

    def __init__(self, host_alias=None, simulation=False, **kwargs):
        """
        Initializes the Flex connection.

        :param host_alias: The alias of the robot from sshclient_settings.json.
        :param simulation: If True, runs in simulation mode without a real robot.
        :param kwargs: Additional arguments for the SSHClient.
        """
        super().__init__(host_alias=host_alias, robot_type='Flex', simulation=simulation, **kwargs)

    def load_gripper(self):
        """
        Factory method to load the gripper onto the robot.

        :return: A `Gripper` object.
        """
        if 'gripper' in self.instruments:
            raise ValueError("A gripper is already loaded.")
        gripper = Gripper(self)
        self.instruments['gripper'] = gripper
        return gripper

    def _load_gripper_on_robot(self):
        """Internal method called by a Gripper object to execute its loading command."""
        command_load = "ctx.load_instrument('flex_gripper', 'extension')"
        response = self.execute_command(command_load)
        if response.get('status') != 'success':
            error_msg = response.get('error', 'Unknown error')
            tb = response.get('traceback', 'No traceback available')
            raise RuntimeError(f"Failed to load gripper on robot: {error_msg}\n{tb}")

        # Assign the loaded gripper to a variable in the protocol context for easy access
        command_assign = "self.gripper = ctx.loaded_instruments['extension']"
        response_assign = self.execute_command(command_assign)
        if response_assign.get('status') != 'success':
            error_msg = response_assign.get('error', 'Unknown error')
            tb = response_assign.get('traceback', 'No traceback available')
            # This is a critical failure in setting up the protocol context
            raise RuntimeError(f"Failed to assign gripper to variable on robot: {error_msg}\n{tb}")

        logging.info("Successfully loaded and assigned gripper.")
        return response


def connect(host_alias=None, simulation=False, log_dir="logs", **kwargs):
    """
    Factory function to connect to a robot using a pre-configured alias.

    This function reads `sshclient_settings.json`, determines the robot type
    from the settings file based on the host_alias and returns an instance of
    the appropriate class (`OT2` or `Flex`).

    :param host_alias: The alias of the robot as defined in sshclient_settings.json.
                         This must be a key in the settings file.
    :param simulation: If True, runs in `opentrons_simulate` mode on the robot.
    :param log_dir: The directory to store log files. Defaults to "logs".
    :param kwargs: Additional arguments passed to the SSHClient, such as 'password'.
    :return: An instance of `OT2` or `Flex`.
    """
    # Always look for the settings file relative to the project root
    # opentrons_control.py is in src/opentrons_workflows/, so go up two levels to reach project root
    settings_path = os.path.join(os.path.dirname(__file__), '..', '..', 'user_scripts', 'sshclient_settings.json')
    if not os.path.exists(settings_path):
        raise FileNotFoundError(f"Settings file not found at {settings_path}")

    with open(settings_path, 'r') as f:
        settings = json.load(f)

    if host_alias not in settings:
        raise ValueError(f"Host alias '{host_alias}' not found in sshclient_settings.json.")

    robot_type = settings[host_alias].get('robot_type')
    if not robot_type:
        raise ValueError(f"'robot_type' not specified for alias '{host_alias}' in settings.")

    if robot_type.lower() == 'ot2':
        return OT2(host_alias=host_alias, simulation=simulation, log_dir=log_dir, **kwargs)
    elif robot_type.lower() == 'flex':
        return Flex(host_alias=host_alias, simulation=simulation, log_dir=log_dir, **kwargs)
    else:
        raise ValueError(f"Unsupported robot type '{robot_type}' in settings for alias '{host_alias}'.")