#!/usr/bin/env python3
"""
Demo: Enhanced Status Tracking for Opentrons Components

This script demonstrates the comprehensive status tracking capabilities
of the enhanced deck and pipette classes.

Features demonstrated:
- Real-time status monitoring for all components
- Volume tracking for pipettes
- Deck slot management with usage statistics
- Error handling and recovery
- Status history and export capabilities

Note: Gripper functionality has been removed as it's not part of the standard Opentrons API.
"""

import sys
import json
import time
from datetime import datetime
from typing import Dict, Any

# Add the src directory to the path
sys.path.insert(0, '../src')

from opentrons_workflows.opentrons_control import OpentronsControl
from opentrons_workflows.pipette_enhanced import PipetteWithStatus
from opentrons_workflows.deck import DeckWithStatus
from opentrons_workflows.status_tracking import InstrumentStatus, TipStatus, DeckSlotStatus


def print_status_summary(component, title: str):
    """Print a formatted status summary for any component"""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    
    if hasattr(component, 'get_status_summary'):
        summary = component.get_status_summary()
        print(f"Component: {summary['component']}")
        print(f"Status: {summary['current_status']}")
        print(f"Last Updated: {summary.get('last_updated', 'Never')}")
        print(f"Total Events: {summary['total_events']}")
        
        if summary.get('metadata'):
            print(f"Metadata: {json.dumps(summary['metadata'], indent=2)}")
    
    # Component-specific details
    if hasattr(component, 'get_detailed_status'):
        details = component.get_detailed_status()
        print(f"\nDetailed Status:")
        for key, value in details.items():
            if key not in ['component', 'current_status', 'last_updated', 'total_events', 'metadata']:
                print(f"  {key}: {value}")


def demo_pipette_status_tracking(robot):
    """Demonstrate pipette status tracking"""
    print("\n" + "="*80)
    print("PIPETTE STATUS TRACKING DEMO")
    print("="*80)
    
    # Create enhanced pipette
    pipette = PipetteWithStatus(robot, "p300_single", "left")
    print_status_summary(pipette, "Pipette Initial Status")
    
    # Load a tip rack for demonstration
    deck = DeckWithStatus(robot)
    tip_rack = deck.load_labware("opentrons_96_tiprack_300ul", "1")
    
    print(f"\nTip Status: {pipette.tip_status}")
    print(f"Current Volume: {pipette.current_volume}μL")
    print(f"Available Volume: {pipette.available_volume}μL")
    
    # Pick up tip
    print("\n--- Picking up tip ---")
    try:
        pipette.pick_up_tip()
        print(f"Tip Status: {pipette.tip_status}")
        print_status_summary(pipette, "After Tip Pickup")
    except Exception as e:
        print(f"Error picking up tip: {e}")
    
    # Simulate aspiration
    print("\n--- Simulating aspiration ---")
    try:
        # Create a mock well location for demo
        well = tip_rack['A1']  # Using tip rack well as example
        pipette.aspirate(100, well)
        print(f"Current Volume: {pipette.current_volume}μL")
        print(f"Available Volume: {pipette.available_volume}μL")
        print_status_summary(pipette, "After Aspiration")
    except Exception as e:
        print(f"Error during aspiration: {e}")
    
    # Try to aspirate too much (should fail)
    print("\n--- Testing volume overflow protection ---")
    try:
        pipette.aspirate(250, well)  # This should fail
    except Exception as e:
        print(f"Expected error caught: {e}")
        print_status_summary(pipette, "After Volume Overflow Error")
    
    # Export status history
    print("\n--- Exporting pipette status history ---")
    history_json = pipette.status_tracker.export_history()
    print(f"Status history exported ({len(history_json)} characters)")
    
    return pipette


# Gripper functionality removed - not part of standard Opentrons API
# def demo_gripper_status_tracking(robot):
#     """Demonstrate gripper status tracking (Flex only)"""
#     print("\n" + "="*80)
#     print("GRIPPER STATUS TRACKING DEMO")
#     print("="*80)
#     
#     try:
#         # Create enhanced gripper
#         gripper = GripperWithStatus(robot)
#         print_status_summary(gripper, "Gripper Initial Status")
#         
#         print(f"\nJaw Status: {gripper.jaw_status}")
#         print(f"Jaw Width: {gripper.jaw_width}mm")
#         print(f"Is Gripping: {gripper.is_gripping}")
#         print(f"Holding Labware: {gripper.holding_labware}")
#         
#         # Check gripper hardware status
#         print("\n--- Checking gripper hardware status ---")
#         hw_status = gripper.check_gripper_status()
#         print(f"Hardware Status: {hw_status}")
#         
#         # Simulate gripping a labware
#         print("\n--- Simulating grip operation ---")
#         try:
#             gripper.grip(grip_force=75, labware_name="test_plate")
#             print(f"Jaw Status: {gripper.jaw_status}")
#             print(f"Holding Labware: {gripper.holding_labware}")
#             print_status_summary(gripper, "After Gripping")
#         except Exception as e:
#             print(f"Error during grip: {e}")
#         
#         # Simulate ungripping
#         print("\n--- Simulating ungrip operation ---")
#         try:
#             gripper.ungrip()
#             print(f"Jaw Status: {gripper.jaw_status}")
#             print(f"Holding Labware: {gripper.holding_labware}")
#             print_status_summary(gripper, "After Ungripping")
#         except Exception as e:
#             print(f"Error during ungrip: {e}")
#         
#         # Get detailed status
#         print("\n--- Detailed gripper status ---")
#         detailed = gripper.get_detailed_status()
#         print(json.dumps(detailed, indent=2, default=str))
#         
#         return gripper
#         
#     except Exception as e:
#         print(f"Gripper demo failed (likely OT-2, not Flex): {e}")
#         return None


def demo_deck_status_tracking(robot):
    """Demonstrate deck status tracking"""
    print("\n" + "="*80)
    print("DECK STATUS TRACKING DEMO")
    print("="*80)
    
    # Create enhanced deck
    deck = DeckWithStatus(robot)
    print_status_summary(deck, "Deck Initial Status")
    
    # Show initial deck summary
    print("\n--- Initial deck summary ---")
    summary = deck.get_deck_summary()
    print(json.dumps(summary, indent=2))
    
    # Reserve a slot
    print("\n--- Reserving slot 5 ---")
    deck.reserve_slot("5", "Reserved for future sample plate")
    print(f"Slot 5 status: {deck.get_slot_status('5')}")
    
    # Load some labware
    print("\n--- Loading labware ---")
    try:
        plate1 = deck.load_labware("corning_96_wellplate_360ul_flat", "2")
        print(f"Loaded plate at slot 2: {plate1.load_name}")
        
        plate2 = deck.load_labware("opentrons_96_tiprack_300ul", "3")
        print(f"Loaded tips at slot 3: {plate2.load_name}")
        
        print_status_summary(deck, "After Loading Labware")
    except Exception as e:
        print(f"Error loading labware: {e}")
    
    # Try to load labware in reserved slot (should fail)
    print("\n--- Testing slot reservation protection ---")
    try:
        deck.load_labware("corning_96_wellplate_360ul_flat", "5")
    except Exception as e:
        print(f"Expected error caught: {e}")
    
    # Access wells and track usage
    print("\n--- Testing well usage tracking ---")
    try:
        well_a1 = plate1['A1']
        well_b1 = plate1['B1']
        well_a1_again = plate1['A1']  # Access same well again
        
        usage = plate1.get_well_usage_summary()
        print(f"Well usage summary: {usage}")
        
        # Show well metadata
        print(f"Well A1 metadata: {well_a1.metadata}")
        print(f"Well A1 last accessed: {well_a1.last_accessed}")
    except Exception as e:
        print(f"Error accessing wells: {e}")
    
    # Get comprehensive deck summary
    print("\n--- Final deck summary ---")
    final_summary = deck.get_deck_summary()
    print(json.dumps(final_summary, indent=2))
    
    # Export deck state
    print("\n--- Exporting deck state ---")
    deck_state = deck.export_deck_state()
    print(f"Deck state exported ({len(deck_state)} characters)")
    
    return deck


def demo_integrated_workflow(robot):
    """Demonstrate integrated workflow with all components"""
    print("\n" + "="*80)
    print("INTEGRATED WORKFLOW DEMO")
    print("="*80)
    
    # Create all components
    deck = DeckWithStatus(robot)
    pipette = PipetteWithStatus(robot, "p300_single", "left")
    
    # Load labware
    source_plate = deck.load_labware("corning_96_wellplate_360ul_flat", "1")
    dest_plate = deck.load_labware("corning_96_wellplate_360ul_flat", "2")
    tip_rack = deck.load_labware("opentrons_96_tiprack_300ul", "3")
    
    print("All labware loaded successfully")
    
    # Simulate a simple transfer workflow
    print("\n--- Simulating liquid transfer workflow ---")
    try:
        # Pick up tip
        pipette.pick_up_tip()
        print(f"✓ Tip picked up - Status: {pipette.tip_status}")
        
        # Aspirate from source
        source_well = source_plate['A1']
        pipette.aspirate(50, source_well)
        print(f"✓ Aspirated 50μL - Current volume: {pipette.current_volume}μL")
        
        # Dispense to destination
        dest_well = dest_plate['A1']
        pipette.dispense(50, dest_well)
        print(f"✓ Dispensed 50μL - Current volume: {pipette.current_volume}μL")
        
        # Drop tip
        pipette.drop_tip()
        print(f"✓ Tip dropped - Status: {pipette.tip_status}")
        
        # Show final status
        print("\n--- Final component statuses ---")
        print(f"Pipette Status: {pipette.status}")
        print(f"Deck occupied slots: {len(deck.labware)}")
        
        # Show well usage
        source_usage = source_plate.get_well_usage_summary()
        dest_usage = dest_plate.get_well_usage_summary()
        print(f"Source plate usage: {source_usage['usage_percentage']:.1f}%")
        print(f"Destination plate usage: {dest_usage['usage_percentage']:.1f}%")
        
    except Exception as e:
        print(f"Error in workflow: {e}")
        print_status_summary(pipette, "Pipette Status After Error")


def main():
    """Main demo function"""
    print("Enhanced Status Tracking Demo")
    print("="*80)
    
    # Note: This demo shows the API without actually connecting to a robot
    print("Note: This demo shows the enhanced status tracking API")
    print("For actual robot connection, uncomment the connection code below")
    print()
    
    # Uncomment these lines to connect to a real robot:
    # robot = OpenTronsControl()
    # robot.connect()
    
    # For demo purposes, we'll use a mock robot object
    class MockRobot:
        def execute_command(self, command):
            return {'status': 'success', 'result': f"Mock result for: {command}"}
        
        def _load_pipette_on_robot(self, name, mount):
            print(f"Mock: Loading {name} on {mount}")
        
        # def _load_gripper_on_robot(self):
        #     print("Mock: Loading gripper")
    
    robot = MockRobot()
    
    print("Using mock robot for demonstration...")
    print("Replace with real robot connection for actual use")
    print()
    
    # Run individual demos
    try:
        pipette = demo_pipette_status_tracking(robot)
        # gripper = demo_gripper_status_tracking(robot)  # Removed - not part of standard API
        deck = demo_deck_status_tracking(robot)
        demo_integrated_workflow(robot)
        
        print("\n" + "="*80)
        print("DEMO COMPLETED SUCCESSFULLY")
        print("="*80)
        
        print("\nKey Features Demonstrated:")
        print("✓ Real-time status tracking for all components")
        print("✓ Volume tracking and overflow protection")
        print("✓ Tip status management")
        print("✓ Deck slot reservation and validation")
        print("✓ Well usage statistics")
        print("✓ Error handling and recovery")
        print("✓ Status history and export")
        print("✓ Integrated workflow monitoring")
        
    except Exception as e:
        print(f"\nDemo failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main() 