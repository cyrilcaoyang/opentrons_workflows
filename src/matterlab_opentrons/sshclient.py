import getpass
import json
import logging
import os
import uuid
import paramiko
import tarfile
from io import BytesIO

class SSHClient:
    """
    Manages a direct SSH connection to an Opentrons robot.

    This class handles the low-level details of connecting to the robot,
    executing commands, and managing files. It is designed to be used as a
    context manager to ensure that connections are properly closed.
    """
    def __init__(self, host_alias=None, simulation=False, password=None, **kwargs):
        """
        Initializes the SSH client and establishes a connection.

        :param host_alias:  The alias of the robot from sshclient_settings.json.
                            This is a mandatory parameter that points to a pre-configured
                            set of connection details. This is the same as the host_alias
                            used in the connect() function. This is the same as the host_alias
                            used in the connect() function.
        :param simulation:  A boolean flag passed down from the controlling class.
                            It does not alter connection behavior but is stored for context.
        :param password:    The password for the private SSH key. If not provided,
                            the client will search for a password file or prompt the user.
        :param kwargs: Additional keyword arguments (currently unused).
        """
        settings = self._load_settings()
        if host_alias not in settings:
            raise ValueError(f"Host alias '{host_alias}' not found in sshclient_settings.json")

        config = settings[host_alias]
        self.hostname = config['hostname']
        self.username = config.get('username', 'root')
        self.key_filename = os.path.expanduser(config.get('key_filename', '~/.ssh/ot2_ssh_key'))
        self.port = config.get('port', 22)
        self.session_id = str(uuid.uuid4())
        self.simulation = simulation

        self.password = self._get_password(password)
        self.client = self._connect()

        # Upload labware definitions on connection
        self._upload_custom_labware()

    def _load_settings(self):
        """Loads SSH settings from the JSON file."""
        # Always look for the settings file relative to the project root
        # sshclient.py is in src/matterlab_opentrons/, so go up two levels to reach project root
        settings_path = os.path.join(os.path.dirname(__file__), '..', '..', 'user_scripts', 'sshclient_settings.json')
        if not os.path.exists(settings_path):
            raise FileNotFoundError(f"Settings file not found at {settings_path}")
        with open(settings_path, 'r') as f:
            return json.load(f)

    def _get_password(self, password_arg):
        """
        Determines the key password to use.
        Order of precedence: argument -> file -> user prompt.
        """
        if password_arg:
            logging.debug("Using password from argument.")
            return password_arg

        password_file = os.path.expanduser("~/.ssh/ot2_ssh_password")
        if os.path.exists(password_file):
            logging.debug(f"Reading password from {password_file}.")
            with open(password_file, 'r') as f:
                return f.read().strip()

        logging.debug("Prompting for password.")
        return getpass.getpass(f"Enter password for SSH key {self.key_filename}: ")

    def _upload_custom_labware(self):
        """
        Uploads the entire user_scripts/generated_labware directory to the robot's /tmp/ directory.
        This makes custom labware available for protocol execution.
        """
        local_labware_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'user_scripts', 'generated_labware'))
        remote_tar_path = f"/tmp/{self.session_id}_labware.tar"
        remote_dest_dir = f"/tmp/{self.session_id}_labware"

        if not os.path.isdir(local_labware_dir):
            logging.warning(f"Local labware directory not found at {local_labware_dir}. Skipping upload.")
            return

        try:
            # Create a tar archive of the labware directory's contents in memory
            tar_data = BytesIO()
            with tarfile.open(fileobj=tar_data, mode='w') as tar:
                tar.add(local_labware_dir, arcname='.') # Add contents to root of tar
            tar_data.seek(0)

            # Upload the tar file to the robot
            sftp = self.client.open_sftp()
            sftp.putfo(tar_data, remote_tar_path)
            sftp.close()

            # Untar the file on the robot into the destination directory
            self.run_command(f"mkdir -p {remote_dest_dir}")
            self.run_command(f"tar -xf {remote_tar_path} -C {remote_dest_dir}")

            # The final labware path on the robot is the destination directory itself
            self.remote_labware_path = remote_dest_dir
            logging.info(f"Successfully uploaded custom labware to {self.remote_labware_path} on robot.")

        except Exception as e:
            logging.error(f"Failed to upload custom labware: {e}")
            self.remote_labware_path = None
        finally:
            # Clean up the tar file on the robot
            if self.client.get_transport().is_active():
                try:
                    sftp_client = self.client.open_sftp()
                    sftp_client.remove(remote_tar_path)
                    sftp_client.close()
                except FileNotFoundError:
                    pass # It's okay if it's already gone
                except Exception as e:
                    logging.warning(f"Could not clean up remote tar file: {e}")

    def _connect(self):
        """Establishes the SSH connection."""
        logging.info(f"Initializing SSH connection to {self.hostname}...")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            logging.info("Loading private key...")
            private_key = paramiko.RSAKey.from_private_key_file(self.key_filename, password=self.password)
            
            logging.info("Attempting to connect to robot...")
            # Add timeout to prevent hanging
            client.connect(
                hostname=self.hostname, 
                port=self.port, 
                username=self.username, 
                pkey=private_key,
                timeout=30,  # 30 second timeout
                banner_timeout=60  # 60 second banner timeout
            )
            logging.info(f"✅ Successfully connected to {self.username}@{self.hostname} for session {self.session_id}")
            return client
        except Exception as e:
            logging.error(f"❌ Failed to connect to {self.hostname}: {e}")
            raise

    def run_command(self, command, check_error=True):
        """Runs a generic command on the robot."""
        logging.debug(f"Running command: {command}")
        stdin, stdout, stderr = self.client.exec_command(command, timeout=60)  # 60 second timeout
        exit_code = stdout.channel.recv_exit_status()
        stderr_output = stderr.read().decode().strip()

        if exit_code != 0 and check_error:
            error_message = f"Command failed with exit code {exit_code}: {stderr_output}"
            logging.error(error_message)
            raise RuntimeError(error_message)
        return stdout.read().decode().strip()

    def read_file(self, remote_path):
        """Reads the content of a remote file."""
        with self.client.open_sftp() as sftp:
            with sftp.file(remote_path, 'r') as f:
                return f.read().decode('utf-8')

    def write_file(self, remote_path, content):
        """
        Writes a string content to a specified file on the robot.

        This method uses `echo` and `tee` to write the file, which is more
        reliable than SFTP for files in locations with restricted permissions.

        :param remote_path: The absolute path to the destination file on the robot.
        :param content: The string content to be written to the file.
        """
        # Use echo and tee to write the file to avoid issues with SFTP permissions
        escaped_content = content.replace("'", "'\\''")
        command = f"echo '{escaped_content}' | tee {remote_path} > /dev/null"
        self.run_command(command, check_error=True)

    def execute_protocol(self, protocol_path, results_path, simulation=False):
        """
        Executes a protocol on the robot.

        :param protocol_path: The absolute path to the protocol file on the robot.
        :param results_path: The absolute path to the results file on the robot.
        :param simulation: If True, use 'opentrons_simulate'. Otherwise, use 'opentrons_execute'.
        :return: The parsed JSON result from the robot.
        """
        # Base command
        executable = "opentrons_simulate" if simulation else "opentrons_execute"
        command = f"{executable}"

        # Add custom labware path if it was successfully uploaded
        if hasattr(self, 'remote_labware_path') and self.remote_labware_path:
            command += f" --custom-labware-path {self.remote_labware_path}"

        command += f" {protocol_path}"

        logging.info(f"Executing protocol command: {command}")
        stdin, stdout, stderr = self.client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        stderr_output = stderr.read().decode().strip()

        # The protocol script is responsible for writing the results file in all cases.
        # We just need to read it.
        try:
            result_json = self.read_file(results_path)
            return json.loads(result_json)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.error(f"Failed to retrieve or parse results from {results_path}. Error: {e}")
            if exit_code != 0:
                logging.error(f"Protocol execution failed with exit code {exit_code}. Stderr: {stderr_output}")
                return {'status': 'error', 'data': None, 'error': f'Protocol failed with exit code {exit_code}', 'traceback': stderr_output}
            else:
                # This case handles when the file is missing/corrupt but the command reported success.
                return {'status': 'error', 'data': None, 'error': f'Result file handling failed: {e}', 'traceback': 'No traceback available.'}

    def close(self):
        """Closes the SSH connection."""
        if self.client:
            self.client.close()
            logging.info(f"Connection to {self.hostname} closed.")

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context and close the connection."""
        self.close()

    def __del__(self):
        """Ensure connection is closed when the object is garbage collected."""
        self.close()
