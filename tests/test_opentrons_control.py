#!/usr/bin/env python3

"""
Test the fixed OpentronsControl class.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows import OpentronsControl
from opentrons_workflows.opentrons_sshclient import SSHClient, SessionState


def test_opentrons_control_invoke_method():
    """Test that the invoke method works correctly with the fixed implementation"""
    
    # Mock the SSHClient
    with patch('opentrons_workflows.opentrons_control.SSHClient') as mock_ssh_class:
        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.session_state = Mock()
        mock_client.session_state.value = "python"
        mock_client.execute_python_command = Mock(return_value=">>> test_output\n>>> ")
        mock_client.start_python_session = Mock()
        mock_client.connect = Mock(return_value=True)
        mock_ssh_class.return_value = mock_client
        
        # Create OpentronsControl instance (in simulation mode to avoid real connection)
        robot = OpentronsControl(host_alias="test", simulation=True)
        
        # Test the invoke method
        result = robot.invoke("print('Hello World')")
        
        # Verify that execute_python_command was called with our command (among others)
        calls = mock_client.execute_python_command.call_args_list
        call_commands = [call[0][0] for call in calls]
        assert "print('Hello World')" in call_commands
        assert result == ">>> test_output\n>>> "


def test_opentrons_control_invoke_switches_to_python():
    """Test that invoke switches to Python mode if not already in Python mode"""
    
    with patch('opentrons_workflows.opentrons_control.SSHClient') as mock_ssh_class:
        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.session_state = Mock()
        mock_client.session_state.value = "shell"  # Start in shell mode
        mock_client.execute_python_command = Mock(return_value=">>> test_output\n>>> ")
        mock_client.start_python_session = Mock()
        mock_client.connect = Mock(return_value=True)
        mock_ssh_class.return_value = mock_client
        
        robot = OpentronsControl(host_alias="test", simulation=True)
        
        # Test the invoke method
        result = robot.invoke("print('Hello World')")
        
        # Verify that start_python_session was called to switch modes
        assert mock_client.start_python_session.call_count >= 1
        calls = mock_client.execute_python_command.call_args_list
        call_commands = [call[0][0] for call in calls]
        assert "print('Hello World')" in call_commands


def test_opentrons_control_invoke_disconnected_client():
    """Test that invoke raises exception when client is not connected"""
    
    with patch('opentrons_workflows.opentrons_control.SSHClient') as mock_ssh_class:
        mock_client = Mock()
        mock_client.is_connected = True  # Connected during init
        mock_client.connect = Mock(return_value=True)
        mock_client.session_state = Mock()
        mock_client.session_state.value = "python"
        mock_client.execute_python_command = Mock(return_value=">>> test_output\n>>> ")
        mock_ssh_class.return_value = mock_client
        
        robot = OpentronsControl(host_alias="test", simulation=True)
        
        # Now disconnect
        mock_client.is_connected = False
        
        # Test that invoke raises exception when not connected
        with pytest.raises(Exception, match="SSH client is not connected"):
            robot.invoke("print('Hello World')")


def test_opentrons_control_basic_methods():
    """Test that basic OpentronsControl methods exist and work"""
    
    with patch('opentrons_workflows.opentrons_control.SSHClient') as mock_ssh_class:
        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.session_state = Mock()
        mock_client.session_state.value = "python"
        mock_client.execute_python_command = Mock(return_value=">>> mock_output\n>>> ")
        mock_client.connect = Mock(return_value=True)
        mock_client.close = Mock()
        mock_ssh_class.return_value = mock_client
        
        robot = OpentronsControl(host_alias="test", simulation=True)
        
        # Test basic method existence
        assert hasattr(robot, 'invoke')
        assert hasattr(robot, 'load_labware')
        assert hasattr(robot, 'load_instrument')
        assert hasattr(robot, 'home')
        assert hasattr(robot, 'close_session')
        
        # Test sample labware configuration
        sample_labware = {
            "nickname": "test_tips",
            "loadname": "opentrons_96_tiprack_300ul",
            "location": "1",
            "ot_default": True
        }
        
        # This should not raise an exception
        robot.load_labware(sample_labware)
        
        # Verify the underlying invoke was called for labware loading
        mock_client.execute_python_command.assert_called()


@pytest.mark.integration  
def test_opentrons_control_simulation_mode():
    """Test OpentronsControl in simulation mode"""
    
    with patch('opentrons_workflows.opentrons_control.SSHClient') as mock_ssh_class:
        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.session_state = Mock()
        mock_client.session_state.value = "python"
        mock_client.execute_python_command = Mock(return_value=">>> mock_output\n>>> ")
        mock_client.connect = Mock(return_value=True)
        mock_ssh_class.return_value = mock_client
        
        # Test simulation mode initialization
        robot = OpentronsControl(host_alias="test", simulation=True)
        
        # Verify simulation-specific calls were made
        calls = mock_client.execute_python_command.call_args_list
        call_commands = [call[0][0] for call in calls]
        
        # Should have simulation imports
        assert any("simulate" in cmd for cmd in call_commands)
        assert any("simulate.get_protocol_api" in cmd for cmd in call_commands)


if __name__ == "__main__":
    pytest.main([__file__, "-v"]) 