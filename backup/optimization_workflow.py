from OT2Demo.src.OT2wrapper import OpenTrons
from prefect import flow,task,serve
from LabMind import KnowledgeObject,nosql_service
from LabMind.Utils import upload
from optimization_algorithm import optimize
import pandas as pd

session_id="test"

def extract_previous_experiments(session_id):
    db="OT2"
    collection = "experiments"
    # collection to dataframe for this session
    collection = nosql_service[db][collection]
    df = pd.DataFrame(list(collection.find({"session_id": session_id})))
    if len(df) == 0:
        return []
    else:
        relevant_columns = ["R", "G", "B", "mae"]
        filtered_df = df[relevant_columns]
        # Convert the filtered DataFrame to a list of dictionaries
        past_experiments = filtered_df.to_dict(orient="records")
        return past_experiments

def find_unused_wells():
    db = "OT2"
    collection = "wells"
    collection = nosql_service[db][collection]
    df = pd.DataFrame(list(collection.find({"status": "empty"})))
    # return a list of wells that are empty
    empty_wells = df["well"].tolist()
    if len(empty_wells) == 0:
        raise ValueError("No empty wells found")
    return empty_wells


@flow(log_prints=True)
def optimization_workflow():
    # find parameters
    past_experiments = extract_previous_experiments(session_id)
    # optimize
    next_parameters = optimize(past_experiments)
    # find empty wells
    empty_well = find_unused_wells()[0]
    



@flow(log_prints=True)
def setup_target(R,G,B,mix_well="H11"):
    # check if they add up to 1
    total = R+G+B
    if total != 1:
        raise ValueError("The sum of the proportions must be 1")
    ot2 = OpenTrons()
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
                    "unique_fields": unique_fields}
        
        target = KnowledgeObject(metadata=metadata,nosql_service=nosql_service,embedding=False)
        upload(target)
        print(well_color_data)

    print("Protocol execution complete")
    ot2.close_session()
    print("Session closed")