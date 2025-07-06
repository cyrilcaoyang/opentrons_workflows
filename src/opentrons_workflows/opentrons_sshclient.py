import paramiko
import paramiko.config
import time
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import threading
import socket
from contextlib import contextmanager
from enum import Enum

logger = logging.getLogger(__name__)

class SessionState(Enum):
    SHELL = "shell"
    PYTHON = "python"
    UNKNOWN = "unknown"

class SSHClient:
    """
    SSH Client for OT-2 with explicit session state management
    
    This client maintains either a Python REPL session or a shell session,
    never both simultaneously. Users must explicitly choose which type of
    session they want to use.
    """
    
    def __init__(self, hostname=None, username=None, key_file_path=None, 
                 host_alias=None, password=None, max_retries=3, 
                 command_timeout=30, connection_timeout=10):
        self.hostname = hostname
        self.username = username
        self.key_file_path = key_file_path
        self.host_alias = host_alias
        self.password = password
        self.max_retries = max_retries
        self.command_timeout = command_timeout
        self.connection_timeout = connection_timeout
        
        self.ssh_client = None
        self.session = None
        self.is_connected = False
        self.session_state = SessionState.UNKNOWN
        self._lock = threading.Lock()
        
        self._config_host()

    def _config_host(self):
        if (self.hostname is None) and (self.host_alias is None):
            raise ValueError("Both hostname and host_alias are None, invalid")
        if self.host_alias:
            ssh_config = self._load_ssh_config()
            self.hostname = ssh_config["hostname"]
            self.username = ssh_config["user"]
            # Handle case where identityfile is a list
            identity_file = ssh_config["identityfile"]
            if isinstance(identity_file, list):
                identity_file = identity_file[0]  # Use the first key file
            self.key_file_path = os.path.expanduser(identity_file)

    def _load_ssh_config(self):
        ssh_config_file = Path.home() / ".ssh" / "config"
        config = paramiko.config.SSHConfig()
        with open(ssh_config_file) as f:
            config.parse(f)
        return config.lookup(self.host_alias)

    def connect(self) -> bool:
        """Establish SSH connection and start in shell mode"""
        with self._lock:
            for attempt in range(self.max_retries):
                try:
                    if self.ssh_client:
                        self.ssh_client.close()
                    
                    self.ssh_client = paramiko.SSHClient()
                    self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    
                    private_key = paramiko.RSAKey.from_private_key_file(
                        self.key_file_path, password=self.password
                    )
                    
                    self.ssh_client.connect(
                        hostname=self.hostname,
                        username=self.username,
                        pkey=private_key,
                        timeout=self.connection_timeout,
                        banner_timeout=self.connection_timeout
                    )
                    
                    # Start shell session
                    self.session = self.ssh_client.invoke_shell()
                    self.session.settimeout(self.command_timeout)
                    
                    # Wait for shell prompt
                    time.sleep(1)
                    self._wait_for_shell_prompt()
                    
                    self.session_state = SessionState.SHELL
                    self.is_connected = True
                    logger.info(f"SSH connection established to {self.hostname} in SHELL mode")
                    return True
                    
                except Exception as e:
                    logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                    else:
                        logger.error(f"Failed to connect after {self.max_retries} attempts")
                        self.is_connected = False
                        return False
            
            return False

    def _wait_for_shell_prompt(self):
        """Wait for shell prompt (#) to appear"""
        self._clear_buffer()
        self.session.send("\n")
        time.sleep(0.5)
        
        output = ""
        start_time = time.time()
        while time.time() - start_time < 5:
            if self.session.recv_ready():
                chunk = self.session.recv(1024).decode('utf-8')
                output += chunk
                if "#" in chunk:
                    logger.info("Shell prompt detected")
                    return
            time.sleep(0.1)
        
        logger.warning("Shell prompt not detected within timeout")

    def _wait_for_python_prompt(self):
        """Wait for Python prompt (>>>) to appear"""
        output = ""
        start_time = time.time()
        while time.time() - start_time < 10:
            if self.session.recv_ready():
                chunk = self.session.recv(1024).decode('utf-8')
                output += chunk
                if ">>>" in chunk:
                    logger.info("Python prompt detected")
                    return
            time.sleep(0.1)
        
        raise Exception("Python prompt not detected within timeout")

    def _clear_buffer(self):
        """Clear any pending output from the buffer"""
        try:
            while self.session and self.session.recv_ready():
                self.session.recv(1024)
        except Exception:
            pass

    def start_python_session(self):
        """
        Switch to Python REPL session
        
        This will start a Python interpreter and all subsequent commands
        will be executed as Python code until switch_to_shell() is called.
        """
        if self.session_state == SessionState.PYTHON:
            logger.info("Already in Python session")
            return True
            
        if not self.is_connected:
            raise Exception("Not connected to robot")
            
        try:
            logger.info("Starting Python session")
            self._clear_buffer()
            self.session.send("python3\n")
            
            self._wait_for_python_prompt()
            self.session_state = SessionState.PYTHON
            logger.info("Successfully started Python session")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Python session: {e}")
            raise Exception(f"Failed to start Python session: {e}")

    def switch_to_shell(self):
        """
        Switch to shell session
        
        If currently in Python session, this will exit Python and return
        to the shell prompt. All subsequent commands will be shell commands.
        """
        if self.session_state == SessionState.SHELL:
            logger.info("Already in shell session")
            return True
            
        if not self.is_connected:
            raise Exception("Not connected to robot")
            
        try:
            logger.info("Switching to shell session")
            self.session.send("exit()\n")
            
            self._wait_for_shell_prompt()
            self.session_state = SessionState.SHELL
            logger.info("Successfully switched to shell session")
            return True
            
        except Exception as e:
            logger.error(f"Failed to switch to shell: {e}")
            raise Exception(f"Failed to switch to shell: {e}")

    def _is_connection_alive(self) -> bool:
        """Check if SSH connection is still alive"""
        try:
            if not self.ssh_client or not self.session:
                return False
            
            transport = self.ssh_client.get_transport()
            return transport and transport.is_active()
            
        except Exception:
            return False

    def execute_python_command(self, code: str, timeout: Optional[int] = None) -> str:
        """
        Execute Python command in Python REPL session
        
        The session must be in Python mode. If not, raises an exception.
        Use start_python_session() first to enter Python mode.
        """
        if self.session_state != SessionState.PYTHON:
            raise Exception(f"Cannot execute Python command: session is in {self.session_state.value} mode. Call start_python_session() first.")
        
        if timeout is None:
            timeout = self.command_timeout
            
        for attempt in range(self.max_retries):
            try:
                if not self.is_connected or not self._is_connection_alive():
                    logger.info("Connection lost, attempting to reconnect...")
                    if not self.connect():
                        continue
                    # After reconnect, we're in shell mode, need to restart Python
                    self.start_python_session()
                
                return self._execute_python_command(code, timeout)
                
            except (socket.timeout, paramiko.SSHException, OSError) as e:
                logger.warning(f"Python command execution failed (attempt {attempt + 1}): {e}")
                self.is_connected = False
                
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    raise Exception(f"Python command failed after {self.max_retries} attempts: {e}")
        
        raise Exception("Failed to execute Python command after all retry attempts")

    def execute_shell_command(self, command: str, timeout: Optional[int] = None) -> str:
        """
        Execute shell command in shell session
        
        The session must be in shell mode. If not, raises an exception.
        Use switch_to_shell() first to enter shell mode.
        """
        if self.session_state != SessionState.SHELL:
            raise Exception(f"Cannot execute shell command: session is in {self.session_state.value} mode. Call switch_to_shell() first.")
        
        if timeout is None:
            timeout = self.command_timeout
            
        for attempt in range(self.max_retries):
            try:
                if not self.is_connected or not self._is_connection_alive():
                    logger.info("Connection lost, attempting to reconnect...")
                    if not self.connect():
                        continue
                    # After reconnect, we're already in shell mode
                
                return self._execute_shell_command(command, timeout)
                
            except (socket.timeout, paramiko.SSHException, OSError) as e:
                logger.warning(f"Shell command execution failed (attempt {attempt + 1}): {e}")
                self.is_connected = False
                
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    raise Exception(f"Shell command failed after {self.max_retries} attempts: {e}")
        
        raise Exception("Failed to execute shell command after all retry attempts")

    def _execute_python_command(self, code: str, timeout: int) -> str:
        """Execute a Python command and wait for >>> prompt (handling multi-line code)"""
        with self._lock:
            if not self.session:
                raise Exception("No active session")
            
            # Clear buffer before sending command
            self._clear_buffer()
            
            # Send command
            self.session.send(code + "\n")
            
            # For multi-line code, we need to send an extra newline to finish
            # Check if this is likely multi-line code (contains def, class, etc.)
            is_multiline = any(keyword in code for keyword in ['def ', 'class ', 'if ', 'for ', 'while ', 'with ', 'try:'])
            
            # Collect response with timeout
            output = ""
            start_time = time.time()
            last_chunk_time = start_time
            continuation_mode = False
            
            while True:
                current_time = time.time()
                if current_time - start_time > timeout:
                    raise socket.timeout(f"Python command timeout after {timeout} seconds")
                
                try:
                    if self.session.recv_ready():
                        chunk = self.session.recv(1024).decode('utf-8')
                        output += chunk
                        last_chunk_time = current_time
                        
                        # Check for continuation prompt (multi-line mode)
                        if "... " in chunk:
                            continuation_mode = True
                            if is_multiline:
                                # Send empty line to complete multi-line block
                                self.session.send("\n")
                        
                        # Check for completion - primary prompt after any continuation
                        elif ">>> " in chunk:
                            if continuation_mode:
                                # Wait a bit more to ensure completion
                                time.sleep(0.1)
                            break
                    else:
                        # If no data for a while and we're in continuation mode, send empty line
                        if continuation_mode and is_multiline and (current_time - last_chunk_time > 1.0):
                            self.session.send("\n")
                            continuation_mode = False
                        time.sleep(0.1)
                        
                except socket.timeout:
                    raise socket.timeout(f"Python command timeout after {timeout} seconds")
                except Exception as e:
                    raise Exception(f"Error reading Python response: {e}")
            
            # Check for Python errors
            if "Traceback (most recent call last):" in output:
                raise Exception(f"Python error in command execution:\n{output}")
            
            return output

    def _execute_shell_command(self, command: str, timeout: int) -> str:
        """Execute a shell command and wait for # prompt"""
        with self._lock:
            if not self.session:
                raise Exception("No active session")
            
            # Clear buffer before sending command
            self._clear_buffer()
            
            # Send command
            self.session.send(command + "\n")
            
            # Collect response with timeout
            output = ""
            start_time = time.time()
            
            while True:
                if time.time() - start_time > timeout:
                    raise socket.timeout(f"Shell command timeout after {timeout} seconds")
                
                try:
                    if self.session.recv_ready():
                        chunk = self.session.recv(1024).decode('utf-8')
                        output += chunk
                        
                        # Check for shell prompt indicating completion
                        if "# " in chunk:
                            break
                    else:
                        time.sleep(0.1)
                        
                except socket.timeout:
                    raise socket.timeout(f"Shell command timeout after {timeout} seconds")
                except Exception as e:
                    raise Exception(f"Error reading shell response: {e}")
            
            return output

    def close(self):
        """Close SSH connection"""
        with self._lock:
            try:
                if self.session:
                    if self.session_state == SessionState.PYTHON:
                        self.session.send("exit()\n")
                        time.sleep(0.5)
                    self.session.close()
                    self.session = None
                    
                if self.ssh_client:
                    self.ssh_client.close()
                    self.ssh_client = None
                    
                self.is_connected = False
                self.session_state = SessionState.UNKNOWN
                logger.info("SSH connection closed")
                
            except Exception as e:
                logger.warning(f"Error closing SSH connection: {e}")

    def ping(self) -> bool:
        """Test connection with a simple ping"""
        try:
            if self.session_state != SessionState.PYTHON:
                self.start_python_session()
            
            response = self.execute_python_command("print('ping')", timeout=5)
            return "ping" in response
        except Exception:
            return False

    def execute_command_batch(self, commands: List[Tuple[str, str]], 
                            command_delay: float = 0.2,
                            show_progress: bool = True,
                            stop_on_error: bool = True,
                            timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Execute a batch of commands with descriptions and formatted output.
        
        Args:
            commands: List of (description, command) tuples
            command_delay: Delay between commands in seconds
            show_progress: Whether to show progress output
            stop_on_error: Whether to stop execution on first error
            timeout: Timeout for each command (uses default if None)
            
        Returns:
            List of results: [{'description': str, 'command': str, 'success': bool, 'output': str, 'error': str}]
        """
        results = []
        total_commands = len(commands)
        
        for i, (description, command) in enumerate(commands, 1):
            if show_progress:
                print(f"\n[{i:2d}/{total_commands}] {description}")
                print(f"         → {command}")
            
            result = {
                'description': description,
                'command': command,
                'success': False,
                'output': '',
                'error': ''
            }
            
            try:
                # Execute command based on current session state
                if self.session_state == SessionState.PYTHON:
                    response = self.execute_python_command(command, timeout)
                else:
                    response = self.execute_shell_command(command, timeout)
                
                result['success'] = True
                result['output'] = response
                
                # Show meaningful output if progress is enabled
                if show_progress and response and response.strip():
                    lines = response.strip().split('\n')
                    for line in lines:
                        # Filter out prompt lines and command echoes
                        if (line.strip() and 
                            not line.startswith('>>> ') and 
                            not line.startswith('# ') and 
                            command not in line):
                            print(f"         ✅ {line}")
                
                # Add delay between commands
                if command_delay > 0:
                    time.sleep(command_delay)
                
            except Exception as e:
                result['error'] = str(e)
                
                if show_progress:
                    print(f"         ❌ Error: {e}")
                
                if stop_on_error:
                    results.append(result)
                    if show_progress:
                        print(f"\n❌ Stopping execution due to error in command {i}")
                    break
            
            results.append(result)
        
        return results

    def execute_python_batch(self, commands: List[Tuple[str, str]], **kwargs) -> List[Dict[str, Any]]:
        """
        Execute a batch of Python commands. Ensures we're in Python mode first.
        
        Args:
            commands: List of (description, command) tuples
            **kwargs: Additional arguments passed to execute_command_batch:
                - command_delay: float = 0.2 (delay between commands in seconds)
                - show_progress: bool = True (show numbered progress output)
                - stop_on_error: bool = True (stop execution on first error)
                - timeout: Optional[int] = None (timeout for each command)
            
        Returns:
            List of command results
        """
        if self.session_state != SessionState.PYTHON:
            if kwargs.get('show_progress', True):
                print("🐍 Switching to Python mode...")
            self.start_python_session()
            if kwargs.get('show_progress', True):
                print(f"✅ Now in {self.session_state.value} mode")
        
        return self.execute_command_batch(commands, **kwargs)

    def execute_shell_batch(self, commands: List[Tuple[str, str]], **kwargs) -> List[Dict[str, Any]]:
        """
        Execute a batch of shell commands. Ensures we're in shell mode first.
        
        Args:
            commands: List of (description, command) tuples
            **kwargs: Additional arguments passed to execute_command_batch:
                - command_delay: float = 0.2 (delay between commands in seconds)
                - show_progress: bool = True (show numbered progress output)
                - stop_on_error: bool = True (stop execution on first error)
                - timeout: Optional[int] = None (timeout for each command)
            
        Returns:
            List of command results
        """
        if self.session_state != SessionState.SHELL:
            if kwargs.get('show_progress', True):
                print("🐚 Switching to shell mode...")
            self.switch_to_shell()
            if kwargs.get('show_progress', True):
                print(f"✅ Now in {self.session_state.value} mode")
        
        return self.execute_command_batch(commands, **kwargs)

    def send_code_block(self, code: str, description: str = "Code block", 
                       timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Send a multi-line code block (function, class, etc.) in Python mode.
        
        Args:
            code: Multi-line Python code
            description: Description for progress output
            timeout: Command timeout (uses default if None)
            
        Returns:
            Result dictionary with success/error info
        """
        if self.session_state != SessionState.PYTHON:
            raise Exception("Must be in Python mode to send code blocks")
        
        print(f"📤 Sending {description}...")
        
        try:
            response = self.execute_python_command(code, timeout)
            
            if "Traceback" in response or "Error" in response:
                print(f"❌ {description} failed: {response}")
                return {'success': False, 'error': response, 'output': response}
            else:
                print(f"✅ {description} loaded successfully")
                return {'success': True, 'error': '', 'output': response}
                
        except Exception as e:
            print(f"❌ {description} failed with exception: {e}")
            return {'success': False, 'error': str(e), 'output': ''}

    def get_connection_status(self) -> Dict[str, Any]:
        """Get detailed connection status"""
        return {
            "connected": self.is_connected,
            "hostname": self.hostname,
            "username": self.username,
            "session_state": self.session_state.value,
            "transport_active": self._is_connection_alive(),
            "can_ping": self.ping() if self.is_connected else False
        }

    @contextmanager
    def session_context(self):
        """Context manager for SSH sessions"""
        try:
            if not self.connect():
                raise Exception("Failed to establish SSH connection")
            yield self
        finally:
            self.close()


# Example usage demonstrating explicit session management
if __name__ == "__main__":
    ssh_client = SSHClient(
        hostname="192.168.254.50",
        username="root",
        key_file_path=os.path.expanduser("~/.ssh/ot2_ssh_key"),
        password="accelerate"
    )
    
    if ssh_client.connect():
        print(f"Connected in {ssh_client.session_state.value} mode")
        
        # Execute shell commands
        shell_response = ssh_client.execute_shell_command("hostname")
        print("Shell response:", shell_response)
        
        # Switch to Python mode
        ssh_client.start_python_session()
        print(f"Now in {ssh_client.session_state.value} mode")
        
        # Execute Python commands
        python_response = ssh_client.execute_python_command("import sys; print(sys.version)")
        print("Python response:", python_response)
        
        # Switch back to shell if needed
        ssh_client.switch_to_shell()
        print(f"Back to {ssh_client.session_state.value} mode")
        
        ssh_client.close()
