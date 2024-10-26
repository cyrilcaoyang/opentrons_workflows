from prefect import flow,task,serve
from sshclient import SSHClient
from dotenv import load_dotenv
import os
import time 
from sensor_util import color_sensor

load_dotenv(".env")

class OT2:
    def __init__(self):
        self._connect()
        self._get_protocol()
        self._load_labware()

    def _connect(self):
        self.client = SSHClient(
            hostname=os.getenv("HOSTNAME"),
            username=os.getenv("USERNAME"),
            key_file_path=os.getenv("KEY_FILE_PATH")
        )
        self.client.connect()

    def invoke(self, code):
        return self.client.invoke(code)

    def _disconnect(self):
        self.client.close()

    def _get_protocol(self):
        self.invoke("import opentrons.execute")
        self.invoke("protocol = opentrons.execute.get_protocol_api('2.11')")


    def _load_labware(self):
        self.invoke("plate = protocol.load_labware(load_name='corning_96_wellplate_360ul_flat', location=1)")
        print("plate loaded")
        self.invoke("tiprack_1 = protocol.load_labware(load_name='opentrons_96_tiprack_300ul', location=9)")
        print("tiprack_1 loaded")
        self.invoke("tiprack_2 = protocol.load_labware(load_name='ac_color_sensor_charging_port', location=10)")
        print("tiprack_2 loaded")
        self.invoke("reservoir = protocol.load_labware(load_name='ac_6_tuberack_15000ul', location=3)")
        print("reservoir loaded")
        self.invoke("p300 = protocol.load_instrument(instrument_name='p300_single_gen2', mount='right', tip_racks=[tiprack_1])")
        print("p300 loaded")
        self._setup_device_metadata()
        #TODO store device variable here like a dict for latter logic checks

    def _setup_device_metadata(self):
        self.invoke("p300.well_bottom_clearance.dispense=10")

    @flow
    def home(self):
        self.invoke("protocol.home()")

    @flow
    def p_300_pick_up_tip(self, pos:str, tiptrack:str="tiprack_1"):
        self.invoke(f"p300.pick_up_tip({tiptrack}['{pos}'])")
    
    @flow
    def p_300_aspirate(self, pos:str,volume:int=40):
        self.invoke(f"p300.aspirate({volume}, reservoir['{pos}'])")
        #TODO: figure out what 40 is doing 

    @flow
    def dispense(self, pos:str,volume:int=40):
        self.invoke(f"p300.dispense({volume}, plate['{pos}'])")
        
    @flow
    def set_speed(self, speed:int):
        self.invoke(f"p300.default_speed = {speed}")
    
    @flow
    def blow_out(self):
        self.invoke("p300.blow_out(reservoir['A1'].top(z=-5))")
        #TODO: not sure what the -5 is doing and hardcode A1, need to be changed later
        
    @flow
    def drop_tip(self, pos:str, tiptrack:str="tiprack_1"):
        #TODO: variable check here 
        self.invoke(f"p300.drop_tip({tiptrack}['{pos}'])")

    @flow
    def pick_up_tip(self, pos:str,tiptrack:str="tiprack_2"):
        self.invoke(f"p300.pick_up_tip({tiptrack}['{pos}'])")

    @flow
    def move_to(self, pos:str):
        self.invoke(f"p300.move_to(plate['{pos}'].top(z=3))")
    
    @flow
    def delay(self, seconds:int):
        self.invoke(f"protocol.delay(seconds={seconds})")

    @flow
    def drop_tip_color_sensor(self, pos:str,tiptrack:str="tiprack_2"):
        self.invoke(f"p300.drop_tip({tiptrack}['{pos}'].top(z=-75))")

    @flow
    def check_color(self, Color_Sensor_Pos:str="A2",Color_Sensor_Tiptrack:str="tiprack_2",target_well:str="A1"):
        # self.invoke(f"p300.drop_tip({Color_Sensor_Tiptrack}['{Color_Sensor_Pos}'].top(z=-75))")
        self.pick_up_tip("A2",tiptrack="tiprack_2")
        self.move_to(target_well)
        color_sensor_1 = color_sensor()
        sensor_data = color_sensor_1.get_color_sensor_data()
        self.delay(5)
        self.drop_tip_color_sensor("A2",tiptrack="tiprack_2")
        
        return sensor_data


    @flow
    def close_session(self):
        self.home()
        self._disconnect()


@flow(log_prints=True)
def demo():
    ot2 = OT2()
    ot2.home()
    position = ["B1", "B2", "B3"]
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    columns = [str(i) for i in range(1, 13)]
    for i in range(1):
        row = rows[i // 12]
        col = columns[i % 12]
        current_well = f"{row}{col}"
        for pos in position:
            ot2.p_300_pick_up_tip(pos,tiptrack="tiprack_1")
            ot2.p_300_aspirate(pos)
            ot2.dispense(current_well)
            ot2.set_speed(100)
            ot2.blow_out()
            ot2.set_speed(400)
            ot2.drop_tip(pos,tiptrack="tiprack_1")

        #color sensor
        ot2.pick_up_tip("A2",tiptrack="tiprack_2")
        ot2.move_to(current_well)
        ot2.delay(5)
        ot2.drop_tip_color_sensor("A2",tiptrack="tiprack_2")

    print("Protocol execution complete")
    ot2.close_session()
    print("Session closed")

@flow(log_prints=True)
def demo_Get_Target(r=0.2,g=0.5,b=0.3):
    ot2 = OT2()
    ot2.home()
    position = ["B1", "B2", "B3"]
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    columns = [str(i) for i in range(1, 13)]
    for i in range(1):
        row = rows[i // 12]
        col = columns[i % 12]
        current_well = f"{row}{col}"
        for pos in position:
            ot2.p_300_pick_up_tip(pos,tiptrack="tiprack_1")
            ot2.p_300_aspirate(pos)
            ot2.dispense(current_well)
            ot2.set_speed(100)
            ot2.blow_out()
            ot2.set_speed(400)
            ot2.drop_tip(pos,tiptrack="tiprack_1")

        #color sensor
        ot2.pick_up_tip("A2",tiptrack="tiprack_2")
        ot2.move_to(current_well)
        ot2.delay(5)
        ot2.drop_tip_color_sensor("A2",tiptrack="tiprack_2")

    print("Protocol execution complete")
    ot2.close_session()
    print("Session closed")



if __name__ == "__main__":
    #demo_deployment = demo.to_deployment(name="demo",description="demo protocol")
    #serve(demo_deployment)
    demo()

