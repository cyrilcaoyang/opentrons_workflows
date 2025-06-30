# examples/demo_flex_gripper_robust.py
"""
A robust demonstration of the gripper functionality on the Opentrons Flex.

This script shows how to connect to a Flex robot, move the gripper,
and control its jaws, all within a Prefect flow.
"""
from prefect import flow
from src.matterlab_opentrons.prefect_tasks import robust_task
from src.matterlab_opentrons.OpenTronsControl import connect, RobotCommandError

@robust_task(retries=2, retry_delay_seconds=5)
def move_gripper_jaw_task(ot, action: str):
    """
    A robust task to control the gripper jaws.

    NOTE: This task is defined locally for debugging. Once fully tested,
    it will be moved to the shared `src/matterlab_opentrons/prefect_tasks.py`.
    """
    print(f"Attempting to perform gripper action: '{action}'")
    ot.move_gripper(action)
    print(f"✅ Gripper jaw action '{action}' completed successfully.")

@robust_task(retries=2, retry_delay_seconds=5)
def move_gripper_to_location_task(ot, gripper_name: str, labware_name: str, well: str):
    """
    A robust task to move the gripper to a specific location.

    NOTE: This task is defined locally for debugging. Once fully tested,
    it will be moved to the shared `src/matterlab_opentrons/prefect_tasks.py`.
    """
    location = f"{labware_name}['{well}'].top()"
    print(f"Attempting to move gripper '{gripper_name}' to {location}")
    ot.move_gripper_to(gripper_name, location)
    print(f"✅ Gripper '{gripper_name}' moved successfully to {location}.")


@flow
def robust_gripper_flow():
    """
    A Prefect flow to demonstrate robust gripper control on a simulated Flex.
    """
    ot = None
    try:
        print("Connecting to simulated Flex robot...")
        ot = connect(host_alias="otflex_sim_local", simulation=True)
        print("✅ Connection successful.")

        # --- Setup ---
        ot.load_instrument(nickname="gripper", instrument_name="flex_gripper", mount="left")
        ot.load_labware(nickname="plate_96", location="D1", load_name="corning_96_wellplate_360ul_flat")

        # --- Protocol ---
        # 1. Demonstrate moving the gripper to a location
        move_gripper_to_location_task.fn(ot, "gripper", "plate_96", "A1")

        # 2. Demonstrate jaw actions
        move_gripper_jaw_task.fn(ot, 'grip')
        move_gripper_jaw_task.fn(ot, 'ungrip')

    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")

    finally:
        if ot:
            print("Closing robot connection.")
            ot.close()
            print("✅ Connection closed.")

if __name__ == "__main__":
    robust_gripper_flow() 