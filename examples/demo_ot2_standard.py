# examples/demo_ot2_standard.py
"""
A demonstration of a standard Prefect workflow on the Opentrons OT-2.

This script shows how to use the library with the standard `@task` decorator
from Prefect, for users who prefer not to use the custom `@robust_task`.
"""
from prefect import flow, task, get_run_logger
from src.opentrons_workflows.opentrons_control import connect, RobotCommandError

# --- Task Definitions ---
# For a standard workflow, we define tasks that wrap the robot methods.
# Error handling must be done manually within each task.

@task
def load_labware_task(ot, nickname: str, load_name: str, location: str):
    logger = get_run_logger()
    logger.info(f"Loading labware '{load_name}' into slot {location} as '{nickname}'.")
    ot.load_labware(nickname=nickname, load_name=load_name, location=location)

@task
def load_instrument_task(ot, nickname: str, instrument_name: str, mount: str, tip_racks: list):
    logger = get_run_logger()
    logger.info(f"Loading instrument '{instrument_name}' into {mount} mount as '{nickname}'.")
    ot.load_instrument(nickname=nickname, instrument_name=instrument_name, mount=mount, tip_racks=tip_racks)

@task(retries=2, retry_delay_seconds=5)
def pick_up_tip_task(ot, pip_name: str):
    logger = get_run_logger()
    logger.info(f"Pipette '{pip_name}' picking up tip.")
    ot.pick_up_tip(pip_name)

@task
def aspirate_task(ot, pip_name: str, volume: float, location: str):
    logger = get_run_logger()
    logger.info(f"Pipette '{pip_name}' aspirating {volume}uL from {location}.")
    ot.aspirate(pip_name, volume, location)

@task
def dispense_task(ot, pip_name: str, volume: float, location: str):
    logger = get_run_logger()
    logger.info(f"Pipette '{pip_name}' dispensing {volume}uL to {location}.")
    ot.dispense(pip_name, volume, location)

@task(retries=2, retry_delay_seconds=5)
def drop_tip_task(ot, pip_name: str):
    logger = get_run_logger()
    logger.info(f"Pipette '{pip_name}' dropping tip.")
    ot.drop_tip(pip_name)

# --- Flow Definition ---

@flow
def standard_ot2_transfer_flow():
    """
    A Prefect flow to demonstrate a simple transfer on a simulated OT-2
    using standard Prefect tasks.
    """
    logger = get_run_logger()
    ot = None
    try:
        logger.info("Connecting to simulated OT-2 robot...")
        ot = connect(host_alias="ot2_sim_local", simulation=True)
        logger.info("✅ Connection successful.")

        # --- Setup ---
        load_labware_task(ot, "plate_96", "corning_96_wellplate_360ul_flat", "1")
        load_labware_task(ot, "tip_rack", "opentrons_96_tiprack_300ul", "2")
        load_instrument_task(ot, "p300", "p300_single_gen2", "right", tip_racks=['tip_rack'])

        # --- Protocol ---
        logger.info("Starting simple transfer protocol...")
        pick_up_tip_task(ot, "p300")
        aspirate_task(ot, "p300", 50, "plate_96['A1']")
        dispense_task(ot, "p300", 50, "plate_96['B2']")
        drop_tip_task(ot, "p300")
        logger.info("✅ Protocol finished.")

    except RobotCommandError as e:
        logger.error(f"❌ A robot command failed: {e}")
    except Exception as e:
        logger.error(f"❌ An unexpected error occurred: {e}")
    finally:
        if ot:
            logger.info("Closing robot connection.")
            ot.close()
            logger.info("✅ Connection closed.")

if __name__ == "__main__":
    standard_ot2_transfer_flow() 