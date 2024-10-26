import paramiko
import time

import paramiko
import time
import re

class SSHClient:
    def __init__(self, hostname, username, key_file_path):
        self.hostname = hostname
        self.username = username
        self.key_file_path = key_file_path
        self.ssh_client = None
        self.python_session = None

    def connect(self):
        """Establish SSH connection and start Python terminal"""
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Use the private key for authentication
        private_key = paramiko.RSAKey.from_private_key_file(self.key_file_path)
        self.ssh_client.connect(hostname=self.hostname, username=self.username, pkey=private_key)
        
        # Start a persistent Python interactive session
        self.python_session = self.ssh_client.invoke_shell()
        self.python_session.send("python3\n")  # Start Python terminal
        time.sleep(1)  # Wait for the Python terminal to initialize
        self.clear_buffer()  # Clear initial prompt output

    def clear_buffer(self):
        """Clear any pending output from the buffer"""
        if self.python_session.recv_ready():
            self.python_session.recv(1024)

    def invoke(self, code):
        """Send code to be executed in the Python terminal and check for errors"""
        if self.python_session is None:
            raise Exception("SSH connection is not open. Call connect() first.")

        # Send code to Python terminal and execute
        self.python_session.send(code + "\n")
        
        # Wait for output to finish
        output = ""
        while True:
            time.sleep(0.1)  # Delay for processing
            if self.python_session.recv_ready():
                output += self.python_session.recv(1024).decode('utf-8')
                # Check if Python prompt (">>> ") reappears indicating execution is complete
                if output.strip().endswith(">>>"):
                    break

        # Check for errors in the output
        if "Traceback (most recent call last):" in output or "Exception" in output:
           #print(f"Warning: An error occurred while executing the code:\n{output}")
            raise Exception(f"An error occurred while executing the code:\n{output}")

        return output

    def close(self):
        """Close SSH connection"""
        if self.python_session is not None:
            self.python_session.send("exit()\n")  # Exit Python terminal
            time.sleep(1)
            self.python_session.close()
        if self.ssh_client is not None:
            self.ssh_client.close()

if __name__ == "__main__":
    # Instantiate and connect with the private key
    ssh_client = SSHClient(
        hostname="169.254.195.156",
        username="root",
        key_file_path="ot2_ssh_key"  # Path to your private key file
    )
    ssh_client.connect()

    # Send each line of code to the Python terminal
    ssh_client.invoke("import opentrons.execute")
    ssh_client.invoke("protocol = opentrons.execute.get_protocol_api('2.11')")
    output = ssh_client.invoke("protocol.home()")

    # Print the output of the `protocol.home()` command
    print("Output of protocol.home():", output)

    # Close the session
    ssh_client.close()
