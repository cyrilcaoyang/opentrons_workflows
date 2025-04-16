from prefect import flow,task,serve
from .sshclient import SSHClient
# from dotenv import load_dotenv
import os
import time 
# from sensor_util import color_sensor
from pathlib import Path
from typing import Dict, Union
import json

# load_dotenv(".env")

class OpenTrons:
    def __init__(self, host_alias:str = None, password="", simulation=False):
        self._connect(host_alias, password)
        self._get_protocol(simulation)

    def _connect(self, host_alias:str = None, password=""):
        
        self.client = SSHClient(
            hostname=os.getenv("HOSTNAME"),
            username=os.getenv("USERNAME"),
            key_file_path=os.getenv("KEY_FILE_PATH"),
            host_alias=host_alias,
            password=password
        )
        self.client.connect()

    def invoke(self, code):
        return self.client.invoke(code)

    def _disconnect(self):
        self.client.close()

    def _get_protocol(self,simulation):
        self.invoke("from opentrons.types import Point, Location")
        self.invoke("from opentrons import protocol_api")
        self.invoke("import json")
        if simulation:
            self.invoke("from opentrons import simulate")
            self.invoke("protocol = simulate.get_protocol_api('2.21')")
        else:
            self.invoke("from opentrons import execute")
            self.invoke("protocol = execute.get_protocol_api('2.21')")

    @flow
    def _load_custom_labware(self, nickname: str, labware_config:Dict, location: str):
        loadname = labware_config["parameters"]["loadName"]
        self.invoke(f"{loadname}={labware_config}")
        self.invoke(f"{nickname} = protocol.load_labware_from_definition(labware_def = {loadname}, location = '{location}')")

    @flow
    def _load_default_labware(self, nickname:str, loadname:str, location:str):
        self.invoke(f"{nickname} = protocol.load_labware(load_name = '{loadname}', location = '{location}')")

    @flow
    def _load_default_instrument(self, nickname:str, instrument_name:str, mount:str):
        self.invoke(f"{nickname} = protocol.load_instrument(instrument_name = '{instrument_name}', mount = '{mount}')")

    @flow
    def _load_custom_instrument(self, nickname: str, instrument_config: Dict, mount: str):
        raise NotImplementedError("custom instrument not implemented")

    def _setup_device_metadata(self):
        self.invoke("p300.well_bottom_clearance.dispense=10")

    def load_labware(self, labware: Dict):
        # sample labware Dict
        # lw = {
        #     "nickname": "96 well plate",
        #     "loadname": "opentrons_96_tiprack_1000ul",
        #     "location": "1",
        #     "ot_default": True,
        #     "config": {}
        # }
        if labware["ot_default"]:
            self._load_default_labware(nickname=labware["nickname"], loadname=labware["loadname"], location=labware["location"])
        else:
            self._load_custom_labware(nickname=labware["nickname"], labware_config=labware["config"], location=labware["location"])
    
    @flow
    def load_instrument(self, instrument: Dict):
        # sample instrument Dict
        # ins = {
        #     "nickname": "p1000",
        #     "instrument_name": "p1000_single_gen2",
        #     "mount": "right",
        #     "ot_default": True,
        #     "config": {}
        # }
        if instrument["ot_default"]:
            self._load_default_instrument(nickname=instrument["nickname"], instrument_name=instrument["instrument_name"], mount=instrument["mount"])
        else:
            self._load_custom_instrument(nickname=instrument["nickname"], instrument_config=instrument["config"], mount=instrument["mount"])

    @flow
    def load_module(self, module: Dict):
        # sample module Dict
        # module = {
        #     "nickname": "hs",
        #     "module_name": "heaterShakerModuleV1",
        #     "location": "A1",
        #     "adapter": "opentrons_universal_flat_adapter"
        # }
        nickname = module["nickname"]
        module_name = module["module_name"]
        location = module["location"]
        adapter = module["adapter"]
        self.invoke(f"{nickname} = protocol.load_module(module_name = '{module_name}', location = '{location}')")
        self.invoke(f"{nickname}_adapter = {nickname}.load_adapter(name = '{adapter}')")

    @flow
    def home(self):
        self.invoke("protocol.home()")

    @flow 
    def well_diameter(self, labware_nickname: str, position: str):
        return float(self.invoke(f"{labware_nickname}['{position}'].diameter").split("\r\n")[-2])
    
    @flow 
    def well_depth(self, labware_nickname: str, position: str):
        return float(self.invoke(f"{labware_nickname}['{position}'].depth").split("\r\n")[-2])
    
    @flow
    def tip_length(self, labware_nickname: str, position: str):
        rtn = self.invoke(f"{labware_nickname}['{position}'].length").split("\r\n")
        if len(rtn == 3):
            return float(rtn[-2])
        else:
            return None

    @flow
    def get_location_from_labware(self, labware_nickname: str, position: str, top: float = 0, bottom: float=0, center: float=0):
        if top:
            append = f".top({top})"
        elif bottom:
            append = f".bottom({bottom})"
        elif center:
            append = f".center()"
        else:
            append = ".top(0)" # original one with 0 offset at z axis
        self.invoke(f"location = {labware_nickname}['{position}']{append}")
        
    @flow
    def get_location_absolute(self, x: float, y: float, z: float, reference: str = None):
        # reference is deck position "1" "D1" etc. Default is None as deck itself
        self.invoke(f"location = Location(Point({x},{y},{z}), '{str(reference)}')")

    @flow
    def move_to_pip(self, pip_name: str):
        self.invoke(f"{pip_name}.move_to(location = location)")

    @flow
    def pick_up_tip(self, pip_name: str):
        self.invoke(f"{pip_name}.pick_up_tip(location = location)")

    @flow
    def return_tip(self, pip_name: str):
        self.invoke(f"{pip_name}.return_tip()")

    @flow
    def drop_tip(self, pip_name: str):
        self.invoke(f"{pip_name}.drop_tip()")

    @flow
    def prepare_aspirate(self, pip_name:str):
        self.invoke(f"{pip_name}.prepare_to_aspirate()")

    @flow
    def aspirate(self, pip_name: str, volume: float):
        self.invoke(f"{pip_name}.aspirate(volume = {volume}, location = location)")

    @flow
    def dispense(self, pip_name: str, volume: float, push_out: float = None):
        self.invoke(f"{pip_name}.dispense(volume = {volume}, location = location, push_out = {str(push_out)})")

    @flow
    def touch_tip(self, pip_name: str, labware_nickname: str, position: str, radius: float = 1.0, v_offset: float = -1.0):
        self.invoke(f"{pip_name}.touch_tip('{labware_nickname}['{position}']', radius = {radius}, v_offset = {v_offset})")

    @flow
    def blow_out(self, pip_name: str):
        self.invoke(f"{pip_name}.blow_out(location = location)")

    @flow
    def set_speed(self, pip_name: str, speed:float):
        self.invoke(f"{pip_name}.default_speed = {speed}")
    
    @flow
    def delay(self, seconds:float = 0, minutes: float = 0):
        self.invoke(f"protocol.delay(seconds={seconds}, minutes = {minutes})")
    
    @flow
    def resume(self):
        self.invoke("protocol.resume()")

    @flow
    def pause(self):
        self.invoke("protocol.pause()")

    @flow
    def move_labware_w_gripper(self, labware_nickname: str, new_location: str):
        # labware_nickname is the name of labware to move, not the loadname which could duplicate
        # new_location "1", "D1", certain module (heater/shaker etc., protocol_api.OFF_DECK)
        if new_location == "OFF_DECK":
            self.invoke(f"protocol.move_labware(labware = {labware_nickname}, new_location = protocol_api.OFF_DECK, use_gripper = True)")
        elif "adapter" in new_location:
            self.invoke(f"protocol.move_labware(labware = {labware_nickname}, new_location = {new_location}, use_gripper = True)")
        else:
            self.invoke(f"protocol.move_labware(labware = {labware_nickname}, new_location = '{new_location}', use_gripper = True)")
    
    @flow
    def hs_latch_open(self, nickname: str):
        self.invoke(f"{nickname}.open_labware_latch()")

    @flow
    def hs_latch_close(self, nickname: str):
        self.invoke(f"{nickname}.close_labware_latch()")
    
    @flow
    def set_rpm(self, nickname: str, rpm: int):
        if rpm in range(200, 3000):
            self.invoke(f"{nickname}.set_and_wait_for_shake_speed(rpm={rpm})")
        else:
            self.invoke(f"{nickname}.deactivate_shaker()")
    
    @flow
    def set_temp(self, nickname: str, temp: float):
        if temp in range(27, 95):
            self.invoke(f"{nickname}.set_and_wait_for_temperature(temp={temp})")
        else:
            self.invoke(f"{nickname}.deactivate_heater()")

    @flow
    def get_rpm(self, nickname: str):
        return self.invoke(f"{nickname}.current_speed")
    
    @flow
    def get_temp(self, nickname: str):
        return self.invoke(f"{nickname}.current_temperature")   

    @flow
    def remove_labware(self, labware_nickname: str):
        self.invoke(f"deck_pos = {labware_nickname}.parent")
        self.invoke(f"del protocol.deck[deck_pos]")

    @flow
    def home_pipette(self, pip_name: str):
        self.invoke(f"{pip_name}.home()")

    @flow
    def home_plunger(self, pip_name: str):
        self.invoke(f"{pip_name}.home_plunger()")

    @flow
    def close_session(self):
        self.home()
        self._disconnect()


@flow(log_prints=True)
def demo_ot2(simulation:bool = True):
    ot = OpenTrons(host_alias="ot2", password = "accelerate", simulation=simulation)
    ot.home()
    with open(Path(r"C:\Users\aag\Downloads\matterlab_24_vialplate_3700ul.json"), "r") as f:
        plate_1 = json.load(f)
    with open(Path(r"C:\Users\aag\Downloads\matterlab_1_beaker_30000ul.json"), "r") as f:
        beaker_1 = json.load(f)
    with open(Path(r"C:\Users\aag\Downloads\matterlab_96_tiprack_10ul.json"), "r") as f:
        tips_1 = json.load(f)

    plates = [
        {"nickname": "plate_96_1", "loadname": "corning_96_wellplate_360ul_flat", "location": "1", "ot_default": True, "config": {}},

        {"nickname": "beaker", "config": beaker_1, "location": "4", "ot_default": False},
        {"nickname": "vial_24_well_1", "config": plate_1, "location": "5", "ot_default": False},
        {"nickname": "vial_24_well_2", "config": plate_1, "location": "6", "ot_default": False},
        # {"nickname": "plate_96_2", "loadname": "corning_96_wellplate_360ul_flat", "location": "3", "ot_default": True, "config": {}},
        ]
    tips = [
        # {"nickname": "tip_20_96_1", "loadname": "opentrons_96_tiprack_20ul", "location": "7", "ot_default": True, "config": {}},
        {"nickname": "tip_20_96_1", "config": tips_1, "location": "7", "ot_default": False},
        {"nickname": "tip_300_96_1", "loadname": "opentrons_96_tiprack_300ul", "location": "8", "ot_default": True, "config": {}},
    ]
    for plate in plates:
        ot.load_labware(plate)
    for tip in tips:
        ot.load_labware(tip)

    ot.load_instrument({"nickname": "p20", "instrument_name": "p20_single_gen2", "mount": "left", "ot_default": True})
    ot.load_instrument({"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "right", "ot_default": True})
    
    ot.get_location_from_labware(labware_nickname="tip_300_96_1", position= "A1", top=0)
    ot.pick_up_tip(pip_name="p300")
    sample_num=48
    for i in range(0, sample_num):
        target_loc = f"{chr(65+i//12)}{i%12+1}"
        print(f"add to {target_loc}")

        ot.get_location_from_labware(labware_nickname="beaker", position="A1", bottom=5)
        ot.move_to_pip(pip_name="p300")
        ot.aspirate(pip_name="p300", volume=150)

        ot.get_location_from_labware(labware_nickname="plate_96_1", position=target_loc, top=-1)
        ot.move_to_pip(pip_name="p300")
        ot.dispense(pip_name="p300", volume=150)
        ot.blow_out(pip_name="p300")        
    ot.return_tip(pip_name="p300")

    for i in range(0, 24):
        source_loc = f"{chr(65+i//6)}{i%6+1}"
        target_loc = f"{chr(65+i//12)}{i%12+1}"

        ot.get_location_from_labware(labware_nickname="tip_20_96_1", position=target_loc, top=0)
        ot.pick_up_tip(pip_name="p20")

        ot.get_location_from_labware(labware_nickname="vial_24_well_1", position=source_loc, bottom=5)
        ot.move_to_pip("p20")
        ot.aspirate(pip_name="p20", volume=10)
        
        ot.get_location_from_labware(labware_nickname="plate_96_1", position=target_loc, top=-1)
        ot.move_to_pip("p20")
        ot.dispense(pip_name="p20", volume=10)
        ot.blow_out(pip_name="p20")

        ot.drop_tip("p20")
    
    for i in range(0, 24):
        source_loc = f"{chr(65+i//6)}{i%6+1}"
        target_loc = f"{chr(67+i//12)}{i%12+1}"

        ot.get_location_from_labware(labware_nickname="tip_20_96_1", position=target_loc, top=0)
        ot.pick_up_tip(pip_name="p20")

        ot.get_location_from_labware(labware_nickname="vial_24_well_2", position=source_loc, bottom=5)
        ot.move_to_pip("p20")
        ot.aspirate(pip_name="p20", volume=10)
        
        ot.get_location_from_labware(labware_nickname="plate_96_1", position=target_loc, bottom=1)
        ot.move_to_pip("p20")
        ot.dispense(pip_name="p20", volume=10)
        ot.blow_out(pip_name="p20")

        ot.drop_tip("p20")
        
        
    ot.get_location_from_labware(labware_nickname="tip_300_96_1", position= "A1", top=0)
    ot.pick_up_tip(pip_name="p300")
    sample_num=48
    for i in range(0, sample_num):
        target_loc = f"{chr(65+i//12)}{i%12+1}"
        print(f"add to {target_loc}")

        ot.get_location_from_labware(labware_nickname="beaker", position="A1", bottom=5)
        ot.move_to_pip(pip_name="p300")
        ot.aspirate(pip_name="p300", volume=150)

        ot.get_location_from_labware(labware_nickname="plate_96_1", position=target_loc, top=-1)
        ot.move_to_pip(pip_name="p300")
        ot.dispense(pip_name="p300", volume=150)
        ot.blow_out(pip_name="p300")        
    ot.return_tip(pip_name="p300")
    
    # ot.remove_labware(labware_nickname="plate_96_1")

    # ot.load_labware({"nickname": "plate_96_2", "loadname": "corning_96_wellplate_360ul_flat", "location": "2", "ot_default": True, "config": {}})

    # ot.get_location_from_labware(labware_nickname="plate_96_2", position="H12", top=-1)
    # ot.move_to_pip(pip_name="p1000")
    # ot.dispense(pip_name="p1000", volume=100)
    # ot.blow_out(pip_name="p1000")

    # ot.return_tip(pip_name="p1000")
    

    ot.close_session()

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


if __name__ == "__main__":
    demo_ot2(simulation=False)
    # demo_flex(simulation=True)
