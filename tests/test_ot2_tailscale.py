"""
Pytest-based tests for verifying connection and basic functionality of an OT-2 robot.

NOTE: These tests are currently disabled as they use the old connect() function signature.
TODO: Update tests to use the new simplified connect(robot_ip, custom_logger=None) function.

These tests use the 'ot2_tailscale' alias and run in simulation mode.
They cover:
1.  Successful connection to the robot.
2.  Loading custom and standard labware.
3.  Loading a pipette.
4.  Performing a simple liquid transfer operation.

How to Run:
-------------
1. Make sure you have installed the development dependencies:
   pip install -e .[dev]

2. Run the tests from the project's root directory using:
   python -m pytest tests/test_ot2_tailscale_connection.py
"""

import sys
import os
import pytest

# Add the src directory to the path to allow importing opentrons_workflows
# This is necessary for running tests directly without installing the package.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from opentrons_workflows import connect
from opentrons_workflows.logging_config import get_logger

# Set up logging using unified logger
logger = get_logger(__name__)


@pytest.fixture(scope="module")
def robot():
    """
    Pytest fixture to establish a connection to the robot.

    This fixture creates a single robot connection for all tests in this module.
    It connects using the 'ot2_tailscale' alias in simulation mode.
    The connection is automatically closed after all tests in the module have run.
    """
    logger.info("Setting up robot connection for the test module...")
    robot_instance = None
    try:
        robot_instance = connect(host_alias="ot2_tailscale", simulation=True)
        yield robot_instance
    finally:
        if robot_instance:
            logger.info("Tearing down robot connection for the test module...")
            robot_instance.close()


def test_robot_connection(robot):
    """Verify that the robot connection fixture was created successfully."""
    logger.info("Test 1: Verifying robot connection...")
    assert robot is not None, "Failed to establish a connection to the robot."
    logger.info("✅ Robot connection successful.")


def test_load_labware(robot):
    """Test loading both custom and standard labware onto the deck."""
    logger.info("Test 2: Loading labware...")
    
    # Test loading custom labware
    plate = robot.deck.load_labware("matterlab_24_vialplate_3700ul", location="1")
    assert plate is not None, "Failed to load custom labware 'matterlab_24_vialplate_3700ul'."
    assert plate.load_name == "matterlab_24_vialplate_3700ul"
    logger.info("✅ Custom labware loaded successfully.")

    # Test loading standard Opentrons labware
    tip_rack = robot.deck.load_labware("opentrons_96_tiprack_300ul", location="2")
    assert tip_rack is not None, "Failed to load standard labware 'opentrons_96_tiprack_300ul'."
    assert tip_rack.load_name == "opentrons_96_tiprack_300ul"
    logger.info("✅ Standard labware loaded successfully.")


def test_load_pipette(robot):
    """Test loading a pipette onto the robot."""
    logger.info("Test 3: Loading pipette...")
    pipette = robot.load_pipette("p300_single_gen2", mount="right")
    assert pipette is not None, "Failed to load pipette 'p300_single_gen2'."
    assert "right" in robot.instruments
    logger.info("✅ Pipette loaded successfully.")


def test_liquid_transfer(robot):
    """
    Test a simple liquid transfer operation.
    
    Note: This test depends on the state from previous tests (loaded labware and pipette).
    For more complex scenarios, consider creating a fixture that provides a pre-configured robot.
    """
    logger.info("Test 4: Performing liquid transfer...")
    
    # Get the instruments and labware from the robot's state
    pipette = robot.instruments.get("right")
    plate = robot.deck.labware.get("1")
    
    assert pipette is not None, "Pipette not found for liquid transfer test."
    assert plate is not None, "Plate not found for liquid transfer test."

    # Perform the transfer
    pipette.pick_up_tip()
    pipette.aspirate(volume=50, location=plate["A1"])
    pipette.dispense(volume=50, location=plate["B1"])
    pipette.drop_tip()
    logger.info("✅ Liquid transfer completed successfully.") 