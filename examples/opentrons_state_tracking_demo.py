#!/usr/bin/env python3
"""
Opentrons State Tracking Demo

This example shows how to use the new opentrons_states module to query
the robot's built-in state tracking capabilities.

This approach leverages the Opentrons API's native state tracking rather
than maintaining separate state objects.
"""

import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows import (
    PipetteState,
    WellState,
    LabwareState, 
    DeckState,
    ModuleState,
    check_pipette_state,
    check_well_state,
    check_labware_state,
    check_deck_state
)


def demo_jupyter_style_state_tracking():
    """
    Demo showing how to use state tracking in Jupyter-style workflow.
    
    This simulates the step-by-step approach you'd use in a Jupyter notebook.
    """
    print("🔬 Opentrons State Tracking Demo")
    print("=" * 50)
    print()
    print("This demo shows how to query robot states using the new state tracking module.")
    print("In actual use, you would run these commands step-by-step in Jupyter.")
    print()
    
    # Simulate Jupyter notebook steps
    print("# Step 1: Import and initialize protocol")
    print("import opentrons.execute")
    print("protocol = opentrons.execute.get_protocol_api('2.18')")
    print("protocol.home()")
    print()
    
    print("# Step 2: Load labware")
    print("tip_rack = protocol.load_labware('opentrons_96_tiprack_300ul', 1)")
    print("plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 2)")
    print()
    
    print("# Step 3: Load pipette")
    print("pipette = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tip_rack])")
    print()
    
    # print("# Step 4: Define and load liquid")
    # print("sample_liquid = protocol.define_liquid(")
    # print("    name='Sample', description='Test sample', display_color='#FF0000')")
    # print()
    # print("# Load 100 µL into wells A1-A12")
    # print("for i in range(12):")
    # print("    well_name = f'A{i+1}'")
    # print("    plate.load_liquid([plate[well_name]], volume=100, liquid=sample_liquid)")
    # print()
    
    print("# Step 5: Import state tracking")
    print("from opentrons_workflows import PipetteState, LabwareState, DeckState, check_well_state")
    print()
    
    print("# Step 6: Create state tracking objects")
    print("pipette_state = PipetteState(pipette)")
    print("plate_state = LabwareState(plate)")
    print("tip_rack_state = LabwareState(tip_rack)")
    print("deck_state = DeckState(protocol)")
    print()
    
    print("# Step 7: Check initial states")
    print("print('Initial Deck State:')")
    print("print(deck_state.get_status_summary())")
    print()
    print("print('Tip Rack State:')")
    print("print(tip_rack_state.get_status_summary())")
    print()
    print("print('Plate State:')")
    print("print(plate_state.get_status_summary())")
    print()
    
    print("# Step 8: Perform operations and check states")
    print("# Pick up tip")
    print("pipette.pick_up_tip()")
    print("print(f'Pipette has tip: {pipette_state.has_tip}')")
    print("print(f'Current volume: {pipette_state.current_volume} µL')")
    print()
    
    # print("# Aspirate from A1")
    # print("pipette.aspirate(10, plate['A1'])")
    # print("print(f'After aspiration:')")
    # print("print(f'  Pipette volume: {pipette_state.current_volume} µL')")
    # print("print(f'  A1 remaining: {check_well_state(plate[\"A1\"])[\"current_liquid_volume\"]} µL')")
    # print()
    
    # print("# Dispense to waste")
    # print("pipette.dispense(10, pipette.trash_container.wells()[0])")
    # print("print(f'After dispense: {pipette_state.current_volume} µL')")
    # print()
    
    # print("# Drop tip")
    # print("pipette.drop_tip()")
    # print("print(f'Pipette has tip: {pipette_state.has_tip}')")
    # print()
    
    print("# Step 8.5: Check states after operations")
    print("print('Available tips:', tip_rack_state.get_available_tips()[:10])  # First 10")
    print("print('Used tips:', tip_rack_state.get_used_tips()[:10])  # First 10")
    print()
    print("print('Wells with liquid:', len(plate_state.get_wells_with_liquid()))")
    print()
    
    print("# Return tip")
    print("pipette.return_tip()")
    print("print(f'Pipette has tip: {pipette_state.has_tip}')")
    print()
    
    
    print("# Step 9: Check states after operations")
    print("print('Available tips:', tip_rack_state.get_available_tips()[:10])  # First 10")
    print("print('Used tips:', tip_rack_state.get_used_tips()[:10])  # First 10")
    print()
    print("print('Wells with liquid:', len(plate_state.get_wells_with_liquid()))")
    print()
    
    print("# Step 10: Advanced state queries")
    print("# Check specific wells")
    print("for well_name in ['A1', 'A2', 'A3']:")
    print("    well_status = check_well_state(plate[well_name])")
    print("    print(f'{well_name}: {well_status[\"current_liquid_volume\"]} µL')")
    print()
    
    print("# Check pipette capabilities")
    print("print(f'Pipette max volume: {pipette_state.max_volume} µL')")
    print("print(f'Next tip location: {pipette_state.starting_tip}')")
    print()
    
    print("# Step 11: Reset tip tracking if needed")
    print("tip_rack_state.reset_tip_tracking()  # Mark all tips as available")
    print("print('Tips after reset:', len(tip_rack_state.get_available_tips()))")
    print()
    
    print("# Step 9: Check states after operations")
    print("print('Available tips:', tip_rack_state.get_available_tips()[:10])  # First 10")
    print("print('Used tips:', tip_rack_state.get_used_tips()[:10])  # First 10")
    print()

def demo_mock_robot_states():
    """
    Demo with mock objects to show the API without requiring a robot.
    """
    print("\n" + "=" * 60)
    print("MOCK ROBOT STATE DEMO")
    print("=" * 60)
    print()
    
    print("This demo shows the state tracking API with mock objects.")
    print("In real use, these would be actual Opentrons protocol objects.")
    print()
    
    # Mock objects for demonstration
    class MockWell:
        def __init__(self, well_name, volume=0, has_tip=False):
            self.well_name = well_name
            self._volume = volume
            self._has_tip = has_tip
            self.max_volume = 200
            self.diameter = 6.86
            self.depth = 10.67
        
        @property
        def current_liquid_volume(self):
            return self._volume
        
        @property
        def current_liquid_height(self):
            return self._volume / 20  # Mock calculation
        
        @property
        def has_tip(self):
            return self._has_tip
    
    class MockPipette:
        def __init__(self):
            self.name = "p300_single_gen2"
            self.has_tip = False
            self.current_volume = 0
            self.max_volume = 300
            self.min_volume = 20
            self.starting_tip = None
    
    # Create mock objects
    mock_well_a1 = MockWell("A1", volume=90)  # After 10µL aspiration
    mock_well_a2 = MockWell("A2", volume=100)  # Untouched
    mock_tip_well = MockWell("A1", has_tip=True)  # Tip rack well
    mock_pipette = MockPipette()
    
    print("Creating state objects...")
    well_state_a1 = WellState(mock_well_a1)
    well_state_a2 = WellState(mock_well_a2)
    tip_well_state = WellState(mock_tip_well)
    pipette_state = PipetteState(mock_pipette)
    
    print("\nWell A1 Status (after aspiration):")
    status = well_state_a1.get_status_summary()
    for key, value in status.items():
        if key != 'timestamp':
            print(f"  {key}: {value}")
    
    print("\nWell A2 Status (untouched):")
    status = well_state_a2.get_status_summary()
    for key, value in status.items():
        if key != 'timestamp':
            print(f"  {key}: {value}")
    
    print("\nTip Well Status:")
    status = tip_well_state.get_status_summary()
    for key, value in status.items():
        if key != 'timestamp':
            print(f"  {key}: {value}")
    
    print("\nPipette Status:")
    status = pipette_state.get_status_summary()
    for key, value in status.items():
        if key != 'timestamp':
            print(f"  {key}: {value}")
    
    print("\nConvenience functions:")
    print(f"Quick well check: {check_well_state(mock_well_a1)['current_liquid_volume']} µL")
    print(f"Quick pipette check: {check_pipette_state(mock_pipette)['has_tip']}")


def demo_real_world_workflow():
    """
    Show how you'd use state tracking in a real protocol.
    """
    print("\n" + "=" * 60)
    print("REAL-WORLD WORKFLOW EXAMPLE")
    print("=" * 60)
    print()
    
    workflow_code = '''
# Real Jupyter notebook workflow with state tracking

# 1. Setup
import opentrons.execute
from opentrons_workflows import PipetteState, LabwareState, check_well_state

protocol = opentrons.execute.get_protocol_api("2.18")
protocol.home()

# 2. Load everything
tip_rack = protocol.load_labware('opentrons_96_tiprack_300ul', 1)
source_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 2)
dest_plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 3)
pipette = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tip_rack])

# 3. Initialize liquids
sample_liquid = protocol.define_liquid(name="Sample", description="Test", display_color="#FF0000")
for well_name in ['A1', 'A2', 'A3', 'A4']:
    source_plate.load_liquid([source_plate[well_name]], volume=100, liquid=sample_liquid)

# 4. Create state trackers
pipette_state = PipetteState(pipette)
source_state = LabwareState(source_plate)
dest_state = LabwareState(dest_plate)

# 5. Transfer workflow with state checking
transfer_volume = 50
source_wells = ['A1', 'A2', 'A3', 'A4']
dest_wells = ['B1', 'B2', 'B3', 'B4']

for source_well, dest_well in zip(source_wells, dest_wells):
    # Check source well has enough liquid
    source_status = check_well_state(source_plate[source_well])
    if source_status['current_liquid_volume'] < transfer_volume:
        print(f"Warning: {source_well} only has {source_status['current_liquid_volume']} µL")
        continue
    
    # Pick up tip
    pipette.pick_up_tip()
    print(f"Tip status: {pipette_state.has_tip}")
    
    # Aspirate
    pipette.aspirate(transfer_volume, source_plate[source_well])
    print(f"Aspirated {transfer_volume} µL, pipette now has {pipette_state.current_volume} µL")
    
    # Dispense
    pipette.dispense(transfer_volume, dest_plate[dest_well])
    print(f"Dispensed, pipette now has {pipette_state.current_volume} µL")
    
    # Drop tip
    pipette.drop_tip()
    
    # Check final volumes
    source_final = check_well_state(source_plate[source_well])
    dest_final = check_well_state(dest_plate[dest_well])
    print(f"Transfer complete: {source_well}({source_final['current_liquid_volume']} µL) -> {dest_well}({dest_final['current_liquid_volume']} µL)")

# 6. Final state summary
print("\\nFinal State Summary:")
print(f"Source plate wells with liquid: {len(source_state.get_wells_with_liquid())}")
print(f"Destination plate wells with liquid: {len(dest_state.get_wells_with_liquid())}")
print(f"Tips remaining: {len(source_state.get_available_tips())}")

# 7. Advanced queries
print("\\nAdvanced State Queries:")
print("Source plate liquid distribution:")
for well_status in source_state.get_wells_with_liquid():
    print(f"  {well_status['well_name']}: {well_status['current_liquid_volume']} µL")

print("\\nDestination plate liquid distribution:")
for well_status in dest_state.get_wells_with_liquid():
    print(f"  {well_status['well_name']}: {well_status['current_liquid_volume']} µL")
'''
    
    print("Here's how you'd use state tracking in a real protocol:")
    print(workflow_code)


if __name__ == "__main__":
    demo_jupyter_style_state_tracking()
    # demo_mock_robot_states()
    # demo_real_world_workflow()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print()
    print("✅ The new opentrons_states module provides:")
    print("   • Direct access to robot's built-in state tracking")
    print("   • No duplicate state management")
    print("   • Real-time state queries")
    print("   • Jupyter-friendly step-by-step workflow")
    print()
    print("🔧 Key classes:")
    print("   • PipetteState - Query pipette.has_tip, current_volume, etc.")
    print("   • WellState - Query well.current_liquid_volume, has_tip, etc.")
    print("   • LabwareState - Query all wells, tip tracking, etc.")
    print("   • DeckState - Query protocol.deck, loaded_labwares, etc.")
    print("   • ModuleState - Query module temperatures, speeds, etc.")
    print()
    print("🚀 Usage patterns:")
    print("   • Create state objects once, query anytime")
    print("   • Use convenience functions for quick checks")
    print("   • Leverage robot's native liquid/tip tracking")
    print("   • Perfect for Jupyter notebook workflows")
    print()
    print("📚 Next steps:")
    print("   • Try the examples in a Jupyter notebook")
    print("   • Connect to your robot and test state queries")
    print("   • Build protocols with real-time state checking") 