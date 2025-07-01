import json
import os
import datetime
from abc import ABC, abstractmethod
from .deck import Deck
from .gripper import Gripper
from .pipette import Pipette
from .sshclient import SSHClient
from .logging_config import get_logger, setup_default_logging

# Constants for protocol templates
OT2_METADATA = {
    'apiLevel': '2.15'
}

FLEX_METADATA = {
    'apiLevel': '2.15',
    'requirements': {'robotType': 'Flex'}
}


class OpenTronsBase(ABC):
    """
    Base class for controlling Opentrons robots.

    This class manages the SSH connection, protocol file handling, and command execution.
    It should not be instantiated directly. Use the `connect` factory function or the
    `OT2` and `Flex` subclasses instead.
    """

    def __init__(self, host_alias=None, robot_type=None, simulation=False, log_dir="logs", 
                 custom_logger=None, **kwargs):
        """
        Initializes the robot connection and sets up the protocol context.

        :param host_alias: The alias of the robot from sshclient_settings.json.
        :param robot_type: The type of robot ('OT-2' or 'Flex').
        :param simulation: If True, runs in simulation mode. Note: simulation still occurs on the robot via SSH.
        :param log_dir: The directory to store local log files. Defaults to "logs".
        :param custom_logger: Optional custom logger to use instead of unified logging.
        :param kwargs: Additional arguments for the SSHClient.
        """
        # Set up logging - either custom or default
        if custom_logger is not None:
            self.logger = custom_logger
        else:
            # Set up default logging to /logs
            self.logger = setup_default_logging(log_dir=log_dir)
        
        self.simulation = simulation
        self.ssh_client = self._connect_to_robot(host_alias, robot_type, **kwargs)

        # Set API level and model based on the known robot_type from settings.
        if robot_type == 'Flex':
            self.robot_api_level = '2.23'
            self.robot_model = 'OT-3 Standard'
            self.protocol_robot_type = 'Flex'
        elif robot_type == 'OT-2':
            self.robot_api_level = '2.15'
            self.robot_model = 'OT-2 Standard'
            self.protocol_robot_type = 'OT-2'
        else:
            # This will cause a fatal error if robot_type is not what we expect.
            raise ValueError(f"FATAL: Unexpected robot_type '{robot_type}' received in OpenTronsBase constructor.")
        
        self.logger.info(f"Configuring for {self.robot_model}. API Level: {self.robot_api_level}")

        # --- Path Definitions ---
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = self.ssh_client.session_id
        
        # Remote paths for execution on the robot's filesystem
        self.remote_protocol_path = f"/tmp/{timestamp}_{session_id}_protocol.py"
        self.remote_results_path = f"/tmp/{timestamp}_{session_id}_results.json"

        # Local paths for logging and archiving
        log_dir = os.path.abspath(log_dir)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        self.local_protocol_log_path = os.path.join(log_dir, f"{timestamp}_{session_id}_protocol.py")
        self.local_results_log_path = os.path.join(log_dir, f"{timestamp}_{session_id}_results.json")

        self.deck = Deck(self)
        self.instruments = {}  # Holds instrument objects, keyed by mount
        self.commands = [] # Stateless command list

    def _connect_to_robot(self, host_alias, robot_type, **kwargs):
        """Establishes the SSH connection."""
        if self.simulation:
            self.logger.info("Running in SIMULATION mode. All commands will be simulated on the robot.")
        else:
            self.logger.info("Running in LIVE mode.")
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
            # Use a default logger for static method
            logger = get_logger(__name__)
            logger.error(f"Could not find a suitable simulation alias: {e}")
            return None
        return None

    def _add_command(self, command, json_response=False):
        """
        Adds a command to the list, generates the entire protocol from scratch,
        and executes it.
        """
        self.commands.append(command)

        # Generate the script from scratch each time to ensure statelessness
        command_str = '\n    '.join(self.commands)

        # Base template - use requirements dict for API 2.15+ and robotType specification
        if float(self.robot_api_level) >= 2.15:
            template = f"""
from opentrons import protocol_api

metadata = {{'apiLevel': '{self.robot_api_level}'}}
requirements = {{'robotType': '{self.protocol_robot_type}'}}

def run(ctx: protocol_api.ProtocolContext):
    {command_str}
"""
        else:
            template = f"""
from opentrons import protocol_api

metadata = {{'apiLevel': '{self.robot_api_level}'}}

def run(ctx: protocol_api.ProtocolContext):
    {command_str}
"""
        
        # Add result-capturing logic if needed
        if json_response:
            final_command = self.commands[-1] # Get the most recent command
            script_to_run = f"""
    try:
        # We only care about the result of the LAST command.
        {final_command}
        output = {{'status': 'success', 'data': 'Command executed successfully', 'error': None, 'traceback': None}}
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        output = {{'status': 'error', 'data': None, 'error': str(e), 'traceback': tb_str}}
    
    import json
    with open('{self.remote_results_path}', 'w') as f:
        json.dump(output, f)
"""
            # Replace the last command with the try/except block
            template = template.replace(final_command, script_to_run)

        # Write the updated protocol to the robot and to a local log file
        self.ssh_client.write_file(self.remote_protocol_path, template)
        with open(self.local_protocol_log_path, "w") as f:
            f.write(template)

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
        self.logger.info(f"Robot execution output for command '{command}':\n{result}")

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
        self.logger.info(f"Successfully loaded pipette '{pipette_name}' on mount '{mount}'.")
        return response

    def close(self):
        """
        Closes the SSH connection and cleans up remote temp files.
        The remote protocol and results files are kept as logs locally.
        """
        # Clean up remote files if the connection is still active
        if self.ssh_client and self.ssh_client.client:
            transport = self.ssh_client.client.get_transport()
            if transport and transport.is_active():
                try:
                    # Construct path for the labware directory created during the session
                    remote_labware_dir = f"/tmp/{self.ssh_client.session_id}_labware"
                    cleanup_command = f"rm -rf {self.remote_protocol_path} {self.remote_results_path} {remote_labware_dir}"
                    self.ssh_client.run_command(cleanup_command, check_error=False)
                    self.logger.info("Cleaned up remote temporary files.")
                except Exception as e:
                    self.logger.warning(f"Could not clean up remote files: {e}")

        if self.ssh_client:
            self.ssh_client.close()
            self.logger.info(f"Connection closed for session {self.ssh_client.session_id}. Log files are preserved locally.")

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
        custom_logger = kwargs.pop('custom_logger', None)
        super().__init__(host_alias=host_alias, robot_type='OT-2', simulation=simulation, 
                         custom_logger=custom_logger, **kwargs)


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
        custom_logger = kwargs.pop('custom_logger', None)
        super().__init__(host_alias=host_alias, robot_type='Flex', simulation=simulation, 
                         custom_logger=custom_logger, **kwargs)

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
        # In Opentrons Flex, grippers are not explicitly "loaded" like pipettes
        # Instead, they are automatically available when physically attached
        # and accessed through ctx.move_labware() commands
        
        # Test that the gripper is available by checking if we can access labware movement
        command_test = "# Gripper is automatically available when attached to Flex robot"
        response = self.execute_command(command_test)
        if response.get('status') != 'success':
            error_msg = response.get('error', 'Unknown error')
            tb = response.get('traceback', 'No traceback available')
            raise RuntimeError(f"Failed to verify gripper availability on robot: {error_msg}\n{tb}")

        self.logger.info("Gripper is available and ready for use with move_labware() commands.")
        return response


def connect(host_alias=None, simulation=False, log_dir="logs", custom_logger=None, **kwargs):
    """
    Factory function to create robot connections based on configuration.

    This function automatically determines the robot type based on the
    host alias configuration and returns the appropriate robot instance.

    :param host_alias: The alias of the robot from sshclient_settings.json.
    :param simulation: If True, runs in simulation mode. Default is False.
    :param log_dir: The directory to store log files. Defaults to "logs".
    :param custom_logger: Optional custom logger to use instead of default logging.
    :param kwargs: Additional arguments passed to the robot constructor.
    :return: An instance of `OT2` or `Flex` based on the robot configuration.
    :raises ValueError: If the robot type is not recognized or no robot type could be determined.
    """
    # Get robot type from settings
    # Look for settings file in user_scripts directory (relative to package root)
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    settings_path = os.path.join(package_root, 'user_scripts', 'sshclient_settings.json')
    robot_type = None
    settings = {}

    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
        robot_type = settings.get(host_alias, {}).get('robot_type')
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    # If no robot type found and in simulation mode, try to find a sim alias
    if robot_type is None and simulation:
        # Try common simulation aliases
        for sim_alias in ['ot2_sim', 'flex_sim']:
            if sim_alias in settings:
                robot_type = settings[sim_alias].get('robot_type')
                if robot_type:
                    host_alias = sim_alias
                    break

    if robot_type == 'OT-2':
        return OT2(host_alias=host_alias, simulation=simulation, log_dir=log_dir, 
                   custom_logger=custom_logger, **kwargs)
    elif robot_type == 'Flex':
        return Flex(host_alias=host_alias, simulation=simulation, log_dir=log_dir, 
                    custom_logger=custom_logger, **kwargs)
    else:
        raise ValueError(f"Unsupported or unknown robot type: {robot_type}")