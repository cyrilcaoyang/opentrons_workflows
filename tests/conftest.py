"""
pytest configuration file with fixtures for opentrons_workflows testing.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, MagicMock

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows.opentrons_sshclient import SSHClient, SessionState


@pytest.fixture
def mock_ssh_client():
    """Create a mock SSH client for testing without actual robot connection"""
    client = Mock(spec=SSHClient)
    client.is_connected = True
    client.session_state = Mock()
    client.session_state.value = "python"
    
    # Mock common methods
    client.execute_python_command = Mock(return_value=">>> mock_output\n>>> ")
    client.execute_shell_command = Mock(return_value="# mock_output\n# ")
    
    def mock_python_batch(commands, **kwargs):
        return [
            {'description': desc, 'command': cmd, 'success': True, 'output': 'mock', 'error': ''}
            for desc, cmd in commands
        ]
    
    def mock_shell_batch(commands, **kwargs):
        return [
            {'description': desc, 'command': cmd, 'success': True, 'output': 'mock', 'error': ''}
            for desc, cmd in commands
        ]
    
    client.execute_python_batch = Mock(side_effect=mock_python_batch)
    client.execute_shell_batch = Mock(side_effect=mock_shell_batch)
    client.send_code_block = Mock(return_value={'success': True, 'error': '', 'output': 'mock'})
    client.start_python_session = Mock(return_value=True)
    client.switch_to_shell = Mock(return_value=True)
    client.connect = Mock(return_value=True)
    client.close = Mock()
    
    return client


@pytest.fixture
def client(mock_ssh_client):
    """Alias for mock_ssh_client to match existing test expectations"""
    return mock_ssh_client


@pytest.fixture
def simulation_client():
    """Create a real SSH client in simulation mode for integration testing"""
    try:
        # This would create a real client but in simulation mode
        # For now, return mock since we don't have robot hardware in CI
        return mock_ssh_client()
    except Exception:
        # Fallback to mock if simulation setup fails
        return mock_ssh_client()


@pytest.fixture(scope="session")
def test_data_dir():
    """Directory containing test data files"""
    return Path(__file__).parent / "data"


@pytest.fixture
def sample_labware_config():
    """Sample labware configuration for testing"""
    return {
        "nickname": "test_tips",
        "loadname": "opentrons_96_tiprack_300ul",
        "location": "1",
        "ot_default": True,
        "config": {}
    }


@pytest.fixture
def sample_instrument_config():
    """Sample instrument configuration for testing"""
    return {
        "nickname": "test_p300",
        "instrument_name": "p300_single_gen2",
        "mount": "right",
        "ot_default": True,
        "config": {}
    }


@pytest.fixture
def sample_protocol_commands():
    """Sample protocol commands for batch testing"""
    return [
        ("Import sys", "import sys"),
        ("Check version", "print(f'Python {sys.version_info.major}.{sys.version_info.minor}')"),
        ("Simple math", "result = 2 + 2; print(f'2 + 2 = {result}')"),
        ("Set variable", "test_var = 'Hello, Robot!'"),
        ("Print variable", "print(test_var)")
    ]


# Configure pytest options
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test requiring robot connection"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on naming conventions"""
    for item in items:
        # Mark integration tests
        if "integration" in item.name or "robot" in item.name:
            item.add_marker(pytest.mark.integration)
        
        # Mark slow tests
        if "batch" in item.name or "workflow" in item.name:
            item.add_marker(pytest.mark.slow) 