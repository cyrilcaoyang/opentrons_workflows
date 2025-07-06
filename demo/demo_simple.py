import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows import OpentronsControl


def demo_simple():
    """Simple demo showing OpentronsControl API usage (requires robot connection)"""
    print("🤖 Opentrons Workflows Demo")
    print("=" * 40)
    print()
    
    print("📋 Available OpentronsControl Class Methods:")
    print("=" * 40)
    
    # Show available methods
    methods = [method for method in dir(OpentronsControl) if not method.startswith('_')]
    for method in methods:
        print(f"  • {method}")
    
    print()
    print("📖 Example Usage:")
    print("=" * 40)
    
    example_code = '''
# Connect to robot
ot = OpentronsControl(host_alias="ot2", password="your_password", simulation=False)

# Load labware
labware = {
    "nickname": "plate_96_1", 
    "loadname": "corning_96_wellplate_360ul_flat", 
    "location": "1", 
    "ot_default": True, 
    "config": {}
}
ot.load_labware(labware)

# Load instrument
instrument = {
    "nickname": "p300", 
    "instrument_name": "p300_single_gen2", 
    "mount": "right", 
    "ot_default": True
}
ot.load_instrument(instrument)

# Home the robot
ot.home()

# Get location and move pipette
ot.get_location_from_labware(labware_nickname="plate_96_1", position="A1", top=-1)
ot.move_to_pip(pip_name="p300")

# Aspirate and dispense
ot.aspirate(pip_name="p300", volume=100)
ot.dispense(pip_name="p300", volume=100)

# Close session
ot.close_session()
'''
    
    print(example_code)
    
    print()
    print("🔗 Connection Requirements:")
    print("=" * 40)
    print("  • Robot must be accessible via SSH")
    print("  • Environment variables or SSH config required:")
    print("    - HOSTNAME (robot IP)")
    print("    - USERNAME (robot username)")
    print("    - KEY_FILE_PATH (SSH key path)")
    print("  • Or use host_alias with SSH config")
    print()
    print("📁 For working examples, see:")
    print("  • demo/demo_ot2_control.py")
    print("  • demo/demo_flex_control.py")
    print("  • demo/pdb_samp_prep.py")
    print("  • demo/snar_test.py")
    print()
    print("✅ Demo completed!")


if __name__ == "__main__":
    demo_simple() 