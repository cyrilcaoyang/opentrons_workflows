"""
matterlab_opentrons

A Python package to control Opentrons robots, with integration for
Prefect workflow orchestration.
"""

import logging

from .deck import Deck
from .pipette import Instrument, Pipette
from .gripper import Gripper
from .labware_generator import LabwareGenerator
from .opentrons_control import connect, OT2, Flex
from .prefect_tasks import robust_task

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

__all__ = [
    "connect",
    "OT2", 
    "Flex",
    "Deck",
    "Instrument",
    "Pipette",
    "Gripper",
    "LabwareGenerator",
    "robust_task",
]