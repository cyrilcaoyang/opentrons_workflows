#!/usr/bin/env python3
"""
OT-2 Connection Test
Connect, get Python version, start Python session, and get pip list
"""

import sys
from pathlib import Path
import time
import paramiko

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows.opentrons_sshclient import SSHClient

class OT2Client(SSHClient):
    """Simple OT-2 client with proper session handling"""
    
    def connect(self):
        """Connect and start Python session"""
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        private_key = paramiko.RSAKey.from_private_key_file(self.key_file_path, password=self.password)
        self.ssh_client.connect(hostname=self.hostname, username=self.username, pkey=private_key)
        
        self.python_session = self.ssh_client.invoke_shell()
        time.sleep(1)
        
        # Clear initial output
        if self.python_session.recv_ready():
            self.python_session.recv(4096)
            
        # Start Python
        self.python_session.send("python3\n")
        time.sleep(2)
        
        # Clear startup messages
        if self.python_session.recv_ready():
            self.python_session.recv(4096)
    
    def run_command(self, command, timeout=10):
        """Run command and get response"""
        self.python_session.send(command + "\n")
        
        output = ""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            time.sleep(0.2)
            if self.python_session.recv_ready():
                new_data = self.python_session.recv(1024).decode('utf-8')
                output += new_data
                if ">>> " in new_data:
                    break
        
        return output.strip()
    
    def run_shell_command(self, command, timeout=10):
        """Run shell command (exit Python first)"""
        self.python_session.send("exit()\n")
        time.sleep(1)
        
        # Clear any output
        if self.python_session.recv_ready():
            self.python_session.recv(4096)
        
        # Run shell command
        self.python_session.send(command + "\n")
        
        output = ""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            time.sleep(0.2)
            if self.python_session.recv_ready():
                new_data = self.python_session.recv(1024).decode('utf-8')
                output += new_data
                if "# " in new_data or "$ " in new_data:
                    break
        
        return output.strip()

def main():
    """Main test function"""
    print("🤖 OT-2 Test")
    print("=" * 30)
    
    client = OT2Client(host_alias="ot2_tailscale", password="accelerate")
    
    try:
        # Connect
        print("1. Connecting...")
        client.connect()
        print("✅ Connected")
        
        # Get Python version
        print("2. Getting Python version...")
        response = client.run_command("import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
        print(f"   {response.split('Python')[-1].strip()}")
        
        # Test simple command
        print("3. Testing Python session...")
        response = client.run_command("print('OT-2 Python session active')")
        print(f"   ✅ {response.split('OT-2')[-1].strip()}")
        
        # Get pip list
        print("4. Getting pip packages...")
        pip_response = client.run_shell_command("pip list", timeout=20)
        
        # Parse pip output
        lines = pip_response.split('\n')
        packages = []
        for line in lines:
            if line.strip() and not line.startswith('Package') and not line.startswith('---'):
                if ' ' in line.strip():
                    packages.append(line.strip())
        
        print(f"   Found {len(packages)} packages:")
        for pkg in packages[:10]:  # Show first 10
            print(f"     {pkg}")
        if len(packages) > 10:
            print(f"     ... and {len(packages) - 10} more")
        
        client.close()
        print("✅ Test completed successfully!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        try:
            client.close()
        except:
            pass
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    if success:
        print("\n🎉 OT-2 connection and Python session working!")
    else:
        print("\n❌ Test failed!")
        sys.exit(1) 