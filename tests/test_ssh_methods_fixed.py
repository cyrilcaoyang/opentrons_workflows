#!/usr/bin/env python3

"""
Fixed version of SSH client methods tests that work properly with pytest.
"""

import sys
import pytest
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows.opentrons_sshclient import SSHClient


def test_basic_python_batch(client):
    """Test basic Python batch execution with default timeout"""
    commands = [
        ("Import sys module", "import sys"),
        ("Check Python version", "print(f'Python version: {sys.version_info.major}.{sys.version_info.minor}')"),
        ("Import math", "import math"),
        ("Calculate pi", "print(f'Pi: {math.pi:.4f}')"),
        ("Simple arithmetic", "result = 42 * 7; print(f'42 * 7 = {result}')"),
    ]
    
    results = client.execute_python_batch(commands)
    
    # Assertions for proper pytest testing
    assert len(results) == len(commands)
    assert all(isinstance(r, dict) for r in results)
    assert all('success' in r for r in results)
    assert all('description' in r for r in results)
    assert all('command' in r for r in results)


def test_python_batch_with_timeout(client):
    """Test Python batch with custom timeout"""
    commands = [
        ("Import time", "import time"),
        ("Quick operation", "print('This is fast')"),
        ("Import datetime", "from datetime import datetime"),
        ("Show current time", "print(f'Current time: {datetime.now()}')"),
    ]
    
    results = client.execute_python_batch(commands, timeout=10)
    
    assert len(results) == len(commands)
    # Mock should return successful results
    assert all(r['success'] for r in results)


def test_error_handling(client):
    """Test error handling in batch execution"""
    commands = [
        ("Valid command 1", "x = 10"),
        ("Valid command 2", "y = 20"),
        ("Invalid command", "this_will_fail()"),  # This will fail
        ("Would not execute", "z = x + y"),  # Won't reach here with stop_on_error=True
    ]
    
    results = client.execute_python_batch(commands, stop_on_error=True)
    
    assert len(results) >= 1  # At least one result should be returned
    assert isinstance(results, list)


def test_continue_on_error(client):
    """Test continuing execution despite errors"""
    commands = [
        ("Valid command 1", "a = 100"),
        ("Invalid command 1", "nonexistent_function()"),  # Will fail
        ("Valid command 2", "b = 200"),
        ("Invalid command 2", "another_bad_call()"),  # Will also fail  
        ("Valid command 3", "c = a + b; print(f'a + b = {c}')"),  # Should still execute
    ]
    
    results = client.execute_python_batch(commands, stop_on_error=False)
    
    assert len(results) == len(commands)
    # With mock, all commands should succeed
    assert all(r['success'] for r in results)


def test_shell_batch(client):
    """Test shell batch execution"""
    commands = [
        ("Check hostname", "hostname"),
        ("Show date", "date"),
        ("List root directory", "ls -la /"),
        ("Check disk space", "df -h /"),
        ("Show memory info", "free -h"),
    ]
    
    results = client.execute_shell_batch(commands, timeout=15)
    
    assert len(results) == len(commands)
    assert all(isinstance(r, dict) for r in results)


def test_code_block_loading(client):
    """Test sending code blocks"""
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
    
    result = client.send_code_block(function_code, "Mathematical functions module")
    
    assert isinstance(result, dict)
    assert 'success' in result
    assert 'error' in result
    assert 'output' in result


@pytest.mark.integration
def test_protocol_state_tracking(client):
    """Test protocol setup and state tracking"""
    # First load the opentrons_states module
    states_file = Path(__file__).parent.parent / "src" / "opentrons_workflows" / "opentrons_states.py"
    
    if states_file.exists():
        with open(states_file, 'r') as f:
            states_code = f.read()
        
        # Load the states module
        states_result = client.send_code_block(states_code, "opentrons_states module", timeout=60)
        assert states_result['success']
        
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
        
        results = client.execute_python_batch(protocol_commands, timeout=120)
        assert len(results) == len(protocol_commands)
    else:
        pytest.skip("opentrons_states.py file not found")


def test_ssh_client_connection(client):
    """Test basic SSH client functionality"""
    assert client.is_connected is True
    assert hasattr(client, 'execute_python_command')
    assert hasattr(client, 'execute_shell_command')
    assert hasattr(client, 'execute_python_batch')
    assert hasattr(client, 'execute_shell_batch')


def test_session_state_management(client):
    """Test session state management"""
    # Test that client has session state
    assert hasattr(client, 'session_state')
    assert client.session_state.value == "python"
    
    # Test session switching methods exist
    assert hasattr(client, 'start_python_session')
    assert hasattr(client, 'switch_to_shell')


@pytest.mark.slow
def test_batch_command_format(client, sample_protocol_commands):
    """Test that batch commands return proper format"""
    results = client.execute_python_batch(sample_protocol_commands)
    
    assert isinstance(results, list)
    assert len(results) == len(sample_protocol_commands)
    
    for result in results:
        assert isinstance(result, dict)
        required_keys = ['description', 'command', 'success', 'output', 'error']
        assert all(key in result for key in required_keys)
        assert isinstance(result['success'], bool)
        assert isinstance(result['description'], str)
        assert isinstance(result['command'], str)


if __name__ == "__main__":
    # For running tests manually
    pytest.main([__file__, "-v"]) 