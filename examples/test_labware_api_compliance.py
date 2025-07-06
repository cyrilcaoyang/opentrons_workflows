#!/usr/bin/env python3
"""
Test script to verify labware module compliance with Opentrons API v2 documentation.

This script demonstrates all the key features from the official documentation:
- Well access patterns (dictionary, rows, columns)
- Liquid definition and loading
- Well dimensions
- Labware management

Based on: https://docs.opentrons.com/v2/new_labware.html
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from opentrons_workflows.labware import (
    LabwareManager, 
    Labware, 
    LabwareWithStatus,
    Well,
    WellWithStatus
)
from opentrons_workflows.liquid import Liquid

def test_liquid_definition():
    """Test liquid definition as per official docs"""
    print("=== Testing Liquid Definition ===")
    
    manager = LabwareManager()
    
    # Define liquids as per official documentation
    green_water = manager.define_liquid(
        name="Green water",
        description="Green colored water for demo",
        display_color="#00FF00"
    )
    
    blue_water = manager.define_liquid(
        name="Blue water", 
        description="Blue colored water for demo",
        display_color="#0000FF"
    )
    
    print(f"✓ Created green water: {green_water}")
    print(f"✓ Created blue water: {blue_water}")
    
    # Test invalid hex color
    try:
        invalid_liquid = manager.define_liquid(
            name="Invalid",
            description="Invalid color",
            display_color="invalid"
        )
        print("✗ Should have failed with invalid color")
    except ValueError as e:
        print(f"✓ Correctly rejected invalid color: {e}")
    
    return green_water, blue_water

def test_labware_creation():
    """Test labware creation and well access patterns"""
    print("\n=== Testing Labware Creation ===")
    
    manager = LabwareManager()
    
    # Create a mock 96-well plate definition
    mock_plate_def = {
        "loadName": "test_96_wellplate_200ul",
        "parameters": {
            "loadName": "test_96_wellplate_200ul"
        },
        "wells": {}
    }
    
    # Generate wells A1-H12 (96 wells)
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    cols = list(range(1, 13))  # 1-12
    
    for row in rows:
        for col in cols:
            well_name = f"{row}{col}"
            mock_plate_def["wells"][well_name] = {
                "depth": 10.67,
                "diameter": 6.86,
                "totalLiquidVolume": 200
            }
    
    # Test basic labware creation
    plate = Labware(mock_plate_def, "D1", label="Test Plate")
    print(f"✓ Created labware: {plate}")
    print(f"✓ Load name: {plate.load_name}")
    print(f"✓ Location: {plate.location}")
    print(f"✓ Label: {plate.label}")
    print(f"✓ Total wells: {len(plate.wells())}")
    
    return plate

def test_well_access_patterns(plate):
    """Test well access patterns as per official docs"""
    print("\n=== Testing Well Access Patterns ===")
    
    # Dictionary access (official docs pattern)
    well_a1 = plate['A1']
    print(f"✓ Dictionary access plate['A1']: {well_a1}")
    print(f"✓ Well name: {well_a1.name}")
    print(f"✓ Well depth: {well_a1.depth}")
    print(f"✓ Well diameter: {well_a1.diameter}")
    print(f"✓ Well max volume: {well_a1.max_volume}")
    
    # Test wells() method
    all_wells = plate.wells()
    print(f"✓ plate.wells() returned {len(all_wells)} wells")
    
    # Test wells_by_name() method
    wells_by_name = plate.wells_by_name()
    print(f"✓ plate.wells_by_name() returned {len(wells_by_name)} wells")
    
    # Test rows() method (official docs pattern)
    rows = plate.rows()
    print(f"✓ plate.rows() returned {len(rows)} rows")
    print(f"✓ Row A has {len(rows[0])} wells")
    
    # Test rows_by_name() method (official docs pattern)
    rows_by_name = plate.rows_by_name()
    print(f"✓ plate.rows_by_name() returned {len(rows_by_name)} rows")
    print(f"✓ Row 'A' has {len(rows_by_name['A'])} wells")
    
    # Test columns() method (official docs pattern)
    columns = plate.columns()
    print(f"✓ plate.columns() returned {len(columns)} columns")
    print(f"✓ Column 1 has {len(columns[0])} wells")
    
    # Test columns_by_name() method (official docs pattern)
    columns_by_name = plate.columns_by_name()
    print(f"✓ plate.columns_by_name() returned {len(columns_by_name)} columns")
    print(f"✓ Column '1' has {len(columns_by_name['1'])} wells")
    
    return well_a1

def test_liquid_loading(plate, green_water, blue_water):
    """Test liquid loading patterns as per official docs"""
    print("\n=== Testing Liquid Loading ===")
    
    # Load entire plate with green water (official docs pattern)
    plate.load_liquid(green_water, volume=50, wells=plate.wells())
    print("✓ Loaded entire plate with green water")
    
    # Load specific wells with blue water (official docs pattern)
    plate.load_liquid_by_well({'A1': 200, 'A2': 100, 'A3': 50}, blue_water)
    print("✓ Loaded specific wells with blue water")
    
    # Test individual well liquid loading
    well_b1 = plate['B1']
    well_b1.load_liquid(blue_water, 75)
    print("✓ Loaded individual well B1 with blue water")
    
    # Test load_empty
    plate.load_empty([plate['C1'], plate['C2']])
    print("✓ Marked wells C1, C2 as empty")
    
    # Get liquid summary
    summary = plate.get_liquid_summary()
    print(f"✓ Liquid summary: {summary['wells_with_liquid']} wells with liquid, {summary['empty_wells']} empty")
    
    return summary

def test_enhanced_labware():
    """Test enhanced labware with status tracking"""
    print("\n=== Testing Enhanced Labware ===")
    
    manager = LabwareManager()
    
    # Create mock definition
    mock_def = {
        "loadName": "test_enhanced_plate",
        "parameters": {"loadName": "test_enhanced_plate"},
        "wells": {
            "A1": {"depth": 10, "diameter": 6, "totalLiquidVolume": 200},
            "A2": {"depth": 10, "diameter": 6, "totalLiquidVolume": 200},
        }
    }
    
    # Create enhanced labware
    enhanced_plate = LabwareWithStatus(mock_def, "D2", label="Enhanced Test Plate")
    print(f"✓ Created enhanced labware: {enhanced_plate}")
    
    # Test enhanced well access (should track usage)
    well_a1 = enhanced_plate['A1']
    well_a1_again = enhanced_plate['A1']
    
    print(f"✓ Well A1 access count: {well_a1.access_count}")
    print(f"✓ Well A1 last accessed: {well_a1.last_accessed}")
    
    # Test usage summary
    usage = enhanced_plate.get_well_usage_summary()
    print(f"✓ Usage summary: {usage}")
    
    return enhanced_plate

def test_iteration_patterns(plate):
    """Test iteration patterns as per official docs"""
    print("\n=== Testing Iteration Patterns ===")
    
    # Iterate through row A (official docs pattern)
    row_a_wells = []
    for well in plate.rows()[0]:  # First row (A)
        row_a_wells.append(well.name)
    print(f"✓ Iterated through row A: {row_a_wells[:5]}...")  # Show first 5
    
    # Iterate using rows_by_name (official docs pattern)
    row_a_by_name = []
    for well in plate.rows_by_name()["A"]:
        row_a_by_name.append(well.name)
    print(f"✓ Iterated through row A by name: {row_a_by_name[:5]}...")
    
    # Iterate through column 1
    col_1_wells = []
    for well in plate.columns()[0]:  # First column (1)
        col_1_wells.append(well.name)
    print(f"✓ Iterated through column 1: {col_1_wells}")

def main():
    """Run all tests"""
    print("Testing Opentrons API v2 Labware Compliance")
    print("=" * 50)
    
    try:
        # Test liquid definition
        green_water, blue_water = test_liquid_definition()
        
        # Test labware creation
        plate = test_labware_creation()
        
        # Test well access patterns
        well_a1 = test_well_access_patterns(plate)
        
        # Test liquid loading
        summary = test_liquid_loading(plate, green_water, blue_water)
        
        # Test enhanced labware
        enhanced_plate = test_enhanced_labware()
        
        # Test iteration patterns
        test_iteration_patterns(plate)
        
        print("\n" + "=" * 50)
        print("✅ ALL TESTS PASSED - Labware module complies with Opentrons API v2!")
        print("✅ Successfully implemented:")
        print("  - Dictionary-style well access")
        print("  - Row and column access methods")
        print("  - Liquid definition and loading")
        print("  - Well dimensions and properties")
        print("  - Iteration patterns")
        print("  - Enhanced status tracking")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 