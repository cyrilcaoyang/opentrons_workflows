#!/usr/bin/env python3
"""
Simple Usage Demo for Opentrons Workflows

This script demonstrates the simplified API for connecting to robots,
using the gripper, and logging configuration.
"""

import sys
from pathlib import Path

# Add the package to the path for demo purposes
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import opentrons_workflows

def demo_basic_connection():
    """Demo 1: Basic robot connection with automatic logging"""
    print("=== Demo 1: Basic Connection ===")
    
    # Most simple usage - automatic logging to /logs folder
    print("robot = opentrons_workflows.connect('192.168.1.100')")
    print("# Logs automatically stored in top-level logs/ folder")
    print("✓ Simple connection with default logging")

def demo_custom_logging():
    """Demo 2: Custom logging setup"""
    print("\n=== Demo 2: Custom Logging ===")
    
    # Create custom logger
    print("Creating custom logger...")
    logger = opentrons_workflows.create_custom_logger(
        name='demo_robot',
        log_file='demo_robot.log',
        console_output=True
    )
    
    logger.info("This is a demo log message")
    logger.warning("Demo warning message")
    
    print("robot = opentrons_workflows.connect('192.168.1.100', custom_logger=logger)")
    print("✓ Custom logger created and ready for use")

def demo_gripper_usage():
    """Demo 3: Gripper usage example"""
    print("\n=== Demo 3: Gripper Usage ===")
    
    print("Example Flex robot gripper usage:")
    print("""
# Connect to Flex robot
robot = opentrons_workflows.connect('192.168.1.100')

# Load labware
source_plate = robot.deck.load_labware("opentrons_24_tuberack_1500ul", "D1")
dest_location = "D2"

# Load gripper (Flex robots only)
gripper = robot.load_gripper()

# Move labware using gripper (correct Flex API)
gripper.move_labware(source_plate, dest_location)

# Legacy compatibility methods (still work)
gripper.grip()
gripper.ungrip()
""")
    print("✓ Gripper usage example shown")

def demo_requirements_issue():
    """Demo 4: Explain the requirements issue fix"""
    print("\n=== Demo 4: Flex Requirements Fix ===")
    
    print("❌ Old broken format (caused connection failures):")
    print("metadata = {'apiLevel': '2.15', 'robotType': 'OT-3 Standard'}")
    
    print("\n✅ New correct format (automatically handled):")
    print("metadata = {'apiLevel': '2.15'}")
    print("requirements = {'robotType': 'Flex'}")
    
    print("\n✓ Package automatically uses correct format for Flex robots")

def main():
    print("Opentrons Workflows - Simple Usage Demo")
    print("=" * 50)
    
    demo_basic_connection()
    demo_custom_logging()
    demo_gripper_usage()
    demo_requirements_issue()
    
    print("\n" + "=" * 50)
    print("Key Features:")
    print("✅ Simple connect(robot_ip) interface")
    print("✅ Automatic logging to /logs folder")
    print("✅ Custom logger support")
    print("✅ Working Flex gripper support")
    print("✅ Correct requirements format for Flex")
    print("✅ Legacy compatibility maintained")
    
    print("\nFor more details, see the README.md file.")

if __name__ == "__main__":
    main() 