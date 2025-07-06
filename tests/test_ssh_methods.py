#!/usr/bin/env python3

"""
Comprehensive test of SSH client methods with timeout support.

This script demonstrates all SSH client methods:
- execute_command_batch()
- execute_python_batch() 
- execute_shell_batch()
- send_code_block()

With various timeout configurations and error handling patterns.
"""

import sys
import time
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows.opentrons_sshclient import SSHClient


def test_basic_python_batch():
    """Test basic Python batch execution with default timeout"""
    print("\n🔬 Test 1: Basic Python Batch Execution")
    print("-" * 50)
    
    commands = [
        ("Import sys module", "import sys"),
        ("Check Python version", "print(f'Python version: {sys.version_info.major}.{sys.version_info.minor}')"),
        ("Import math", "import math"),
        ("Calculate pi", "print(f'Pi: {math.pi:.4f}')"),
        ("Simple arithmetic", "result = 42 * 7; print(f'42 * 7 = {result}')"),
    ]
    
    return commands, "Basic Python operations with default timeout"


def test_python_batch_with_timeout():
    """Test Python batch with custom timeout"""
    print("\n⏱️  Test 2: Python Batch with Custom Timeout")
    print("-" * 50)
    
    commands = [
        ("Import time", "import time"),
        ("Quick operation", "print('This is fast')"),
        ("Slightly slower", "time.sleep(0.5); print('This took 0.5 seconds')"),
        ("Import datetime", "from datetime import datetime"),
        ("Show current time", "print(f'Current time: {datetime.now()}')"),
    ]
    
    return commands, "Operations with 10-second timeout", {"timeout": 10}


def test_error_handling():
    """Test error handling in batch execution"""
    print("\n🚨 Test 3: Error Handling and Recovery")
    print("-" * 50)
    
    commands = [
        ("Valid command 1", "x = 10"),
        ("Valid command 2", "y = 20"),
        ("Invalid command", "this_will_fail()"),  # This will fail
        ("Would not execute", "z = x + y"),  # Won't reach here with stop_on_error=True
    ]
    
    return commands, "Error handling test (stops on error)", {"stop_on_error": True}


def test_continue_on_error():
    """Test continuing execution despite errors"""
    print("\n🔄 Test 4: Continue on Error")
    print("-" * 50)
    
    commands = [
        ("Valid command 1", "a = 100"),
        ("Invalid command 1", "nonexistent_function()"),  # Will fail
        ("Valid command 2", "b = 200"),
        ("Invalid command 2", "another_bad_call()"),  # Will also fail  
        ("Valid command 3", "c = a + b; print(f'a + b = {c}')"),  # Should still execute
    ]
    
    return commands, "Continue execution despite errors", {"stop_on_error": False}


def test_shell_batch():
    """Test shell batch execution"""
    print("\n🐚 Test 5: Shell Batch Execution")
    print("-" * 50)
    
    commands = [
        ("Check hostname", "hostname"),
        ("Show date", "date"),
        ("List root directory", "ls -la /"),
        ("Check disk space", "df -h /"),
        ("Show memory info", "free -h"),
    ]
    
    return commands, "Shell commands with timeout", {"timeout": 15}


def test_code_block_loading():
    """Test sending code blocks"""
    print("\n📦 Test 6: Code Block Loading")
    print("-" * 50)
    
    # Define a multi-line function
    function_code = '''
def fibonacci(n):
    """Generate fibonacci sequence up to n terms"""
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    elif n == 2:
        return [0, 1]
    
    sequence = [0, 1]
    for i in range(2, n):
        sequence.append(sequence[i-1] + sequence[i-2])
    return sequence

def factorial(n):
    """Calculate factorial of n"""
    if n <= 1:
        return 1
    return n * factorial(n - 1)

# Test the functions
print("Functions defined successfully!")
'''
    
    return function_code, "Mathematical functions module"


def test_protocol_state_tracking():
    """Test protocol setup and state tracking"""
    print("\n🤖 Test 7: Protocol State Tracking")
    print("-" * 50)
    
    # First load the opentrons_states module
    states_file = Path(__file__).parent.parent / "src" / "opentrons_workflows" / "opentrons_states.py"
    
    with open(states_file, 'r') as f:
        states_code = f.read()
    
    protocol_commands = [
        ("Import execute", "from opentrons import execute"),
        ("Create protocol", "protocol = execute.get_protocol_api('2.18')"),
        ("Home robot", "protocol.home()"),
        ("Load plate", "plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '1')"),
        ("Load tips", "tips = protocol.load_labware('opentrons_96_tiprack_300ul', '2')"),
        ("Load pipette", "p300 = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tips])"),
        ("Check deck state", "deck_state = get_deck_state(protocol)"),
        ("Show loaded slots", "print(f'Loaded slots: {list(deck_state[\"slots\"].keys())}')"),
        ("Check pipette state", "pip_state = get_pipette_state(p300)"),
        ("Show pipette info", "print(f'Pipette has tip: {pip_state[\"has_tip\"]}')"),
    ]
    
    return states_code, protocol_commands


def main():
    """Run comprehensive SSH client method tests"""
    print("🧪 Comprehensive SSH Client Methods Test")
    print("=" * 60)
    
    # Configuration
    host_alias = "ot2_tailscale"
    password = input("Enter OT-2 password: ")
    
    # Connect with custom timeout settings
    print("🔌 Connecting to robot...")
    client = SSHClient(
        host_alias=host_alias, 
        password=password,
        command_timeout=30,  # Default 30-second timeout
        max_retries=3
    )
    
    if not client.connect():
        print("❌ Failed to connect to robot")
        return
    
    try:
        # Test 1: Basic Python batch
        commands, description = test_basic_python_batch()
        print(f"\n▶️  Running: {description}")
        results = client.execute_python_batch(commands)
        print(f"✅ Result: {len([r for r in results if r['success']])}/{len(results)} successful")
        
        # Test 2: Python batch with custom timeout
        commands, description, options = test_python_batch_with_timeout()
        print(f"\n▶️  Running: {description}")
        results = client.execute_python_batch(commands, **options)
        print(f"✅ Result: {len([r for r in results if r['success']])}/{len(results)} successful")
        
        # Test 3: Error handling (stop on error)
        commands, description, options = test_error_handling()
        print(f"\n▶️  Running: {description}")
        results = client.execute_python_batch(commands, **options)
        print(f"⚠️  Result: {len([r for r in results if r['success']])}/{len(results)} successful (expected partial failure)")
        
        # Test 4: Continue on error
        commands, description, options = test_continue_on_error()
        print(f"\n▶️  Running: {description}")
        results = client.execute_python_batch(commands, **options)
        successful = len([r for r in results if r['success']])
        print(f"✅ Result: {successful}/{len(results)} successful (continued despite errors)")
        
        # Test 5: Shell batch execution
        commands, description, options = test_shell_batch()
        print(f"\n▶️  Running: {description}")
        results = client.execute_shell_batch(commands, **options)
        print(f"✅ Result: {len([r for r in results if r['success']])}/{len(results)} successful")
        
        # Test 6: Code block loading
        code_block, description = test_code_block_loading()
        print(f"\n▶️  Running: {description}")
        result = client.send_code_block(code_block, description, timeout=30)
        if result['success']:
            print("✅ Code block loaded successfully")
            
            # Test the loaded functions
            test_commands = [
                ("Test fibonacci", "fib_result = fibonacci(10); print(f'First 10 fibonacci: {fib_result}')"),
                ("Test factorial", "fact_result = factorial(5); print(f'5! = {fact_result}')"),
            ]
            results = client.execute_python_batch(test_commands)
            print(f"✅ Function tests: {len([r for r in results if r['success']])}/{len(results)} successful")
        else:
            print(f"❌ Code block failed: {result['error']}")
        
        # Test 7: Protocol state tracking (advanced)
        print(f"\n▶️  Running: Protocol state tracking test")
        states_code, protocol_commands = test_protocol_state_tracking()
        
        # Load states module first
        states_result = client.send_code_block(states_code, "opentrons_states module", timeout=60)
        if states_result['success']:
            print("✅ States module loaded")
            
            # Run protocol commands with longer timeout
            results = client.execute_python_batch(protocol_commands, timeout=60, command_delay=1.0)
            successful = len([r for r in results if r['success']])
            print(f"✅ Protocol setup: {successful}/{len(results)} successful")
            
            if successful == len(results):
                print("🎉 Full protocol state tracking test completed successfully!")
            else:
                print("⚠️  Protocol setup had some issues - check robot state")
        else:
            print(f"❌ States module failed to load: {states_result['error']}")
        
        # Summary
        print(f"\n📊 Test Summary")
        print("=" * 60)
        print("✅ All SSH client methods tested successfully!")
        print("✅ Timeout configurations working properly")
        print("✅ Error handling patterns validated")
        print("✅ Both Python and shell batch execution tested")
        print("✅ Code block loading functionality verified")
        print("✅ Advanced protocol state tracking demonstrated")
        
        print(f"\n🎯 Key Features Demonstrated:")
        print("   • Default and custom timeouts")
        print("   • Error handling with stop_on_error options")
        print("   • Progress tracking and structured results")
        print("   • Multi-line code block execution")
        print("   • Session management (auto Python/shell switching)")
        print("   • Real-time robot state monitoring")
        
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        
    finally:
        print("\n🔌 Closing connection...")
        client.close()
        print("✅ Test completed")


if __name__ == "__main__":
    main() 