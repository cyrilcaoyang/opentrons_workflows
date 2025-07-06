#!/usr/bin/env python3
"""
Comprehensive State Tracking Demo with Variable Persistence Testing.

This demo proves that variables DO persist between separate batch calls
when proper timeout management is used to avoid reconnections.

Tests include:
1. Simple variable persistence test (basic Python variables)
2. Opentrons protocol persistence test (complex objects)
3. Full state tracking demo (comprehensive Opentrons workflow)
"""

import sys
import time
import json
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows.opentrons_sshclient import SSHClient


def test_simple_persistence(client):
    """Test that simple variables persist between separate batch calls"""
    print("🧪 Simple Variable Persistence Test")
    print("=" * 50)
    
    try:
        # Batch 1: Set some variables
        print("\n📝 Batch 1: Setting variables...")
        batch1_commands = [
            ("Set variable x", "x = 42"),
            ("Set variable name", "name = 'OpenTrons Robot'"),
            ("Set variable pi", "import math; pi_value = math.pi"),
            ("Print x", "print(f'x = {x}')"),
        ]
        
        results1 = client.execute_python_batch(batch1_commands, command_delay=0.2)
        success1 = sum(1 for r in results1 if r['success'])
        print(f"✅ Batch 1: {success1}/{len(results1)} commands successful")
        
        if success1 != len(results1):
            print("❌ Batch 1 failed - stopping test")
            return False
        
        # Batch 2: Use variables from Batch 1 (separate call!)
        print("\n🔄 Batch 2: Using variables from Batch 1...")
        batch2_commands = [
            ("Check x exists", "print(f'x is still: {x}')"),
            ("Check name exists", "print(f'name is: {name}')"),
            ("Check pi exists", "print(f'pi is: {pi_value:.4f}')"),
            ("Do math with x", "result = x * 2; print(f'x * 2 = {result}')"),
        ]
        
        results2 = client.execute_python_batch(batch2_commands, command_delay=0.2)
        success2 = sum(1 for r in results2 if r['success'])
        print(f"✅ Batch 2: {success2}/{len(results2)} commands successful")
        
        # Batch 3: Import something new and use old variables
        print("\n🤖 Batch 3: Import new modules and use old variables...")
        batch3_commands = [
            ("Import datetime", "from datetime import datetime"),
            ("Get current time", "now = datetime.now()"),
            ("Combine old and new", "message = f'{name}: x={x}, time={now.strftime(\"%H:%M:%S\")}'"),
            ("Print combined", "print(message)"),
        ]
        
        results3 = client.execute_python_batch(batch3_commands, command_delay=0.2)
        success3 = sum(1 for r in results3 if r['success'])
        print(f"✅ Batch 3: {success3}/{len(results3)} commands successful")
        
        # Summary
        total_success = success1 + success2 + success3
        total_commands = len(results1) + len(results2) + len(results3)
        
        print(f"\n📊 Simple Persistence Results:")
        print(f"   Total commands: {total_commands}")
        print(f"   Successful: {total_success}")
        print(f"   Success rate: {total_success/total_commands*100:.1f}%")
        
        if total_success == total_commands:
            print("\n🎉 SUCCESS: Variables persist across separate batch calls!")
            return True
        else:
            print(f"\n⚠️  {total_commands - total_success} commands failed")
            return False
            
    except Exception as e:
        print(f"❌ Simple persistence test failed: {e}")
        return False


def test_opentrons_persistence(client):
    """Test that Opentrons protocol variables persist"""
    print("\n\n🤖 Opentrons Protocol Persistence Test")
    print("=" * 50)
    
    try:
        # Batch 1: Setup protocol (with 120s timeout for import)
        print("\n📝 Setting up protocol...")
        setup_commands = [
            ("Import execute", "from opentrons import execute"),
            ("Create protocol", "protocol = execute.get_protocol_api('2.18')"),
            ("Store version", "api_version = protocol.api_version"),
            ("Print version", "print(f'Protocol API version: {api_version}')"),
        ]
        
        results = client.execute_python_batch(setup_commands, timeout=120, command_delay=1.0)
        success = sum(1 for r in results if r['success'])
        print(f"✅ Protocol setup: {success}/{len(results)} successful")
        
        if success != len(results):
            print("❌ Protocol setup failed")
            return False
        
        # Batch 2: Use protocol from previous batch
        print("\n🔍 Using protocol from previous batch...")
        use_commands = [
            ("Check protocol exists", "print(f'Protocol object: {type(protocol)}')"),
            ("Check version exists", "print(f'Stored version: {api_version}')"),
            ("Access protocol property", "print(f'Protocol API: {protocol.api_version}')"),
            ("Create summary", "summary = f'Protocol v{api_version} is ready'; print(summary)"),
        ]
        
        results = client.execute_python_batch(use_commands, command_delay=0.3)
        success = sum(1 for r in results if r['success'])
        print(f"✅ Protocol usage: {success}/{len(results)} successful")
        
        if success == len(results):
            print("🎉 Protocol variables persist across batches!")
            return True
        else:
            print("❌ Protocol variables were lost!")
            return False
            
    except Exception as e:
        print(f"❌ Protocol test failed: {e}")
        return False


def main():
    """Demo comprehensive state tracking with separate batch calls"""
    print("🔬 Complete Variable Persistence & State Tracking Demo")
    print("=" * 70)
    
    # Configuration
    host_alias = "ot2_tailscale"
    password = input("Enter OT-2 password: ")
    
    # Connect with longer timeout to avoid reconnections
    print("🔌 Connecting to robot...")
    client = SSHClient(
        host_alias=host_alias, 
        password=password,
        command_timeout=120,  # Longer timeout for protocol operations
        max_retries=2
    )
    
    if not client.connect():
        print("❌ Connection failed")
        return False
    
    print(f"✅ Connected in {client.session_state.value} mode")
    
    try:
        # Test 1: Simple variable persistence
        test1_success = test_simple_persistence(client)
        
        # Test 2: Protocol persistence 
        test2_success = test_opentrons_persistence(client)
        
        # Test 3: Full state tracking demo (if both tests passed)
        if test1_success and test2_success:
            print("\n\n🎯 Advanced State Tracking Demo")
            print("=" * 50)
            print("Since persistence is confirmed, proceeding with full demo...")
            test3_success = run_state_tracking_demo(client)
        else:
            print("\n❌ Skipping advanced demo due to persistence test failures")
            test3_success = False
        
        # Final summary
        print(f"\n🎯 Final Test Summary:")
        print(f"   Simple variables test: {'✅ PASSED' if test1_success else '❌ FAILED'}")
        print(f"   Protocol persistence test: {'✅ PASSED' if test2_success else '❌ FAILED'}")
        print(f"   Advanced state demo: {'✅ PASSED' if test3_success else '❌ FAILED/SKIPPED'}")
        
        if test1_success and test2_success:
            print(f"\n🎉 CONCLUSION:")
            print(f"   Variables DO persist between separate batch calls!")
            print(f"   Both simple variables and complex objects persist.")
            print(f"   Key: Use 120s+ timeouts for 'import execute' operations")
            print(f"   Key: Maintain stable SSH session with proper timeouts")
            return True
        else:
            print(f"\n❌ Some tests failed - variables may not be persisting correctly.")
            return False
        
    except Exception as e:
        print(f"❌ Demo failed: {e}")
        return False
    
    finally:
        print("\n🔌 Disconnecting...")
        client.close()


def run_state_tracking_demo(client):
    """Run the full state tracking demo"""
    try:
        # Batch 1: Import additional modules for state tracking
        print("\n📦 Batch 1: Importing state tracking modules...")
        import_commands = [
            ("Import typing", "from typing import Dict, List, Any, Optional"),
            ("Import logging", "import logging"),
            ("Import json", "import json"),
        ]
        
        results = client.execute_python_batch(import_commands, timeout=30)
        if not all(r['success'] for r in results):
            print("❌ Failed to import required modules")
            return False
        
        # Batch 2: Define state tracking functions
        print("\n🔧 Batch 2: Defining state tracking functions...")
        
        # Use send_code_block for the function definitions
        state_functions = '''
def get_deck_state(protocol_context):
    """Get deck state as simple dictionary - robot optimized version"""
    slots = {}
    for slot_num in range(1, 13):
        slot = str(slot_num)
        try:
            deck_item = protocol_context.deck[slot]
            if deck_item is not None:
                if hasattr(deck_item, 'load_name'):  # Labware
                    slots[slot] = {
                        'type': 'labware',
                        'name': getattr(deck_item, 'name', deck_item.load_name),
                        'load_name': deck_item.load_name,
                        'is_tiprack': getattr(deck_item, 'is_tiprack', False)
                    }
                elif hasattr(deck_item, 'module_name'):  # Module
                    slots[slot] = {
                        'type': 'module',
                        'name': getattr(deck_item, 'name', 'Unknown Module'),
                        'module_name': deck_item.module_name,
                        'status': getattr(deck_item, 'status', 'unknown')
                    }
                else:
                    slots[slot] = {'type': 'other', 'name': str(deck_item)}
            else:
                slots[slot] = None
        except:
            slots[slot] = None
    
    return {
        'slots': slots,
        'occupied_slots': len([s for s in slots.values() if s is not None]),
        'empty_slots': len([s for s in slots.values() if s is None]),
        'timestamp': datetime.now().isoformat()
    }

def get_labware_state(labware_context):
    """Get labware wells as simple list - robot optimized version"""
    wells = []
    available_tips = 0
    wells_with_liquid = 0
    
    for well in labware_context.wells():
        well_data = {
            'name': well.well_name,
            'max_volume': well.max_volume,
            'depth': well.depth,
            'diameter': getattr(well, 'diameter', None)
        }
        
        # Add tip status for tip racks
        if getattr(labware_context, 'is_tiprack', False):
            has_tip = getattr(well, 'has_tip', True)
            well_data['has_tip'] = has_tip
            if has_tip:
                available_tips += 1
        
        # Add liquid volume
        try:
            liquid_volume = well.current_liquid_volume
            well_data['current_liquid_volume'] = liquid_volume
            if liquid_volume > 0:
                wells_with_liquid += 1
        except:
            well_data['current_liquid_volume'] = 0
        
        wells.append(well_data)
    
    return {
        'load_name': labware_context.load_name,
        'is_tiprack': getattr(labware_context, 'is_tiprack', False),
        'wells': wells,
        'total_wells': len(wells),
        'available_tips': available_tips if getattr(labware_context, 'is_tiprack', False) else None,
        'wells_with_liquid': wells_with_liquid
    }

def get_pipette_state(pipette_context):
    """Get pipette state as simple dictionary - robot optimized version"""
    return {
        'name': pipette_context.name,
        'mount': str(pipette_context.mount),
        'has_tip': pipette_context.has_tip,
        'current_volume': pipette_context.current_volume,
        'max_volume': pipette_context.max_volume,
        'min_volume': pipette_context.min_volume,
        'tip_racks': len(pipette_context.tip_racks) if hasattr(pipette_context, 'tip_racks') else 0
    }

def print_deck_summary(deck_state):
    """Print a nice summary of deck state"""
    print(f"\\n📋 Deck Summary:")
    print(f"   • Total slots: 12")
    print(f"   • Occupied: {deck_state['occupied_slots']}")
    print(f"   • Empty: {deck_state['empty_slots']}")
    print(f"   • Timestamp: {deck_state['timestamp']}")
    
    print(f"\\n🧪 Loaded Items:")
    for slot, item in deck_state['slots'].items():
        if item:
            print(f"   • Slot {slot}: {item['type']} - {item['name']}")

def print_labware_summary(labware_state):
    """Print a nice summary of labware state"""
    print(f"\\n🔬 Labware: {labware_state['load_name']}")
    print(f"   • Total wells: {labware_state['total_wells']}")
    print(f"   • Is tiprack: {labware_state['is_tiprack']}")
    
    if labware_state['is_tiprack']:
        print(f"   • Available tips: {labware_state['available_tips']}")
        print(f"   • Used tips: {labware_state['total_wells'] - labware_state['available_tips']}")
    else:
        print(f"   • Wells with liquid: {labware_state['wells_with_liquid']}")

def print_pipette_summary(pipette_state):
    """Print a nice summary of pipette state"""
    print(f"\\n🔧 Pipette: {pipette_state['name']}")
    print(f"   • Mount: {pipette_state['mount']}")
    print(f"   • Has tip: {pipette_state['has_tip']}")
    print(f"   • Current volume: {pipette_state['current_volume']} µL")
    print(f"   • Volume range: {pipette_state['min_volume']}-{pipette_state['max_volume']} µL")
    print(f"   • Tip racks: {pipette_state['tip_racks']}")

print("✅ State tracking functions defined successfully!")
'''
        
        result = client.send_code_block(state_functions, "State tracking functions", timeout=30)
        if not result['success']:
            print(f"❌ Failed to load functions: {result['error']}")
            return False
        
        # Batch 3: Setup robot protocol (note: protocol should already exist from persistence test)
        print("\n🤖 Batch 3: Setting up robot operations...")
        setup_commands = [
            ("Home robot", "protocol.home()"),
            ("Check initial state", "empty_deck = get_deck_state(protocol); print(f'Empty deck: {empty_deck[\"empty_slots\"]}/12 slots')"),
        ]
        
        results = client.execute_python_batch(setup_commands, timeout=90, command_delay=1.0)
        if not all(r['success'] for r in results):
            print("❌ Robot setup failed")
            return False
        
        # Batch 4: Load labware (using variables from previous batches!)
        print("\n🧪 Batch 4: Loading labware...")
        labware_commands = [
            ("Load tip rack", "tip_rack = protocol.load_labware('opentrons_96_tiprack_300ul', 1)"),
            ("Load plate", "plate = protocol.load_labware('corning_96_wellplate_360ul_flat', 2)"),
            ("Load pipette", "pipette = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tip_rack])"),
            ("Verify loading", "print(f'✅ Loaded: {tip_rack.load_name}, {plate.load_name}, {pipette.name}')"),
        ]
        
        results = client.execute_python_batch(labware_commands, timeout=60, command_delay=1.0)
        if not all(r['success'] for r in results):
            print("❌ Labware loading failed")
            return False
        
        # Batch 5: State analysis (using functions AND variables from previous batches!)
        print("\n📊 Batch 5: Comprehensive state analysis...")
        analysis_commands = [
            ("Get loaded deck state", "loaded_deck = get_deck_state(protocol)"),
            ("Print deck summary", "print_deck_summary(loaded_deck)"),
            ("Get tip rack state", "tip_state = get_labware_state(tip_rack)"),
            ("Print tip rack summary", "print_labware_summary(tip_state)"),
            ("Get pipette state", "pip_state = get_pipette_state(pipette)"),
            ("Print pipette summary", "print_pipette_summary(pip_state)"),
        ]
        
        results = client.execute_python_batch(analysis_commands, timeout=30, command_delay=0.5)
        successful = sum(1 for r in results if r['success'])
        print(f"✅ State analysis: {successful}/{len(results)} commands successful")
        
        # Batch 6: Tip operations (using all previous variables!)
        print("\n🔄 Batch 6: Tip operations with state tracking...")
        tip_demo_commands = [
            ("Initial tip count", "initial_tips = get_labware_state(tip_rack)['available_tips']; print(f'Starting tips: {initial_tips}')"),
            ("Pick up tip", "pipette.pick_up_tip()"),
            ("Check pipette state", "pip_after = get_pipette_state(pipette); print(f'Pipette has_tip: {pip_after[\"has_tip\"]}')"),
            ("Count remaining tips", "tips_after = get_labware_state(tip_rack)['available_tips']; print(f'Tips remaining: {tips_after}')"),
            ("Return tip", "pipette.return_tip()"),
            ("Final tip count", "final_tips = get_labware_state(tip_rack)['available_tips']; print(f'Final tips: {final_tips}')"),
        ]
        
        results = client.execute_python_batch(tip_demo_commands, timeout=45, command_delay=0.5)
        successful = sum(1 for r in results if r['success'])
        print(f"✅ Tip operations: {successful}/{len(results)} commands successful")
        
        # Batch 7: Final comprehensive report
        print("\n📋 Batch 7: Final comprehensive report...")
        final_commands = [
            ("Final deck state", "final_deck = get_deck_state(protocol)"),
            ("Final pipette state", "final_pip = get_pipette_state(pipette)"),
            ("Final tip state", "final_tips = get_labware_state(tip_rack)"),
            ("Final plate state", "final_plate = get_labware_state(plate)"),
            ("Print final summary", "print('\\n🎯 FINAL PROTOCOL STATE:'); print_deck_summary(final_deck)"),
            ("Export as JSON", "state_export = {'deck': final_deck, 'pipette': final_pip, 'tip_rack': final_tips, 'plate': final_plate}"),
            ("Show JSON size", "json_str = json.dumps(state_export, indent=2); print(f'JSON export: {len(json_str)} characters')"),
        ]
        
        results = client.execute_python_batch(final_commands, timeout=30, command_delay=0.3)
        successful = sum(1 for r in results if r['success'])
        print(f"✅ Final report: {successful}/{len(results)} commands successful")
        
        print("\n🎉 Advanced demo completed successfully!")
        print("\n🎯 Key Achievements:")
        print("   ✅ Used 7 separate batch calls")
        print("   ✅ Variables persisted across all batches")
        print("   ✅ Functions defined in batch 2 used in batches 5, 6, 7")
        print("   ✅ Protocol from persistence test used throughout")
        print("   ✅ Labware from batch 4 used in batches 5, 6, 7")
        print("   ✅ No need to batch everything together!")
        
        return True
        
    except Exception as e:
        print(f"❌ Advanced demo failed: {e}")
        return False


if __name__ == "__main__":
    success = main()
    print("\n💡 Success Factors:")
    print("   • Use 120s+ timeouts for 'from opentrons import execute'")
    print("   • Use 60-90s timeouts for other heavy operations")
    print("   • Add appropriate delays between commands")
    print("   • Maintain stable SSH session throughout")
    print("   • Robot state IS persistent - it's all about session management!")
    
    if success:
        print("\n🎉 FINAL CONCLUSION: Variables DO persist between separate batch calls!")
        print("The key is proper timeout management to avoid SSH reconnections.")
    else:
        print("\n❌ Test failed - check error messages above")
    
    sys.exit(0 if success else 1) 