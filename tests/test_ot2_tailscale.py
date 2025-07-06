#!/usr/bin/env python3
"""
OT-2 Tailscale Connection Test

Basic connectivity test for OT-2 robot via Tailscale SSH.
Tests connection, Python session, and basic Opentrons imports.
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
        # Use parent connect method to establish connection in shell mode
        if not super().connect():
            return False
        
        # Switch to Python session
        try:
            self.start_python_session()
            return True
        except Exception as e:
            print(f"Failed to start Python session: {e}")
            return False
    
    def run_python_command(self, command, timeout=10):
        """Run Python command and get response"""
        try:
            response = self.execute_python_command(command, timeout=timeout)
            # Clean up the response to extract meaningful output
            lines = response.strip().split('\n')
            output_lines = []
            for line in lines:
                # Skip command echo and prompt lines
                if line.strip() and not line.startswith('>>> ') and command not in line:
                    output_lines.append(line)
            return '\n'.join(output_lines) if output_lines else response
        except Exception as e:
            return f"Error: {e}"
    
    def run_shell_command(self, command, timeout=10):
        """Run shell command (switch to shell first)"""
        try:
            # Switch to shell mode
            self.switch_to_shell()
            
            response = self.execute_shell_command(command, timeout=timeout)
            
            # Clean up the response
            lines = response.strip().split('\n')
            output_lines = []
            for line in lines:
                # Skip command echo and prompt lines
                if line.strip() and not line.startswith('# ') and command not in line:
                    output_lines.append(line)
            
            # Switch back to Python for consistency
            self.start_python_session()
            
            return '\n'.join(output_lines) if output_lines else response
            
        except Exception as e:
            return f"Error: {e}"

def main():
    """Main test function"""
    print("🤖 OT-2 Test")
    print("=" * 30)
    
    # Prompt for password
    password = input("Enter OT-2 password: ")
    client = OT2Client(host_alias="ot2_tailscale", password=password)
    
    try:
        # Connect
        print("1. Connecting...")
        if not client.connect():
            print("❌ Connection failed")
            return False
        print("✅ Connected and in Python mode")
        
        # Get Python version
        print("2. Getting Python version...")
        response = client.run_python_command(
            "import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
            )
        print(f"   Python version: {response}")
        
        # Test simple command
        print("3. Testing Python session...")
        response = client.run_python_command("print('OT-2 Python session active')")
        print(f"   ✅ Response: {response}")
        
        # Get pip list
        print("4. Getting pip packages...")
        pip_response = client.run_shell_command("pip list | head -20", timeout=30)  # It will take a while
        
        if pip_response and not pip_response.startswith("Error"):
            print("   ✅ Sample packages installed:")
            # Parse and display packages nicely
            lines = pip_response.split('\n')
            for line in lines[:10]:  # Show first 10
                if line.strip() and not line.startswith('Package') and not line.startswith('---'):
                    print(f"     {line.strip()}")
            if len(lines) > 10:
                print(f"     ... and more packages available")
        else:
            print(f"   ⚠️ Could not get package list: {pip_response}")
        
        # Test some basic Opentrons imports
        print("5. Testing Opentrons imports...")
        imports_to_test = [
            "import opentrons",
            "from opentrons import execute",
            "print(f'Opentrons version: {opentrons.__version__}')"
        ]
        
        for import_cmd in imports_to_test:
            response = client.run_python_command(import_cmd, timeout=120)
            if "Error" not in response and "Traceback" not in response:
                if "Opentrons version" in response:
                    print(f"   ✅ {response}")
                else:
                    print(f"   ✅ {import_cmd}")
            else:
                print(f"   ⚠️ {import_cmd} - {response}")
        
        client.close()
        print("✅ Test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        try:
            client.close()
        except:
            pass
        return False

if __name__ == "__main__":
    success = main()
    if success:
        print("\n🎉 OT-2 connection and Python session working!")
        print("Ready for opentrons_workflows testing!")
    else:
        print("\n❌ Test failed!")
        sys.exit(1) 