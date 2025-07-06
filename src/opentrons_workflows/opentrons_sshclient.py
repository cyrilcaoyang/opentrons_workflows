import paramiko
import paramiko.config
import time
import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
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
    Robust SSH Client for OT-2 with proper session state management
    Replaces the original SSHClient with improved reliability and session handling
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
        self.python_session = None  # Keep this name for backward compatibility
        self.session = None  # Internal session reference
        self.is_connected = False
        self.session_state = SessionState.UNKNOWN
        self._lock = threading.Lock()
        
        self._config_host()

    def _config_host(self):
        if (self.hostname is None) and (self.host_alias is None):
            raise ValueError("Both hostname and hostalias is None, invalid")
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
        """Establish SSH connection with retry logic"""
        with self._lock:
            for attempt in range(self.max_retries):
                try:
                    if self.ssh_client:
                        self.ssh_client.close()
                    
                    self.ssh_client = paramiko.SSHClient()
                    self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    
                    # Set connection timeout
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
                    self.python_session = self.session  # Backward compatibility
                    self.session.settimeout(self.command_timeout)
                    
                    # Wait for shell prompt and detect initial state
                    time.sleep(1)
                    self._detect_session_state()
                    
                    self.is_connected = True
                    logger.info(f"SSH connection established to {self.hostname}")
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

    def _detect_session_state(self):
        """Detect current session state by looking at the prompt"""
        try:
            # Clear any existing output
            self.clear_buffer()
            
            # Send empty command to get current prompt
            self.session.send("\n")
            time.sleep(0.5)
            
            output = ""
            if self.session.recv_ready():
                output = self.session.recv(1024).decode('utf-8')
            
            if ">>>" in output:
                self.session_state = SessionState.PYTHON
                logger.info("Detected Python session")
            elif "#" in output:
                self.session_state = SessionState.SHELL
                logger.info("Detected shell session")
            else:
                self.session_state = SessionState.UNKNOWN
                logger.warning(f"Unknown session state, output: {output}")
                
        except Exception as e:
            logger.warning(f"Failed to detect session state: {e}")
            self.session_state = SessionState.UNKNOWN

    def clear_buffer(self):
        """Clear any pending output from the buffer"""
        try:
            while self.session and self.session.recv_ready():
                self.session.recv(1024)
        except Exception:
            pass

    def _switch_to_python(self):
        """Switch from shell to Python session"""
        if self.session_state == SessionState.PYTHON:
            return True
            
        try:
            logger.info("Switching to Python session")
            self.session.send("python3\n")
            
            # Wait for Python to start
            output = ""
            start_time = time.time()
            
            while time.time() - start_time < 10:  # 10 second timeout
                time.sleep(0.2)
                if self.session.recv_ready():
                    chunk = self.session.recv(1024).decode('utf-8')
                    output += chunk
                    if ">>>" in chunk:
                        self.session_state = SessionState.PYTHON
                        logger.info("Successfully switched to Python session")
                        return True
            
            logger.error("Failed to switch to Python session")
            return False
            
        except Exception as e:
            logger.error(f"Error switching to Python: {e}")
            return False

    def _switch_to_shell(self):
        """Switch from Python to shell session"""
        if self.session_state == SessionState.SHELL:
            return True
            
        try:
            logger.info("Switching to shell session")
            self.session.send("exit()\n")
            
            # Wait for shell prompt
            output = ""
            start_time = time.time()
            
            while time.time() - start_time < 5:  # 5 second timeout
                time.sleep(0.2)
                if self.session.recv_ready():
                    chunk = self.session.recv(1024).decode('utf-8')
                    output += chunk
                    if "#" in chunk:
                        self.session_state = SessionState.SHELL
                        logger.info("Successfully switched to shell session")
                        return True
            
            logger.error("Failed to switch to shell session")
            return False
            
        except Exception as e:
            logger.error(f"Error switching to shell: {e}")
            return False

    def _is_connection_alive(self) -> bool:
        """Check if SSH connection is still alive"""
        try:
            if not self.ssh_client or not self.session:
                return False
            
            transport = self.ssh_client.get_transport()
            return transport and transport.is_active()
            
        except Exception:
            return False

    def invoke(self, code: str, timeout: Optional[int] = None) -> str:
        """
        Legacy invoke method for backward compatibility
        Automatically detects if it should be a Python or shell command
        """
        if timeout is None:
            timeout = self.command_timeout
        
        # Simple heuristic: if it looks like a shell command, use shell
        shell_commands = ['pip', 'ls', 'cat', 'grep', 'find', 'python3 --version', 'hostname']
        is_shell_command = any(code.strip().startswith(cmd) for cmd in shell_commands)
        
        if is_shell_command:
            return self.invoke_shell_command(code, timeout)
        else:
            return self.invoke_python_command(code, timeout)

    def invoke_python_command(self, code: str, timeout: Optional[int] = None) -> str:
        """Execute Python command with proper session handling"""
        if timeout is None:
            timeout = self.command_timeout
            
        for attempt in range(self.max_retries):
            try:
                if not self.is_connected or not self._is_connection_alive():
                    logger.info("Connection lost, attempting to reconnect...")
                    if not self.connect():
                        continue
                
                # Ensure we're in Python session
                if not self._switch_to_python():
                    raise Exception("Failed to switch to Python session")
                
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

    def invoke_shell_command(self, command: str, timeout: Optional[int] = None) -> str:
        """Execute shell command with proper session handling"""
        if timeout is None:
            timeout = self.command_timeout
            
        for attempt in range(self.max_retries):
            try:
                if not self.is_connected or not self._is_connection_alive():
                    logger.info("Connection lost, attempting to reconnect...")
                    if not self.connect():
                        continue
                
                # Ensure we're in shell session
                if not self._switch_to_shell():
                    raise Exception("Failed to switch to shell session")
                
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

    def invoke_with_retry(self, code: str, timeout: Optional[int] = None) -> str:
        """
        New method that provides explicit retry functionality
        Uses the same logic as invoke() but with explicit retry naming
        """
        return self.invoke(code, timeout)

    def _execute_python_command(self, code: str, timeout: int) -> str:
        """Execute a Python command and wait for >>> prompt"""
        with self._lock:
            if not self.session:
                raise Exception("No active session")
            
            # Clear buffer before sending command
            self.clear_buffer()
            
            # Send command
            self.session.send(code + "\n")
            
            # Collect response with timeout
            output = ""
            start_time = time.time()
            
            while True:
                if time.time() - start_time > timeout:
                    raise socket.timeout(f"Python command timeout after {timeout} seconds")
                
                try:
                    if self.session.recv_ready():
                        chunk = self.session.recv(1024).decode('utf-8')
                        output += chunk
                        
                        # Check for Python prompt indicating completion
                        if ">>> " in chunk:
                            break
                    else:
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
            self.clear_buffer()
            
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
                    self.python_session = None  # Backward compatibility
                    
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
            response = self.invoke_python_command("print('ping')", timeout=5)
            return "ping" in response
        except Exception:
            return False

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


# Legacy compatibility - keep the old usage pattern working
if __name__ == "__main__":
    # Example usage that maintains backward compatibility
    ssh_client = SSHClient(
        hostname="192.168.254.50",
        username="root",
        key_file_path=os.path.expanduser("~/.ssh/ot2_ssh_key"),
        password="accelerate"
    )
    
    if ssh_client.connect():
        # These should all work with the new implementation
        response = ssh_client.invoke("print('Hello from new robust client')")
        print("Response:", response)
        
        # New methods also available
        shell_response = ssh_client.invoke_shell_command("python3 --version")
        print("Shell response:", shell_response)
        
        python_response = ssh_client.invoke_python_command("import sys; print(sys.version)")
        print("Python response:", python_response)
        
        ssh_client.close()
