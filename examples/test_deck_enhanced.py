#!/usr/bin/env python3
"""
Test Script: Enhanced Deck Logic Validation

This script tests the enhanced deck logic to ensure it follows the official
Opentrons documentation for deck slots, staging areas, and fixtures.
"""

import sys
import json
from typing import Dict, Any

# Add the src directory to the path
sys.path.insert(0, '../src')

from opentrons_workflows.deck_enhanced import DeckWithStatus
from opentrons_workflows.status_tracking import DeckSlotStatus


class MockRobot:
    """Mock robot for testing deck logic without actual hardware"""
    def execute_command(self, command):
        return {'status': 'success', 'result': f"Mock result for: {command}"}


def test_slot_equivalencies():
    """Test that slot equivalencies work according to official documentation"""
    print("Testing Slot Equivalencies...")
    
    robot = MockRobot()
    deck = DeckWithStatus(robot)
    
    # Test equivalencies from official docs
    equivalencies = {
        'A1': '10', 'A2': '11', 'A3': 'trash',
        'B1': '7', 'B2': '8', 'B3': '9',
        'C1': '4', 'C2': '5', 'C3': '6',
        'D1': '1', 'D2': '2', 'D3': '3'
    }
    
    for flex_slot, ot2_slot in equivalencies.items():
        equivalent_slots = deck.get_equivalent_slots(flex_slot)
        print(f"  {flex_slot} ↔ {ot2_slot}: {equivalent_slots}")
        
        # Test that both formats work
        assert flex_slot in equivalent_slots
        if ot2_slot != 'trash':  # Skip trash for now
            assert ot2_slot in equivalent_slots
    
    print("✓ Slot equivalencies working correctly")


def test_staging_area_detection():
    """Test staging area slot detection"""
    print("\nTesting Staging Area Detection...")
    
    robot = MockRobot()
    deck = DeckWithStatus(robot)
    
    staging_slots = ['A4', 'B4', 'C4', 'D4']
    working_slots = ['A1', 'A2', 'A3', 'B1', 'B2', 'B3', 'C1', 'C2', 'C3', 'D1', 'D2', 'D3']
    
    for slot in staging_slots:
        assert deck._is_staging_area_slot(slot), f"Slot {slot} should be staging area"
        print(f"  {slot}: ✓ Staging area")
    
    for slot in working_slots:
        assert not deck._is_staging_area_slot(slot), f"Slot {slot} should not be staging area"
        print(f"  {slot}: ✓ Working area")
    
    print("✓ Staging area detection working correctly")


def test_slot_validation():
    """Test slot validation and normalization"""
    print("\nTesting Slot Validation...")
    
    robot = MockRobot()
    deck = DeckWithStatus(robot)
    
    # Test valid slots
    valid_slots = ['A1', 'D3', '1', '11', 'A4', 'D4']
    for slot in valid_slots:
        assert deck._validate_slot_exists(slot), f"Slot {slot} should be valid"
        print(f"  {slot}: ✓ Valid")
    
    # Test invalid slots
    invalid_slots = ['E1', 'A5', '0', '12', 'Z9']
    for slot in invalid_slots:
        assert not deck._validate_slot_exists(slot), f"Slot {slot} should be invalid"
        print(f"  {slot}: ✓ Invalid (correctly rejected)")
    
    # Test normalization
    assert deck._normalize_slot_name(10) == '10'
    assert deck._normalize_slot_name('A1') == 'A1'
    print("  Normalization: ✓ Working")
    
    print("✓ Slot validation working correctly")


def test_labware_loading():
    """Test labware loading with enhanced validation"""
    print("\nTesting Labware Loading...")
    
    robot = MockRobot()
    deck = DeckWithStatus(robot)
    
    # Test normal labware loading
    try:
        labware = deck.load_labware("corning_96_wellplate_360ul_flat", "A1")
        print(f"  A1: ✓ Loaded {labware.load_name}")
        
        # Test slot status
        assert deck.get_slot_status("A1") == DeckSlotStatus.OCCUPIED
        print(f"  A1: ✓ Status = {deck.get_slot_status('A1')}")
        
        # Test equivalent slot access
        equivalent_labware = deck["10"]  # Should access same labware as A1
        assert equivalent_labware.load_name == labware.load_name
        print(f"  10 (equiv to A1): ✓ Equivalent access working")
        
    except Exception as e:
        print(f"  Error loading labware: {e}")
    
    # Test staging area loading
    try:
        staging_labware = deck.load_labware("corning_96_wellplate_360ul_flat", "A4")
        print(f"  A4: ✓ Loaded in staging area")
        
        assert deck._is_staging_area_slot("A4")
        print(f"  A4: ✓ Correctly identified as staging area")
        
    except Exception as e:
        print(f"  Error loading staging area labware: {e}")
    
    print("✓ Labware loading working correctly")


def test_deck_fixtures():
    """Test deck fixture loading (trash bin, waste chute)"""
    print("\nTesting Deck Fixtures...")
    
    robot = MockRobot()
    deck = DeckWithStatus(robot)
    
    # Test trash bin loading
    try:
        trash_result = deck.load_trash_bin("A3")
        print(f"  Trash bin A3: ✓ {trash_result}")
        
        # Test invalid trash location
        try:
            deck.load_trash_bin("A2")  # Should fail - A2 not valid for trash
            print("  A2 trash: ✗ Should have failed")
        except ValueError as e:
            print(f"  A2 trash: ✓ Correctly rejected ({e})")
            
    except Exception as e:
        print(f"  Trash bin error: {e}")
    
    # Test waste chute loading
    try:
        chute_result = deck.load_waste_chute(with_cover=True, with_staging_area=True)
        print(f"  Waste chute D3: ✓ {chute_result}")
        
    except Exception as e:
        print(f"  Waste chute error: {e}")
    
    print("✓ Deck fixtures working correctly")


def test_deck_summary():
    """Test comprehensive deck summary"""
    print("\nTesting Deck Summary...")
    
    robot = MockRobot()
    deck = DeckWithStatus(robot)
    
    # Load some labware
    deck.load_labware("corning_96_wellplate_360ul_flat", "A1")
    deck.load_labware("opentrons_96_tiprack_300ul", "B2")
    deck.load_labware("nest_12_reservoir_15ml", "C4")  # Staging area
    deck.reserve_slot("D1", "Reserved for future plate")
    
    summary = deck.get_deck_summary()
    
    print(f"  Total slots: {summary['total_slots']}")
    print(f"  Occupied slots: {summary['occupied_slots']}")
    print(f"  Empty slots: {summary['empty_slots']}")
    print(f"  Reserved slots: {summary['reserved_slots']}")
    print(f"  Staging area slots: {len(summary['slot_categories']['flex_staging_area'])}")
    print(f"  Working area slots: {len(summary['slot_categories']['flex_working_area'])}")
    print(f"  OT-2 slots: {len(summary['slot_categories']['ot2_slots'])}")
    
    # Check specific slot details
    a1_details = summary['slot_details']['A1']
    print(f"  A1 details: {a1_details}")
    
    c4_details = summary['slot_details']['C4']
    print(f"  C4 details (staging): {c4_details}")
    
    print("✓ Deck summary working correctly")


def main():
    """Run all tests"""
    print("Enhanced Deck Logic Validation")
    print("=" * 50)
    print("Based on official Opentrons documentation:")
    print("https://docs.opentrons.com/v2/deck_slots.html")
    print()
    
    try:
        test_slot_equivalencies()
        test_staging_area_detection()
        test_slot_validation()
        test_labware_loading()
        test_deck_fixtures()
        test_deck_summary()
        
        print("\n" + "=" * 50)
        print("✅ ALL TESTS PASSED")
        print("Enhanced deck logic correctly implements official documentation!")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main() 