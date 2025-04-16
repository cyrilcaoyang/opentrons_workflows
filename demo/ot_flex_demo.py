from prefect import flow
from matterlab_opentrons import OpenTrons

@flow(log_prints=True)
def demo_flex(simulation: bool = True):
    ot=OpenTrons(host_alias="otflex", password="accelerate", simulation=simulation)
    ot.home()
    plates=[
       {"nickname": "plate_96_1", "loadname": "corning_96_wellplate_360ul_flat", "location": "B3", "ot_default": True, "config": {}},
        # {"nickname": "plate_96_2", "loadname": "corning_96_wellplate_360ul_flat", "location": "3", "ot_default": True, "config": {}},
        ]
    tips=[
        {"nickname": "tip_1000_96_1", "loadname": "opentrons_flex_96_filtertiprack_1000ul", "location": "B1", "ot_default": True, "config": {}}
    ]
    for plate in plates:
        ot.load_labware(plate)
    for tip in tips:
        ot.load_labware(tip)

    ot.load_instrument({"nickname": "p1000", "instrument_name": "flex_1channel_1000", "mount" : "left", "ot_default": True})
    ot.load_module({"nickname": "hs", "module_name": "heaterShakerModuleV1", "location": "A1", "adapter": "opentrons_universal_flat_adapter"})


    # ot.move_labware_w_gripper(labware_nickname="plate_96_1", new_location="C3")

    ot.hs_latch_open(nickname="hs")
    # ot.move_labware_w_gripper(labware_nickname="plate_96_1", new_location="hs_adapter")
    ot.hs_latch_close(nickname="hs")

    # ot.set_rpm(nickname="hs", rpm=200)
    # time.sleep(10)
    # ot.set_rpm(nickname="hs", rpm=0)
    # time.sleep(5)

    # ot.hs_latch_open(nickname="hs")
    # ot.move_labware_w_gripper(labware_nickname="plate_96_1", new_location="B4")

    # ot.get_location_from_labware(labware_nickname="tip_1000_96_1", position= "A1", top=0)
    # ot.pick_up_tip(pip_name="p1000")

    # ot.get_location_from_labware(labware_nickname="plate_96_1", position="A1", bottom=1)
    # ot.move_to_pip(pip_name="p1000")
    # ot.aspirate(pip_name="p1000", volume=200)
    
    # ot.get_location_from_labware(labware_nickname="plate_96_1", position="A2", top=-1)
    # ot.move_to_pip(pip_name="p1000")
    # ot.aspirate(pip_name="p1000", volume=200)

    # ot.return_tip(pip_name="p1000")

    ot.close_session()