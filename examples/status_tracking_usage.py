#!/usr/bin/env python3
"""
Simple Usage Example: Enhanced Status Tracking

This example shows how to use the enhanced classes with status tracking
in a real workflow scenario.
"""

from opentrons_workflows import (
    OpentronsControl,
    PipetteWithStatus,
    DeckWithStatus,
    InstrumentStatus,
    TipStatus,
    DeckSlotStatus
)


def simple_liquid_transfer_with_status():
    """Example: Simple liquid transfer with comprehensive status tracking"""
    
    # Connect to robot
    robot = OpentronsControl()
    robot.connect()
    
    # Create enhanced components with status tracking
    deck = DeckWithStatus(robot)
    pipette = PipetteWithStatus(robot, "p300_single", "left")
    
    print(f"Initial pipette status: {pipette.status}")
    print(f"Initial deck status: {deck.status}")
    
    # Load labware with status tracking
    source_plate = deck.load_labware("corning_96_wellplate_360ul_flat", "1")
    dest_plate = deck.load_labware("corning_96_wellplate_360ul_flat", "2")
    tip_rack = deck.load_labware("opentrons_96_tiprack_300ul", "3")
    
    print(f"Deck summary: {deck.get_deck_summary()}")
    
    # Perform liquid transfer with automatic status tracking
    try:
        # Pick up tip (automatically tracks tip status)
        pipette.pick_up_tip()
        print(f"Tip status: {pipette.tip_status}")
        
        # Aspirate (automatically tracks volume)
        source_well = source_plate['A1']
        pipette.aspirate(100, source_well)
        print(f"Current volume: {pipette.current_volume}μL")
        
        # Dispense (automatically updates volume)
        dest_well = dest_plate['A1']
        pipette.dispense(100, dest_well)
        print(f"Remaining volume: {pipette.current_volume}μL")
        
        # Drop tip (automatically resets tip status and volume)
        pipette.drop_tip()
        print(f"Final tip status: {pipette.tip_status}")
        
        # Export status history
        pipette_history = pipette.status_tracker.export_history("pipette_history.json")
        deck_state = deck.export_deck_state("deck_state.json")
        
        print("Status tracking data exported successfully!")
        
    except Exception as e:
        print(f"Error occurred: {e}")
        print(f"Pipette status: {pipette.status}")
        
        # Status tracking helps with debugging
        if pipette.status == InstrumentStatus.ERROR:
            print("Pipette is in error state - check status history")
    
    finally:
        # Always show final status
        print(f"\nFinal Status Summary:")
        print(f"Pipette: {pipette.get_status_summary()}")
        print(f"Deck: {deck.get_deck_summary()}")


# Gripper functionality removed - not part of standard Opentrons API
# def gripper_workflow_with_status():
#     """Example: Gripper workflow with status tracking (Flex only)"""
#     
#     # Connect to Flex robot
#     robot = OpenTronsControl()
#     robot.connect()
#     
#     # Create enhanced gripper with status tracking
#     gripper = GripperWithStatus(robot)
#     deck = DeckWithStatus(robot)
#     
#     print(f"Initial gripper status: {gripper.status}")
#     print(f"Jaw status: {gripper.jaw_status}")
#     
#     # Load labware to move
#     source_plate = deck.load_labware("corning_96_wellplate_360ul_flat", "A1")
#     
#     try:
#         # Move to labware location
#         gripper.move_to(source_plate.location)
#         
#         # Grip labware (automatically tracks grip status)
#         gripper.grip(grip_force=50, labware_name="source_plate")
#         print(f"Gripping status: {gripper.is_gripping}")
#         print(f"Holding: {gripper.holding_labware}")
#         
#         # Move to new location
#         gripper.move_to("B1")
#         
#         # Release labware (automatically updates status)
#         gripper.ungrip()
#         print(f"Released labware: {gripper.holding_labware}")
#         
#         # Get detailed status
#         detailed_status = gripper.get_detailed_status()
#         print(f"Detailed gripper status: {detailed_status}")
#         
#     except Exception as e:
#         print(f"Gripper error: {e}")
#         print(f"Gripper status: {gripper.status}")


def error_handling_example():
    """Example: How status tracking helps with error handling"""
    
    robot = OpentronsControl()
    robot.connect()
    
    pipette = PipetteWithStatus(robot, "p300_single", "left")
    
    try:
        # Try to aspirate without a tip (should fail)
        pipette.aspirate(100, "mock_well")
        
    except RuntimeError as e:
        print(f"Expected error caught: {e}")
        print(f"Pipette status: {pipette.status}")
        
        # Check status history to understand what happened
        history = pipette.status_tracker.status_history
        latest_event = history[-1]
        print(f"Latest status event: {latest_event.message}")
        
        # Status tracking provides context for debugging
        if pipette.tip_status == TipStatus.NO_TIP:
            print("Solution: Pick up a tip before aspirating")


if __name__ == "__main__":
    print("Enhanced Status Tracking Usage Examples")
    print("="*50)
    
    # Note: Uncomment the examples you want to run
    # simple_liquid_transfer_with_status()
    # gripper_workflow_with_status()
    # error_handling_example()
    
    print("\nTo run these examples:")
    print("1. Connect to your robot")
    print("2. Uncomment the desired example function")
    print("3. Run the script")
    print("\nStatus tracking provides:")
    print("✓ Real-time component status")
    print("✓ Automatic error detection")
    print("✓ Volume and tip tracking")
    print("✓ Deck slot management")
    print("✓ Status history for debugging")
    print("✓ Export capabilities") 