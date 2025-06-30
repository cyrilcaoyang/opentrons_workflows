from prefect import flow, get_run_logger
from pathlib import Path
import json
from src.opentrons_workflows.opentrons_control import connect, RobotCommandError
from src.opentrons_workflows.prefect_tasks import robust_task

# ======================================================================================================================
# Define protocol-specific tasks using the @robust_task decorator
# ======================================================================================================================

@robust_task(retries=2, retry_delay_seconds=5)
def home_robot(ot):
    """Homes the robot."""
    ot.home()

@robust_task()
def load_labware(ot, *args, **kwargs):
    """Loads a piece of labware."""
    ot.load_labware(*args, **kwargs)

@robust_task()
def load_instrument(ot, *args, **kwargs):
    """Loads an instrument."""
    ot.load_instrument(*args, **kwargs)
    
@robust_task()
def load_module(ot, *args, **kwargs):
    """Loads a hardware module."""
    ot.load_module(*args, **kwargs)

@robust_task(retries=2, retry_delay_seconds=5)
def move_labware(ot, *args, **kwargs):
    """Moves labware with the gripper."""
    ot.move_labware_with_gripper(*args, **kwargs)

@robust_task()
def hs_latch_close(ot, *args, **kwargs):
    """Closes the Heater-Shaker latch."""
    ot.hs_latch_close(*args, **kwargs)

@robust_task()
def hs_latch_open(ot, *args, **kwargs):
    """Opens the Heater-Shaker latch."""
    ot.hs_latch_open(*args, **kwargs)
    
@robust_task()
def set_shake_speed(ot, hs_nickname: str, rpm: int):
    """Safely sets the shaker speed."""
    logger = get_run_logger()
    if not (200 <= rpm <= 3000):
        logger.warning(f"RPM {rpm} is outside safe range (200-3000). Setting to 0.")
        rpm = 0
    ot.set_hs_shake_speed(nickname=hs_nickname, rpm=rpm)

@robust_task()
def delay(ot, *args, **kwargs):
    """Pauses the protocol for a set duration."""
    ot.delay(*args, **kwargs)

@robust_task(retries=2, retry_delay_seconds=5)
def pick_up_tip(ot, *args, **kwargs):
    """Picks up a tip."""
    ot.pick_up_tip(*args, **kwargs)

@robust_task()
def aspirate(ot, *args, **kwargs):
    """Aspirates liquid."""
    ot.aspirate(*args, **kwargs)

@robust_task()
def dispense(ot, *args, **kwargs):
    """Dispenses liquid."""
    ot.dispense(*args, **kwargs)

@robust_task()
def return_tip(ot, *args, **kwargs):
    """Returns the tip to the rack."""
    ot.return_tip(*args, **kwargs)

# ======================================================================================================================
# Main Flow
# ======================================================================================================================

@flow(log_prints=True)
def demo_flex_robust(simulation: bool = True):
    """
    A demo protocol for the Opentrons Flex, showcasing module and gripper control.
    This protocol performs a series of actions, including:
    - Loading a Heater-Shaker module.
    - Using the gripper to move a plate onto the Heater-Shaker.
    - Shaking the plate.
    - Using the gripper to move the plate back to the deck.
    - Moving a tip rack with the gripper.
    - Basic pipetting actions.
    """
    logger = get_run_logger()
    ot = None
    try:
        # --- 0. Connect to Robot ---
        logger.info("Connecting to the robot...")
        ot = connect(host_alias="otflex_local", simulation=simulation)
        home_robot(ot)

        # --- 1. Load Labware, Modules, and Instruments (Setup - no tasks needed) ---
        logger.info("Loading hardware...")
        load_labware(ot, nickname="plate_96_1", location="B3", load_name="corning_96_wellplate_360ul_flat")

        with open("settings/movable_tiprack_1000ul.json", 'r') as f:
            tiprack_1000_def = json.load(f)
        load_labware(ot, nickname="tiprack_1000", location="B1", labware_def=tiprack_1000_def)

        load_instrument(ot, nickname="p1000", instrument_name="flex_1channel_1000", mount="left", tip_racks=['tiprack_1000'])
        load_module(ot, nickname="hs", module_name="heaterShakerModuleV1", location="A1", adapter_name="opentrons_universal_flat_adapter")

        # --- 2. Gripper and Heater-Shaker Sequence (using robust tasks) ---
        logger.info("Starting Heater-Shaker sequence...")
        move_labware(ot, labware_nickname="plate_96_1", new_location="hs_adapter")

        hs_latch_close(ot, nickname="hs")
        set_shake_speed(ot, hs_nickname="hs", rpm=500)
        delay(ot, seconds=10)
        set_shake_speed(ot, hs_nickname="hs", rpm=0)
        hs_latch_open(ot, nickname="hs")

        move_labware(ot, labware_nickname="plate_96_1", new_location="'B3'")

        # --- 3. Move Tip Rack and Pipette (using robust tasks) ---
        move_labware(ot, labware_nickname="tiprack_1000", new_location="'C1'")

        logger.info("Moving tip rack and starting pipetting...")
        pick_up_tip(ot, pip_name="p1000")
        aspirate(ot, pip_name="p1000", volume=200, location="plate_96_1['A1'].bottom(1)")
        dispense(ot, pip_name="p1000", volume=200, location="plate_96_1['A2'].top(-1)")
        return_tip(ot, pip_name="p1000")

    except Exception as e:
        logger.error(f"An unexpected error occurred in the flow: {e}", exc_info=True)

    finally:
        # --- 4. Finalize ---
        if ot:
            logger.info("Protocol finished or failed. Closing session.")
            ot.close()


if __name__ == "__main__":
    demo_flex_robust(simulation=False) 