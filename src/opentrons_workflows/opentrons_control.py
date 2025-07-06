from .opentrons_sshclient import SSHClient
import os
from typing import Dict


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

    def _load_custom_labware(self, nickname: str, labware_config:Dict, location: str):
        loadname = labware_config["parameters"]["loadName"]
        self.invoke(f"{loadname}={labware_config}")
        self.invoke(f"{nickname} = protocol.load_labware_from_definition(labware_def = {loadname}, location = '{location}')")

    def _load_default_labware(self, nickname:str, loadname:str, location:str):
        self.invoke(f"{nickname} = protocol.load_labware(load_name = '{loadname}', location = '{location}')")

    def _load_default_instrument(self, nickname:str, instrument_name:str, mount:str):
        self.invoke(f"{nickname} = protocol.load_instrument(instrument_name = '{instrument_name}', mount = '{mount}')")

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

    def home(self):
        self.invoke("protocol.home()")

    def well_diameter(self, labware_nickname: str, position: str):
        return float(self.invoke(f"{labware_nickname}['{position}'].diameter").split("\r\n")[-2])

    def well_depth(self, labware_nickname: str, position: str):
        return float(self.invoke(f"{labware_nickname}['{position}'].depth").split("\r\n")[-2])

    def tip_length(self, labware_nickname: str, position: str):
        rtn = self.invoke(f"{labware_nickname}['{position}'].length").split("\r\n")
        if len(rtn == 3):
            return float(rtn[-2])
        else:
            return None

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

    def get_location_absolute(self, x: float, y: float, z: float, reference: str = None):
        # reference is deck position "1" "D1" etc. Default is None as deck itself
        self.invoke(f"location = Location(Point({x},{y},{z}), '{str(reference)}')")

    def move_to_pip(self, pip_name: str):
        self.invoke(f"{pip_name}.move_to(location = location)")

    def pick_up_tip(self, pip_name: str):
        self.invoke(f"{pip_name}.pick_up_tip(location = location)")

    def return_tip(self, pip_name: str):
        self.invoke(f"{pip_name}.return_tip()")

    def drop_tip(self, pip_name: str):
        self.invoke(f"{pip_name}.drop_tip()")

    def prepare_aspirate(self, pip_name:str):
        self.invoke(f"{pip_name}.prepare_to_aspirate()")

    def aspirate(self, pip_name: str, volume: float):
        self.invoke(f"{pip_name}.aspirate(volume = {volume}, location = location)")

    def dispense(self, pip_name: str, volume: float, push_out: float = None):
        self.invoke(f"{pip_name}.dispense(volume = {volume}, location = location, push_out = {str(push_out)})")

    def touch_tip(self, pip_name: str, labware_nickname: str, position: str, radius: float = 1.0, v_offset: float = -1.0):
        self.invoke(f"{pip_name}.touch_tip('{labware_nickname}['{position}']', radius = {radius}, v_offset = {v_offset})")

    def blow_out(self, pip_name: str):
        self.invoke(f"{pip_name}.blow_out(location = location)")

    def set_speed(self, pip_name: str, speed:float):
        self.invoke(f"{pip_name}.default_speed = {speed}")

    def delay(self, seconds:float = 0, minutes: float = 0):
        self.invoke(f"protocol.delay(seconds={seconds}, minutes = {minutes})")

    def resume(self):
        self.invoke("protocol.resume()")

    def pause(self):
        self.invoke("protocol.pause()")

    def move_labware_w_gripper(self, labware_nickname: str, new_location: str):
        # labware_nickname is the name of labware to move, not the loadname which could duplicate
        # new_location "1", "D1", certain module (heater/shaker etc., protocol_api.OFF_DECK)
        if new_location == "OFF_DECK":
            self.invoke(f"protocol.move_labware(labware = {labware_nickname}, new_location = protocol_api.OFF_DECK, use_gripper = True)")
        elif "adapter" in new_location:
            self.invoke(f"protocol.move_labware(labware = {labware_nickname}, new_location = {new_location}, use_gripper = True)")
        else:
            self.invoke(f"protocol.move_labware(labware = {labware_nickname}, new_location = '{new_location}', use_gripper = True)")

    def hs_latch_open(self, nickname: str):
        self.invoke(f"{nickname}.open_labware_latch()")

    def hs_latch_close(self, nickname: str):
        self.invoke(f"{nickname}.close_labware_latch()")

    def set_rpm(self, nickname: str, rpm: int):
        if rpm in range(200, 3000):
            self.invoke(f"{nickname}.set_and_wait_for_shake_speed(rpm={rpm})")
        else:
            self.invoke(f"{nickname}.deactivate_shaker()")

    def set_temp(self, nickname: str, temp: float):
        if temp in range(27, 95):
            self.invoke(f"{nickname}.set_and_wait_for_temperature(temp={temp})")
        else:
            self.invoke(f"{nickname}.deactivate_heater()")

    def get_rpm(self, nickname: str):
        return self.invoke(f"{nickname}.current_speed")

    def get_temp(self, nickname: str):
        return self.invoke(f"{nickname}.current_temperature")   

    def remove_labware(self, labware_nickname: str):
        self.invoke(f"deck_pos = {labware_nickname}.parent")
        self.invoke(f"del protocol.deck[deck_pos]")

    def home_pipette(self, pip_name: str):
        self.invoke(f"{pip_name}.home()")

    def home_plunger(self, pip_name: str):
        self.invoke(f"{pip_name}.home_plunger()")

    def close_session(self):
        self.home()
        self._disconnect()
