"""
Simple demo script to connect P300 pipette and home the robot.

This script demonstrates:
1. Loading a P300 single-channel pipette on the right mount
2. Homing the robot

**-- IMPORTANT --**
Before running, you MUST update the `ROBOT_ALIAS` variable below to match
the alias of your target robot in your `user_scripts/sshclient_settings.json` file.

How to Run:
-------------
1. Make sure you have installed the development dependencies:
   pip install -e .[dev]

2. Edit the ROBOT_ALIAS variable in this script.

3. Run the script from the project's root directory:
   python tests/test_pipette_tip_demo.py
"""

import sys
import os

# --- Configuration ---
# TODO: CHANGE THIS to the alias of your OT-2 in `user_scripts/sshclient_settings.json`
ROBOT_ALIAS = "ot2_tailscale"
# ---------------------

# Add the src directory to the path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from opentrons_workflows import connect
from opentrons_workflows.logging_config import get_logger

# Use the unified logger
logger = get_logger(__name__)


def run_simple_demo():
    """
    Connects to the robot, loads pipette, and homes.
    """
    robot = None  # Initialize robot to None
    logger.info(f"Starting simple pipette demo on robot: '{ROBOT_ALIAS}'")
    logger.info("Connecting to the robot in LIVE mode...")

    try:
        # Establish connection to the real robot
        robot = connect(host_alias=ROBOT_ALIAS, simulation=False)
        logger.info("✅ Connection successful.")

        # Execute simple sequence: load pipette and home
        logger.info("Loading pipette and homing...")
        
        # Create a single command that loads pipette and homes
        # Note: The system will wrap this in a try/except block with 8-space indentation
        simple_sequence = """        # Load pipette
        pipette = ctx.load_instrument('p300_single_gen2', 'right')
        
        # Home the robot
        ctx.home()"""
        
        # Execute the simple sequence as one protocol
        logger.info("Executing pipette loading and homing...")
        robot.execute_command(simple_sequence)
        
        logger.info("✅ Pipette loaded and robot homed successfully!")

    except Exception as e:
        logger.error(f"❌ An error occurred: {e}")
        # Add more detailed error logging
        import traceback
        traceback.print_exc()
        
    finally:
        # Ensure the connection is always closed
        if robot:
            logger.info("Closing connection to the robot...")
            robot.close()
            logger.info("✅ Connection closed.")


if __name__ == "__main__":
    run_simple_demo() 