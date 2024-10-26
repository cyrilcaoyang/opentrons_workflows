from OT2wrapper import OT2
from prefect import flow,task,serve
from LabMind import KnowledgeObject,nosql_service
from LabMind.Utils import upload

color_metadata = {"R": 0.1, "G": 0.6, "B": 0.3, "mae": 0.25}

session_id="test"

@flow(log_prints=True)
def setup_target(R,G,B,mix_well="H11"):
    # check if they add up to 1
    total = R+G+B
    if total != 1:
        raise ValueError("The sum of the proportions must be 1")
    ot2 = OT2()
    ot2.home()

    position = ["B1", "B2", "B3"]
    rows = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
    portion = {"B1": R, "B2": G, "B3": B}
    total_volume = 150
    red_volume = int(portion["B1"] * total_volume)
    green_volume = int(portion["B2"] * total_volume)
    blue_volume = int(portion["B3"] * total_volume)
    reservoir = {"B1": red_volume, "B2": green_volume, "B3": blue_volume}

    columns = [str(i) for i in range(1, 13)]
    for i in range(5,6):
        row = rows[i // 12]
        col = columns[i % 12]
        for pos in position:
            if float(portion[pos]) != 0.0:
                ot2.p_300_pick_up_tip(pos,tiptrack="tiprack_1")
                ot2.p_300_aspirate(pos,volume = reservoir[pos])
                ot2.dispense(mix_well,volume = reservoir[pos])
                ot2.set_speed(100)
                ot2.blow_out()
                ot2.set_speed(400)
                ot2.drop_tip(pos,tiptrack="tiprack_1")

        #color sensor
        well_color_data = ot2.check_color(target_well=mix_well)
        #upload well_color_data
        
        project = "OT2"
        collection = "target"
        unique_fields = ["R","G","B","well_color_data"]
        metadata = {"R":R,
                    "G":G,
                    "B":B,
                    "well_color_data": well_color_data,
                    "project": project,
                    "collection": collection,
                    "unique_fields": unique_fields,
                    "session_id": session_id}
        
        target = KnowledgeObject(metadata=metadata,nosql_service=nosql_service,embedding=False)
        upload(target)
        print(well_color_data)

    print("Protocol execution complete")
    ot2.close_session()
    print("Session closed")

if __name__ == "__main__":
    setup_target(0.2,0.8,0.0)