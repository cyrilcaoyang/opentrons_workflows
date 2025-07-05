"""
Opentrons Control using Standard Protocol API v2 with Advanced Control
This module provides a wrapper around the standard Opentrons Protocol API v2
using opentrons.execute.get_protocol_api() for direct robot control.

Based on: https://docs.opentrons.com/v2/new_advanced_running.html
"""

import json
import os
import datetime
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Union, List
import opentrons.execute
from opentrons.protocol_api import ProtocolContext, InstrumentContext, Labware
from .logging_config import get_logger, setup_default_logging


class DeckV2:
    """
    Deck management using standard Protocol API v2.
    Provides a wrapper around the protocol context's labware loading.
    """
    
    def __init__(self, protocol_context: ProtocolContext, robot_control):
        self.protocol_context = protocol_context
        self.robot_control = robot_control
        self.logger = robot_control.logger
        self.loaded_labware = {}  # Track loaded labware by slot
        
    def load_labware(self, labware_name: str, location: Union[str, int], 
                     label: Optional[str] = None, namespace: Optional[str] = None,
                     version: Optional[int] = None) -> Labware:
        """
        Load labware onto the deck using the standard Protocol API v2.
        
        :param labware_name: The name of the labware to load
        :param location: The deck slot (e.g., '1', 'A1' for Flex)
        :param label: Optional custom label for the labware
        :param namespace: Optional namespace for custom labware
        :param version: Optional version for custom labware
        :return: The loaded labware object
        """
        try:
            # Convert location to string if it's an integer
            location_str = str(location)
            
            # Load the labware using the standard API
            labware = self.protocol_context.load_labware(
                labware_name, 
                location_str, 
                label=label,
                namespace=namespace,
                version=version
            )
            
            # Track the loaded labware
            self.loaded_labware[location_str] = {
                'labware': labware,
                'name': labware_name,
                'label': label or labware_name
            }
            
            self.logger.info(f"Loaded labware '{labware_name}' in slot {location_str}")
            return labware
            
        except Exception as e:
            self.logger.error(f"Failed to load labware '{labware_name}' in slot {location}: {e}")
            raise
    
    def get_labware(self, location: Union[str, int]) -> Optional[Labware]:
        """Get labware from a specific slot."""
        location_str = str(location)
        labware_info = self.loaded_labware.get(location_str)
        return labware_info['labware'] if labware_info else None
    
    def list_labware(self) -> Dict[str, Dict[str, Any]]:
        """List all loaded labware."""
        return {slot: {'name': info['name'], 'label': info['label']} 
                for slot, info in self.loaded_labware.items()}


class PipetteV2:
    """
    Pipette management using standard Protocol API v2.
    Provides a wrapper around the protocol context's instrument loading.
    """
    
    def __init__(self, protocol_context: ProtocolContext, robot_control, 
                 pipette_name: str, mount: str, tip_racks: Optional[List[Labware]] = None):
        self.protocol_context = protocol_context
        self.robot_control = robot_control
        self.logger = robot_control.logger
        self.pipette_name = pipette_name
        self.mount = mount
        
        # Load the pipette using the standard API
        self.instrument = self.protocol_context.load_instrument(
            pipette_name, mount, tip_racks=tip_racks or []
        )
        
        self.logger.info(f"Loaded pipette '{pipette_name}' on mount '{mount}'")
    
    def pick_up_tip(self, location=None):
        """Pick up a tip."""
        try:
            if location:
                self.instrument.pick_up_tip(location)
            else:
                self.instrument.pick_up_tip()
            self.logger.debug(f"Picked up tip on {self.mount} mount")
        except Exception as e:
            self.logger.error(f"Failed to pick up tip: {e}")
            raise
    
    def drop_tip(self, location=None):
        """Drop the current tip."""
        try:
            if location:
                self.instrument.drop_tip(location)
            else:
                self.instrument.drop_tip()
            self.logger.debug(f"Dropped tip on {self.mount} mount")
        except Exception as e:
            self.logger.error(f"Failed to drop tip: {e}")
            raise
    
    def return_tip(self):
        """Return the tip to its original location."""
        try:
            self.instrument.return_tip()
            self.logger.debug(f"Returned tip on {self.mount} mount")
        except Exception as e:
            self.logger.error(f"Failed to return tip: {e}")
            raise
    
    def aspirate(self, volume: float, location, rate: float = 1.0):
        """Aspirate liquid from a location."""
        try:
            self.instrument.aspirate(volume, location, rate)
            self.logger.debug(f"Aspirated {volume}µL from {location}")
        except Exception as e:
            self.logger.error(f"Failed to aspirate: {e}")
            raise
    
    def dispense(self, volume: float, location, rate: float = 1.0):
        """Dispense liquid to a location."""
        try:
            self.instrument.dispense(volume, location, rate)
            self.logger.debug(f"Dispensed {volume}µL to {location}")
        except Exception as e:
            self.logger.error(f"Failed to dispense: {e}")
            raise
    
    def transfer(self, volume: float, source, dest, **kwargs):
        """Transfer liquid from source to destination."""
        try:
            self.instrument.transfer(volume, source, dest, **kwargs)
            self.logger.debug(f"Transferred {volume}µL from {source} to {dest}")
        except Exception as e:
            self.logger.error(f"Failed to transfer: {e}")
            raise
    
    def mix(self, repetitions: int, volume: float, location, rate: float = 1.0):
        """Mix at a location."""
        try:
            self.instrument.mix(repetitions, volume, location, rate)
            self.logger.debug(f"Mixed {repetitions}x {volume}µL at {location}")
        except Exception as e:
            self.logger.error(f"Failed to mix: {e}")
            raise
    
    def blow_out(self, location=None):
        """Blow out liquid."""
        try:
            if location:
                self.instrument.blow_out(location)
            else:
                self.instrument.blow_out()
            self.logger.debug(f"Blew out at {location or 'current location'}")
        except Exception as e:
            self.logger.error(f"Failed to blow out: {e}")
            raise


class GripperV2:
    """
    Gripper management using standard Protocol API v2 (Flex only).
    Provides a wrapper around the protocol context's gripper loading.
    """
    
    def __init__(self, protocol_context: ProtocolContext, robot_control):
        self.protocol_context = protocol_context
        self.robot_control = robot_control
        self.logger = robot_control.logger
        
        # Load the gripper using the standard API
        self.gripper = self.protocol_context.load_instrument('flex_gripper', 'extension')
        self.logger.info("Loaded gripper on extension mount")
    
    def grip(self, force: Optional[float] = None):
        """Grip with the gripper."""
        try:
            if force:
                self.gripper.grip(force)
            else:
                self.gripper.grip()
            self.logger.debug("Gripper gripped")
        except Exception as e:
            self.logger.error(f"Failed to grip: {e}")
            raise
    
    def ungrip(self):
        """Release the gripper."""
        try:
            self.gripper.ungrip()
            self.logger.debug("Gripper released")
        except Exception as e:
            self.logger.error(f"Failed to ungrip: {e}")
            raise


class OpenTronsV2Base(ABC):
    """
    Base class for controlling Opentrons robots using the standard Protocol API v2.
    Uses opentrons.execute.get_protocol_api() for direct robot control.
    
    Based on: https://docs.opentrons.com/v2/new_advanced_running.html
    """
    
    def __init__(self, api_level: str = '2.18', log_dir: str = "logs", 
                 custom_logger=None, **kwargs):
        """
        Initialize the robot connection using standard Protocol API v2.
        
        :param api_level: The API level to use (e.g., '2.18')
        :param log_dir: Directory for log files
        :param custom_logger: Optional custom logger
        """
        # Set up logging
        if custom_logger is not None:
            self.logger = custom_logger
        else:
            self.logger = setup_default_logging(log_dir=log_dir)
        
        self.api_level = api_level
        
        # Create protocol context using the standard approach
        try:
            self.protocol_context = opentrons.execute.get_protocol_api(api_level)
            self.logger.info(f"Created protocol context with API level {api_level}")
        except Exception as e:
            self.logger.error(f"Failed to create protocol context: {e}")
            raise
        
        # Initialize components
        self.deck = DeckV2(self.protocol_context, self)
        self.instruments = {}  # Track loaded instruments
        
        # Robot should be homed first - this is critical!
        self.logger.info("Robot must be homed before use. Call robot.home() first!")
    
    def home(self):
        """
        Home the robot.
        This should be the first command you execute!
        """
        try:
            self.protocol_context.home()
            self.logger.info("Robot homed successfully")
        except Exception as e:
            self.logger.error(f"Failed to home robot: {e}")
            raise
    
    def load_pipette(self, pipette_name: str, mount: str, tip_racks: Optional[List[Labware]] = None) -> PipetteV2:
        """
        Load a pipette onto the robot.
        
        :param pipette_name: The API name of the pipette
        :param mount: The mount to attach the pipette to
        :param tip_racks: Optional list of tip racks for the pipette
        :return: A PipetteV2 object
        """
        if mount in self.instruments:
            raise ValueError(f"An instrument is already loaded on mount '{mount}'")
        
        pipette = PipetteV2(self.protocol_context, self, pipette_name, mount, tip_racks)
        self.instruments[mount] = pipette
        return pipette
    
    def pause(self, message: str = "Protocol paused"):
        """Pause the protocol."""
        try:
            self.protocol_context.pause(message)
            self.logger.info(f"Protocol paused: {message}")
        except Exception as e:
            self.logger.error(f"Failed to pause protocol: {e}")
            raise
    
    def resume(self):
        """Resume the protocol (note: this is typically handled by the robot interface)."""
        self.logger.info("Protocol resumed")
    
    def comment(self, message: str):
        """Add a comment to the protocol."""
        self.protocol_context.comment(message)
        self.logger.info(f"Comment: {message}")
    
    def delay(self, seconds: float, message: str = ""):
        """Add a delay to the protocol."""
        self.protocol_context.delay(seconds, message)
        self.logger.info(f"Delayed {seconds}s: {message}")
    
    def set_rail_lights(self, on: bool):
        """Control the rail lights."""
        try:
            self.protocol_context.set_rail_lights(on)
            self.logger.info(f"Rail lights {'on' if on else 'off'}")
        except Exception as e:
            self.logger.error(f"Failed to set rail lights: {e}")
            raise


class OT2V2(OpenTronsV2Base):
    """
    OT-2 robot using standard Protocol API v2 with Advanced Control.
    """
    
    def __init__(self, api_level: str = '2.18', **kwargs):
        super().__init__(api_level=api_level, **kwargs)
        self.robot_type = 'OT-2'


class FlexV2(OpenTronsV2Base):
    """
    Flex robot using standard Protocol API v2 with Advanced Control.
    """
    
    def __init__(self, api_level: str = '2.18', **kwargs):
        super().__init__(api_level=api_level, **kwargs)
        self.robot_type = 'Flex'
        
        # Set up robot-specific configurations
        try:
            self.protocol_context.set_rail_lights(True)  # Default lights on for Flex
        except Exception as e:
            self.logger.warning(f"Could not set rail lights: {e}")
    
    def load_gripper(self) -> GripperV2:
        """
        Load the gripper onto the Flex robot.
        
        :return: A GripperV2 object
        """
        if 'gripper' in self.instruments:
            raise ValueError("A gripper is already loaded")
        
        gripper = GripperV2(self.protocol_context, self)
        self.instruments['gripper'] = gripper
        return gripper


def connect_v2(robot_type: str = 'OT-2', api_level: str = '2.18',
               log_dir: str = "logs", custom_logger=None, **kwargs):
    """
    Factory function to create robot connections using standard Protocol API v2.
    Uses opentrons.execute.get_protocol_api() for direct robot control.
    
    Based on: https://docs.opentrons.com/v2/new_advanced_running.html
    
    :param robot_type: The type of robot ('OT-2' or 'Flex')
    :param api_level: The API level to use (e.g., '2.18')
    :param log_dir: Directory for log files
    :param custom_logger: Optional custom logger
    :param kwargs: Additional arguments
    :return: An instance of OT2V2 or FlexV2
    """
    if robot_type == 'OT-2':
        return OT2V2(api_level=api_level, log_dir=log_dir, 
                     custom_logger=custom_logger, **kwargs)
    elif robot_type == 'Flex':
        return FlexV2(api_level=api_level, log_dir=log_dir, 
                      custom_logger=custom_logger, **kwargs)
    else:
        raise ValueError(f"Unsupported robot type: {robot_type}")


# Convenience function for Jupyter notebook usage
def get_protocol_api_v2(api_level: str = '2.18', robot_type: str = 'OT-2', **kwargs):
    """
    Convenience function for Jupyter notebook usage.
    Creates a robot connection and homes it automatically.
    
    :param api_level: The API level to use
    :param robot_type: The type of robot ('OT-2' or 'Flex')
    :param kwargs: Additional arguments
    :return: A robot instance that's ready to use
    """
    robot = connect_v2(robot_type=robot_type, api_level=api_level, **kwargs)
    
    # Auto-home for convenience (following the official pattern)
    print("🏠 Homing robot...")
    robot.home()
    print("✅ Robot ready!")
    
    return robot 