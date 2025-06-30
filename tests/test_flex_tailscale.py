"""
Pytest-based tests for verifying connection and basic functionality of a Flex robot.

These tests use the 'flex_tailscale' alias and run in simulation mode.
They cover:
1.  Successful connection to the robot.
2.  Loading labware and a gripper.
3.  Moving the gripper to a specified location.
4.  Performing grip and ungrip actions.

How to Run:
-------------
1. Make sure you have installed the development dependencies:
   pip install -e .[dev]

2. Run the tests from the project's root directory using:
   python -m pytest tests/test_flex_tailscale.py
"""

import sys
import os
import logging
import pytest

# Add the src directory to the path to allow importing opentrons_workflows
# This is necessary for running tests directly without installing the package.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from opentrons_workflows import connect

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def robot():
    """
    Pytest fixture to establish a connection to the robot.

    This fixture creates a single robot connection for all tests in this module.
    It connects using the 'flex_tailscale' alias in simulation mode.
    The connection is automatically closed after all tests in the module have run.
    """
    logger.info("Setting up robot connection for the test module...")
    robot_instance = None
    try:
        robot_instance = connect(host_alias="otflex_wifi", simulation=True)
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


def test_load_labware_and_gripper(robot):
    """Test loading labware and a gripper onto the robot."""
    logger.info("Test 2: Loading labware and gripper...")

    # Load labware
    plate = robot.deck.load_labware("corning_96_wellplate_360ul_flat", location="D1")
    assert plate is not None, "Failed to load labware 'corning_96_wellplate_360ul_flat'."
    assert plate.load_name == "corning_96_wellplate_360ul_flat"
    logger.info("✅ Labware loaded successfully.")

    # Load gripper
    gripper = robot.load_instrument("flex_gripper", mount="left")
    assert gripper is not None, "Failed to load gripper 'flex_gripper'."
    assert "left" in robot.instruments
    logger.info("✅ Gripper loaded successfully.")


def test_gripper_movement(robot):
    """Test moving the gripper to a specific location."""
    logger.info("Test 3: Moving gripper...")

    # Get the gripper and labware from the robot's state
    gripper = robot.instruments.get("left")
    plate = robot.deck.labware.get("D1")

    assert gripper is not None, "Gripper not found for movement test."
    assert plate is not None, "Plate not found for movement test."

    # Move gripper
    gripper.move_to(plate["A1"].top())
    logger.info("✅ Gripper moved successfully.")


def test_gripper_actions(robot):
    """Test grip and ungrip actions."""
    logger.info("Test 4: Performing grip and ungrip actions...")

    # Get the gripper from the robot's state
    gripper = robot.instruments.get("left")
    assert gripper is not None, "Gripper not found for actions test."

    # Perform grip and ungrip
    gripper.grip()
    gripper.ungrip()
    logger.info("✅ Grip and ungrip actions completed successfully.")
     