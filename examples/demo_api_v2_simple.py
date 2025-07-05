#!/usr/bin/env python3
"""
Demo script showing the new Standard Protocol API v2 approach.

This script demonstrates how to use the standard Opentrons Protocol API v2
while maintaining the familiar class structure (Deck, Pipette, Gripper).

Key advantages of this approach:
1. Single homing cycle (robot only homes once at the start)
2. Stateful execution (robot maintains state throughout protocol)
3. Familiar interface (same classes and methods you're used to)
4. Standard API underneath (full compatibility with Opentrons ecosystem)
"""

import sys
import os

# Add the src directory to the path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from opentrons_workflows.opentrons_control_v2 import connect_v2
from opentrons_workflows.logging_config import get_logger

# Use the unified logger
logger = get_logger(__name__)


def demo_ot2_basic_transfer():
    """
    Demonstrates basic liquid transfer using the new API v2 approach.
    This will only home once at the beginning!
    """
    logger.info("Starting OT-2 basic transfer demo with API v2...")
    
    # Connect using the new API v2 approach
    robot = connect_v2(robot_type='OT-2', simulation=True)
    
    try:
        # Load labware using the familiar interface
        plate = robot.deck.load_labware('corning_96_wellplate_360ul_flat', '1')
        tip_rack = robot.deck.load_labware('opentrons_96_tiprack_300ul', '2')
        
        # Load pipette with tip rack
        pipette = robot.load_pipette('p300_single_gen2', 'right', tip_racks=[tip_rack])
        
        # Robot homes once here (automatically when protocol starts)
        logger.info("Robot will home once now...")
        
        # Perform multiple operations - NO ADDITIONAL HOMING!
        logger.info("Performing multiple transfers with no additional homing...")
        
        # Transfer 1: A1 to B1
        robot.comment("Transfer 1: A1 to B1")
        pipette.pick_up_tip()
        pipette.aspirate(100, plate['A1'])
        pipette.dispense(100, plate['B1'])
        pipette.drop_tip()
        
        # Transfer 2: A2 to B2
        robot.comment("Transfer 2: A2 to B2")
        pipette.pick_up_tip()
        pipette.aspirate(150, plate['A2'])
        pipette.dispense(150, plate['B2'])
        pipette.drop_tip()
        
        # Transfer 3: Using the high-level transfer method
        robot.comment("Transfer 3: Using high-level transfer method")
        pipette.transfer(200, plate['A3'], plate['B3'], new_tip='always')
        
        # Mix operation
        robot.comment("Mixing B1")
        pipette.pick_up_tip()
        pipette.mix(3, 50, plate['B1'])
        pipette.drop_tip()
        
        logger.info("✅ All operations completed with only ONE homing cycle!")
        
    except Exception as e:
        logger.error(f"❌ Demo failed: {e}")
        raise
    
    finally:
        # Note: In simulation mode, there's no connection to close
        logger.info("Demo completed.")


def demo_flex_with_gripper():
    """
    Demonstrates Flex robot with gripper using the new API v2 approach.
    """
    logger.info("Starting Flex gripper demo with API v2...")
    
    # Connect to Flex robot
    robot = connect_v2(robot_type='Flex', simulation=True)
    
    try:
        # Load labware
        plate = robot.deck.load_labware('corning_96_wellplate_360ul_flat', 'A1')
        tip_rack = robot.deck.load_labware('opentrons_flex_96_tiprack_300ul', 'A2')
        
        # Load instruments
        pipette = robot.load_pipette('flex_1channel_300', 'left', tip_racks=[tip_rack])
        gripper = robot.load_gripper()
        
        # Robot homes once here
        logger.info("Robot will home once now...")
        
        # Demonstrate gripper and pipette operations
        robot.comment("Demonstrating gripper operations")
        
        # Gripper operations (these don't cause additional homing)
        robot.comment("Gripping plate")
        gripper.grip()
        
        robot.comment("Moving plate")
        robot.delay(2, "Simulating plate movement")
        
        robot.comment("Releasing plate")
        gripper.ungrip()
        
        # Pipette operations (these don't cause additional homing)
        robot.comment("Pipette operations")
        pipette.pick_up_tip()
        pipette.aspirate(100, plate['A1'])
        pipette.dispense(100, plate['B1'])
        pipette.drop_tip()
        
        logger.info("✅ Flex demo completed with only ONE homing cycle!")
        
    except Exception as e:
        logger.error(f"❌ Flex demo failed: {e}")
        raise
    
    finally:
        logger.info("Flex demo completed.")


def demo_comparison():
    """
    Shows the key differences between the old SSH approach and new API v2 approach.
    """
    logger.info("=" * 60)
    logger.info("COMPARISON: Old SSH vs New API v2 Approach")
    logger.info("=" * 60)
    
    logger.info("OLD SSH APPROACH:")
    logger.info("- Robot homes after EVERY command")
    logger.info("- Each operation regenerates entire protocol")
    logger.info("- Stateless execution")
    logger.info("- Multiple file transfers and executions")
    logger.info("")
    
    logger.info("NEW API v2 APPROACH:")
    logger.info("- Robot homes ONCE at the beginning")
    logger.info("- Maintains state throughout protocol")
    logger.info("- All operations in single protocol execution")
    logger.info("- Much faster and more efficient")
    logger.info("")
    
    logger.info("FAMILIAR INTERFACE MAINTAINED:")
    logger.info("- Same Deck, Pipette, Gripper classes")
    logger.info("- Same method names and signatures")
    logger.info("- Same logging and error handling")
    logger.info("- Easy migration from existing code")
    logger.info("=" * 60)


if __name__ == "__main__":
    print("🚀 Opentrons API v2 Demo")
    print("This demo shows the new standard Protocol API v2 approach")
    print("with the familiar class interface you're used to.")
    print("")
    
    try:
        # Show comparison
        demo_comparison()
        
        # Run OT-2 demo
        print("\n" + "="*50)
        print("🤖 OT-2 Basic Transfer Demo")
        print("="*50)
        demo_ot2_basic_transfer()
        
        # Run Flex demo
        print("\n" + "="*50)
        print("🦾 Flex Gripper Demo")
        print("="*50)
        demo_flex_with_gripper()
        
        print("\n" + "="*50)
        print("✅ All demos completed successfully!")
        print("Notice: Only ONE homing cycle per demo!")
        print("="*50)
        
    except Exception as e:
        logger.error(f"❌ Demo script failed: {e}")
        sys.exit(1) 