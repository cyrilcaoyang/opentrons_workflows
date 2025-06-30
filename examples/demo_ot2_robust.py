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
def blow_out(ot, *args, **kwargs):
    """Blows out liquid."""
    ot.blow_out(*args, **kwargs)

@robust_task()
def return_tip(ot, *args, **kwargs):
    """Returns the tip to the rack."""
    ot.return_tip(*args, **kwargs)

@robust_task(retries=2, retry_delay_seconds=5)
def drop_tip(ot, *args, **kwargs):
    """Drops the tip in the trash."""
    ot.drop_tip(*args, **kwargs)

@robust_task()
def remove_labware(ot, *args, **kwargs):
    """Removes a labware from the deck."""
    ot.remove_labware(*args, **kwargs)

# ======================================================================================================================
# Main Flow
# ======================================================================================================================

@flow(log_prints=True)
def demo_ot2_robust(simulation: bool = True):
    """
    A demo protocol for the OT-2, showcasing the robust task pattern.
    """
    logger = get_run_logger()
    ot = None
    try:
        # --- 0. Connect to Robot ---
        logger.info("Connecting to the robot...")
        ot = connect(host_alias="ot2_local", simulation=simulation)
        home_robot(ot)

        # --- 1. Load Labware Definitions ---
        logger.info("Loading labware definitions...")
        settings_path = Path(__file__).parent.parent / "settings"
        with open(settings_path / "matterlab_24_vialplate_3700ul.json", "r") as f:
            plate_24_def = json.load(f)
        with open(settings_path / "matterlab_1_beaker_30000ul.json", "r") as f:
            beaker_def = json.load(f)
        with open(settings_path / "matterlab_96_tiprack_10ul.json", "r") as f:
            tiprack_10ul_def = json.load(f)

        # --- 2. Load Labware onto the Deck ---
        logger.info("Loading labware onto the deck...")
        load_labware(ot, nickname="plate_96_1", location="1", load_name="corning_96_wellplate_360ul_flat")
        load_labware(ot, nickname="beaker", location="4", labware_def=beaker_def)
        load_labware(ot, nickname="vial_24_1", location="5", labware_def=plate_24_def)
        load_labware(ot, nickname="vial_24_2", location="6", labware_def=plate_24_def)

        # --- 3. Load Tip Racks ---
        logger.info("Loading tip racks...")
        load_labware(ot, nickname="tips_10ul", location="7", labware_def=tiprack_10ul_def)
        load_labware(ot, nickname="tips_300ul", location="8", load_name="opentrons_96_tiprack_300ul")

        # --- 4. Load Instruments (Pipettes) ---
        logger.info("Loading instruments...")
        load_instrument(ot, nickname="p20", instrument_name="p20_single_gen2", mount="left", tip_racks=['tips_10ul'])
        load_instrument(ot, nickname="p300", instrument_name="p300_single_gen2", mount="right", tip_racks=['tips_300ul'])

        # --- 5. Main Protocol Steps ---
        logger.info("Distributing from beaker with p300...")
        pick_up_tip(ot, pip_name="p300")
        for i in range(48):
            row = chr(ord('A') + i // 12)
            col = i % 12 + 1
            aspirate(ot, pip_name="p300", volume=150, location="beaker['A1'].bottom(5)")
            dispense(ot, pip_name="p300", volume=150, location=f"plate_96_1['{row}{col}'].top(-1)")
            blow_out(ot, pip_name="p300", location=f"plate_96_1['{row}{col}'].top()")
        return_tip(ot, pip_name="p300")

        logger.info("Transferring from vial_24_1 with p20...")
        for i in range(24):
            s_row, s_col = chr(ord('A') + i // 6), i % 6 + 1
            t_row, t_col = chr(ord('A') + i // 12), i % 12 + 1
            pick_up_tip(ot, pip_name="p20")
            aspirate(ot, pip_name="p20", volume=10, location=f"vial_24_1['{s_row}{s_col}'].bottom(5)")
            dispense(ot, pip_name="p20", volume=10, location=f"plate_96_1['{t_row}{t_col}'].top(-1)")
            blow_out(ot, pip_name="p20", location=f"plate_96_1['{t_row}{t_col}'].top()")
            drop_tip(ot, pip_name="p20")

        logger.info("Transferring from vial_24_2 with p20...")
        for i in range(24):
            s_row, s_col = chr(ord('A') + i // 6), i % 6 + 1
            t_row, t_col = chr(ord('C') + i // 12), i % 12 + 1
            pick_up_tip(ot, pip_name="p20")
            aspirate(ot, pip_name="p20", volume=10, location=f"vial_24_2['{s_row}{s_col}'].bottom(5)")
            dispense(ot, pip_name="p20", volume=10, location=f"plate_96_1['{t_row}{t_col}'].bottom(1)")
            blow_out(ot, pip_name="p20", location=f"plate_96_1['{t_row}{t_col}'].top()")
            drop_tip(ot, pip_name="p20")

        # --- 6. Demonstrate Removing and Reloading Labware ---
        logger.info("Demonstrating labware removal and reload...")
        remove_labware(ot, labware_nickname="plate_96_1")
        load_labware(ot, nickname="plate_96_2", location="2", load_name="corning_96_wellplate_360ul_flat")

        pick_up_tip(ot, pip_name="p300")
        aspirate(ot, pip_name="p300", volume=100, location="beaker['A1'].bottom(5)")
        dispense(ot, pip_name="p300", volume=100, location="plate_96_2['H12'].top(-1)")
        blow_out(ot, pip_name="p300", location="plate_96_2['H12'].top()")
        return_tip(ot, pip_name="p300")

    except Exception as e:
        logger.error(f"An unexpected error occurred in the flow: {e}", exc_info=True)

    finally:
        if ot:
            logger.info("Protocol finished or failed. Closing session.")
            ot.close()


if __name__ == "__main__":
    demo_ot2_robust(simulation=False) 