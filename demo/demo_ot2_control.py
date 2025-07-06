import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from opentrons_workflows import OpentronsControl
import json
import time


def demo_ot2(simulation: bool = True):
    """Demo script for OT2 control without Prefect dependency"""
    print("🤖 Starting OT2 Demo")
    print("=" * 30)
    
    ot = OpentronsControl(host_alias="ot2", password="accelerate", simulation=simulation)
    ot.home()

    # Example usage with custom labware (commented out for basic demo)
    # with open(Path(r"C:\Users\aag\Downloads\matterlab_24_vialplate_3700ul.json"), "r") as f:
    #     plate_1 = json.load(f)
    # with open(Path(r"C:\Users\aag\Downloads\matterlab_1_beaker_30000ul.json"), "r") as f:
    #     beaker_1 = json.load(f)
    # with open(Path(r"C:\Users\aag\Downloads\matterlab_96_tiprack_10ul.json"), "r") as f:
    #     tips_1 = json.load(f)

    # plates = [
    #     {"nickname": "plate_96_1", "loadname": "corning_96_wellplate_360ul_flat", "location": "1", "ot_default": True, "config": {}},
    #     {"nickname": "beaker", "config": beaker_1, "location": "4", "ot_default": False},
    #     {"nickname": "vial_24_well_1", "config": plate_1, "location": "5", "ot_default": False},
    #     {"nickname": "vial_24_well_2", "config": plate_1, "location": "6", "ot_default": False},
    # ]
    # tips = [
    #     {"nickname": "tip_20_96_1", "config": tips_1, "location": "7", "ot_default": False},
    #     {"nickname": "tip_300_96_1", "loadname": "opentrons_96_tiprack_300ul", "location": "8", "ot_default": True, "config": {}},
    # ]
    # for plate in plates:
    #     ot.load_labware(plate)
    # for tip in tips:
    #     ot.load_labware(tip)

    # ot.load_instrument({"nickname": "p20", "instrument_name": "p20_single_gen2", "mount": "left", "ot_default": True})
    # ot.load_instrument({"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "right", "ot_default": True})

    # Example liquid handling workflow
    # ot.get_location_from_labware(labware_nickname="tip_300_96_1", position="A1", top=0)
    # ot.pick_up_tip(pip_name="p300")
    # sample_num = 48
    # for i in range(0, sample_num):
    #     target_loc = f"{chr(65+i//12)}{i%12+1}"
    #     print(f"Processing sample {i+1}: {target_loc}")
    #
    #     ot.get_location_from_labware(labware_nickname="beaker", position="A1", bottom=5)
    #     ot.move_to_pip(pip_name="p300")
    #     ot.aspirate(pip_name="p300", volume=150)
    #
    #     ot.get_location_from_labware(labware_nickname="plate_96_1", position=target_loc, top=-1)
    #     ot.move_to_pip(pip_name="p300")
    #     ot.dispense(pip_name="p300", volume=150)
    #     ot.blow_out(pip_name="p300")
    # ot.return_tip(pip_name="p300")

    print("✅ Demo completed successfully!")
    ot.close_session()


if __name__ == "__main__":
    demo_ot2(simulation=True) 