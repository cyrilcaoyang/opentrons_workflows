#!/usr/bin/env python3
"""
Test script to demonstrate OT-2 state monitoring capabilities
Shows how your approach can get real-time machine state information
"""

import json
import time
import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'src'))

from opentrons_workflows.opentrons_control import connect
# For the demo, we'll show how to use the existing connect function


def demo_stepwise_execution():
    """Demonstrate stepwise command execution using your existing architecture"""
    print("🔍 Demo: Stepwise OT-2 Control")
    print("=" * 50)
    
    try:
        # Connect using your existing connect function
        with connect(host_alias=None, simulation=True) as robot:
            print("✅ Connected to OT-2 in simulation mode")
            
            # Demo 1: Load equipment step by step
            print("\n🔧 Demo 1: Loading equipment step by step...")
            
            # Step 1: Load pipette
            print("  → Loading pipette...")
            pipette = robot.load_pipette('p1000_single_gen2', 'left')
            print(f"     ✅ Loaded: {pipette}")
            
            # Step 2: Load labware
            print("  → Loading plate...")
            plate = robot.deck.load_labware('corning_96_wellplate_360ul_flat', '1')
            print(f"     ✅ Loaded: {plate}")
            
            # Step 3: Load tips
            print("  → Loading tip rack...")
            tips = robot.deck.load_labware('opentrons_96_tiprack_1000ul', '2')
            print(f"     ✅ Loaded: {tips}")
            
            # Demo 2: Individual operations
            print("\n🧪 Demo 2: Individual liquid handling operations...")
            
            print("  → Picking up tip...")
            pipette.pick_up_tip(tips['A1'])
            
            print("  → Aspirating from source...")
            pipette.aspirate(100, plate['A1'])
            
            print("  → Dispensing to destination...")
            pipette.dispense(100, plate['B1'])
            
            print("  → Dropping tip...")
            pipette.drop_tip()
            
            # Demo 3: Show current state
            print("\n📋 Demo 3: Current robot state...")
            print(f"  Instruments loaded: {list(robot.instruments.keys())}")
            print(f"  Deck slots occupied: {[slot for slot, labware in robot.deck.loaded_labware.items() if labware]}")
            print(f"  Total commands executed: {len(robot.commands)}")
            
            print("\n✅ Demo completed successfully!")
            
    except Exception as e:
        print(f"❌ Demo failed: {str(e)}")
        import traceback
        traceback.print_exc()


def demonstrate_ui_integration():
    """Demonstrate how this integrates with a UI"""
    print("\n🌐 UI Integration Demo")
    print("=" * 30)
    
    print("Your approach enables the following UI capabilities:")
    print()
    print("1. ✅ REAL-TIME STATE MONITORING")
    print("   - Get current pipette status (loaded, has tip, etc.)")
    print("   - Monitor deck layout changes")
    print("   - Track robot status (homed, lights, door)")
    print()
    
    print("2. ✅ STEPWISE COMMAND EXECUTION")
    print("   - Execute individual commands and get updated state")
    print("   - Perfect for interactive UI operations")
    print("   - No need to run complete protocols")
    print()
    
    print("3. ✅ STATE-AWARE OPERATIONS")
    print("   - UI can show what's currently loaded")
    print("   - Prevent invalid operations (e.g., no tip attached)")
    print("   - Visual feedback for each action")
    print()
    
    print("4. ✅ CACHING & PERFORMANCE")
    print("   - State caching reduces API calls")
    print("   - Real-time updates when needed")
    print("   - Efficient for web interfaces")
    print()


def show_comparison_with_pylabrobot():
    """Show how your approach compares to PyLabRobot"""
    print("\n⚖️ Your Approach vs PyLabRobot")
    print("=" * 40)
    
    print("YOUR APPROACH ADVANTAGES:")
    print("✅ Uses your existing SSH-based architecture")
    print("✅ Leverages your proven opentrons_control.py")
    print("✅ Can get comprehensive robot state information")
    print("✅ Supports both stepwise and protocol-based execution")
    print("✅ Direct access to full Opentrons API capabilities")
    print("✅ Works with your existing labware and deck management")
    print()
    
    print("PYLABROBOT ADVANTAGES:")
    print("📊 Built-in browser-based visualizer")
    print("🔄 HTTP API approach (faster for simple operations)")
    print("🔧 Hardware-agnostic (works with multiple robot types)")
    print("📈 Real-time position tracking")
    print()
    
    print("RECOMMENDATION:")
    print("🎯 Use YOUR approach for the UI backend because:")
    print("   - You already have a working, robust system")
    print("   - It integrates seamlessly with your existing code")
    print("   - You can add state monitoring without major refactoring")
    print("   - Consider PyLabRobot's visualizer as inspiration for UI design")


if __name__ == "__main__":
    demo_stepwise_execution()
    demonstrate_ui_integration()
    show_comparison_with_pylabrobot()
    
    print("\n🚀 Next Steps:")
    print("1. Run the web dashboard: python user_scripts/start_ui.py")
    print("2. Open http://localhost:8080 in your browser")
    print("3. Test the real-time state monitoring and stepwise execution")
    print("4. Customize the UI to your specific needs") 