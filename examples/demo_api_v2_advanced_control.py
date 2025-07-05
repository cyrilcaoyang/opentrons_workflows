#!/usr/bin/env python3
"""
Demo script showing the PROPER Standard Protocol API v2 Advanced Control approach.

This script demonstrates how to use the standard Opentrons Protocol API v2
with Advanced Control (opentrons.execute.get_protocol_api()) for direct robot control.

Based on: https://docs.opentrons.com/v2/new_advanced_running.html

Key advantages:
1. Direct robot control (no simulation, real robot execution)
2. Single homing cycle (robot only homes once at the start)
3. Stateful execution (robot maintains state throughout protocol)
4. Familiar interface (same classes and methods you're used to)
5. Standard API underneath (full compatibility with Opentrons ecosystem)
"""

import sys
import os

# Add the src directory to the path for direct script execution
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from opentrons_workflows.opentrons_control_v2 import connect_v2
from opentrons_workflows.logging_config import get_logger

# Use the unified logger
logger = get_logger(__name__)


def demo_ot2_advanced_control():
    """
    Demonstrates the proper Advanced Control approach for OT-2.
    This will control the REAL robot directly!
    """
    logger.info("Starting OT-2 Advanced Control demo...")
    
    try:
        # Connect using the new Advanced Control approach
        # This uses opentrons.execute.get_protocol_api() underneath
        robot = connect_v2(robot_type='OT-2', api_level='2.18')
        
        # CRITICAL: Home the robot first (as per official documentation)
        logger.info("🏠 Homing robot (this should be your first command)...")
        robot.home()
        
        # Load labware using the familiar interface
        logger.info("Loading labware...")
        tip_rack = robot.deck.load_labware('opentrons_96_tiprack_300ul', '1')
        # plate = robot.deck.load_labware('corning_96_wellplate_360ul_flat', '2')
        
        # Load pipette with tip rack
        logger.info("Loading pipette...")
        pipette = robot.load_pipette('p300_single_gen2', 'right', tip_racks=[tip_rack])
        
        # Now perform multiple operations - NO ADDITIONAL HOMING!
        logger.info("Performing multiple operations with NO additional homing...")
        
        # Operation 1: Pick up tip and return it
        robot.comment("Operation 1: Pick up tip and return it")
        pipette.pick_up_tip()
        robot.delay(1, "Pausing with tip")
        pipette.return_tip()  # Return to original position
        
        # Operation 2: Transfer with new tip
        # robot.comment("Operation 2: Transfer A1 to B1")
        # pipette.pick_up_tip()
        # pipette.aspirate(100, plate['A1'])
        # pipette.dispense(100, plate['B1'])
        # pipette.drop_tip()
        
        # Operation 3: Another transfer
        robot.comment("Operation 3: Transfer A2 to B2")
        # pipette.pick_up_tip()
        # pipette.aspirate(150, plate['A2'])
        # pipette.dispense(150, plate['B2'])
        # pipette.drop_tip()
        
        # Operation 4: Mix operation
        # robot.comment("Operation 4: Mix B1")
        # pipette.pick_up_tip()
        # pipette.mix(3, 50, plate['B1'])
        # pipette.drop_tip()
        
        # Operation 5: High-level transfer
        # robot.comment("Operation 5: High-level transfer A3 to B3")
        # pipette.transfer(200, plate['A3'], plate['B3'], new_tip='always')
        
        robot.comment("All operations completed successfully!")
        logger.info("✅ All operations completed with only ONE homing cycle!")
        
    except Exception as e:
        logger.error(f"❌ Demo failed: {e}")
        logger.error("Make sure you're running this on an actual robot with Jupyter Notebook")
        logger.error("or via SSH with the robot server running.")
        raise


def demo_comparison_with_your_working_notebook():
    """
    Shows how this approach compares to your working notebook example.
    """
    logger.info("=" * 70)
    logger.info("COMPARISON: Your Working Notebook vs Our Wrapper")
    logger.info("=" * 70)
    
    logger.info("\n📓 YOUR WORKING NOTEBOOK APPROACH:")
    logger.info("   import opentrons.execute")
    logger.info("   protocol = opentrons.execute.get_protocol_api('2.18')")
    logger.info("   protocol.home()")
    logger.info("   tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', '1')")
    logger.info("   pipette = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack])")
    logger.info("   pipette.pick_up_tip()")
    logger.info("   pipette.return_tip()")
    
    logger.info("\n🎯 OUR WRAPPER APPROACH:")
    logger.info("   from opentrons_workflows.opentrons_control_v2 import connect_v2")
    logger.info("   robot = connect_v2(robot_type='OT-2', api_level='2.18')")
    logger.info("   robot.home()")
    logger.info("   tiprack = robot.deck.load_labware('opentrons_96_tiprack_300ul', '1')")
    logger.info("   pipette = robot.load_pipette('p300_single_gen2', 'right', tip_racks=[tiprack])")
    logger.info("   pipette.pick_up_tip()")
    logger.info("   pipette.return_tip()")
    
    logger.info("\n🔍 KEY DIFFERENCES:")
    logger.info("   ✅ SAME: Uses opentrons.execute.get_protocol_api() underneath")
    logger.info("   ✅ SAME: Direct robot control, no simulation")
    logger.info("   ✅ SAME: Single homing cycle, stateful execution")
    logger.info("   ✅ ADDED: Familiar Deck, Pipette, Gripper classes")
    logger.info("   ✅ ADDED: Enhanced logging and error handling")
    logger.info("   ✅ ADDED: Easy migration from your existing SSH code")
    logger.info("   ✅ ADDED: Consistent interface across robot types")
    
    logger.info("\n🎉 BEST OF BOTH WORLDS:")
    logger.info("   - Standard Opentrons API efficiency")
    logger.info("   - Your familiar class structure")
    logger.info("   - Easy migration path")
    logger.info("   - Enhanced features and logging")
    logger.info("=" * 70)


def demo_jupyter_notebook_usage():
    """
    Shows how to use this in Jupyter notebooks.
    """
    logger.info("=" * 50)
    logger.info("JUPYTER NOTEBOOK USAGE")
    logger.info("=" * 50)
    
    logger.info("\n📓 In your Jupyter notebook cells:")
    logger.info("")
    logger.info("# Cell 1: Import and connect")
    logger.info("from opentrons_workflows.opentrons_control_v2 import get_protocol_api_v2")
    logger.info("robot = get_protocol_api_v2(api_level='2.18', robot_type='OT-2')")
    logger.info("# This automatically homes the robot!")
    logger.info("")
    logger.info("# Cell 2: Load labware")
    logger.info("tip_rack = robot.deck.load_labware('opentrons_96_tiprack_300ul', '1')")
    logger.info("plate = robot.deck.load_labware('corning_96_wellplate_360ul_flat', '2')")
    logger.info("")
    logger.info("# Cell 3: Load pipette")
    logger.info("pipette = robot.load_pipette('p300_single_gen2', 'right', tip_racks=[tip_rack])")
    logger.info("")
    logger.info("# Cell 4: Execute commands (run individually!)")
    logger.info("pipette.pick_up_tip()")
    logger.info("")
    logger.info("# Cell 5: More commands")
    logger.info("pipette.aspirate(100, plate['A1'])")
    logger.info("pipette.dispense(100, plate['B1'])")
    logger.info("")
    logger.info("# Cell 6: Clean up")
    logger.info("pipette.drop_tip()")
    logger.info("")
    logger.info("🎯 Each cell executes immediately on the robot!")
    logger.info("🏠 Robot only homes once at the beginning!")
    logger.info("=" * 50)


if __name__ == "__main__":
    print("🤖 Opentrons API v2 Advanced Control Demo")
    print("This demo shows the PROPER standard Protocol API v2 approach")
    print("using opentrons.execute.get_protocol_api() for direct robot control.")
    print("")
    print("⚠️  WARNING: This will control a REAL robot!")
    print("   Make sure you're running this on an actual Opentrons robot")
    print("   via Jupyter Notebook or SSH.")
    print("")
    
    try:
        # Show comparison
        demo_comparison_with_your_working_notebook()
        
        # Show Jupyter usage
        demo_jupyter_notebook_usage()
        
        # Ask for confirmation before running on real robot
        response = input("\nDo you want to run the actual robot demo? (y/N): ")
        if response.lower() == 'y':
            print("\n" + "="*50)
            print("🤖 Running OT-2 Advanced Control Demo")
            print("="*50)
            demo_ot2_advanced_control()
            
            print("\n" + "="*50)
            print("✅ Demo completed successfully!")
            print("Notice: Only ONE homing cycle!")
            print("="*50)
        else:
            print("\n✅ Demo information displayed. No robot commands executed.")
        
    except Exception as e:
        logger.error(f"❌ Demo script failed: {e}")
        sys.exit(1) 