"""
Opentrons State Tracking Module

This module provides convenient classes to query and monitor the robot's built-in state tracking.
The Opentrons API already tracks all states - this module just provides easier access to that information.

Based on Opentrons API v2 state tracking capabilities:
- Tip tracking via has_tip properties
- Liquid volume tracking via current_liquid_volume
- Deck state via protocol.deck
- Pipette state via current_volume, has_tip
- Module states via their specific properties
"""

from typing import Dict, List, Optional, Any, Union
import logging
from datetime import datetime


class PipetteState:
    """Query pipette state from the robot's built-in tracking."""
    
    def __init__(self, pipette_context):
        """
        Initialize with an Opentrons InstrumentContext.
        
        :param pipette_context: opentrons.protocol_api.InstrumentContext
        """
        self.pipette = pipette_context
        self.logger = logging.getLogger(f"state.{pipette_context.name}")
    
    @property
    def has_tip(self) -> bool:
        """Check if pipette has a tip attached."""
        return self.pipette.has_tip
    
    @property
    def current_volume(self) -> float:
        """Get current liquid volume in pipette (µL)."""
        return self.pipette.current_volume
    
    @property
    def max_volume(self) -> float:
        """Get pipette maximum volume capacity (µL)."""
        return self.pipette.max_volume
    
    @property
    def min_volume(self) -> float:
        """Get pipette minimum volume (µL)."""
        return self.pipette.min_volume
    
    @property
    def starting_tip(self):
        """Get the next tip position the pipette will pick up from."""
        return self.pipette.starting_tip
    
    @property
    def name(self) -> str:
        """Get pipette name."""
        return self.pipette.name
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive pipette status."""
        return {
            'name': self.name,
            'has_tip': self.has_tip,
            'current_volume': self.current_volume,
            'max_volume': self.max_volume,
            'min_volume': self.min_volume,
            'starting_tip': str(self.starting_tip) if self.starting_tip else None,
            'available_volume': self.max_volume - self.current_volume,
            'volume_percentage': (self.current_volume / self.max_volume) * 100,
            'timestamp': datetime.now().isoformat()
        }
    
    def log_status(self, message: str = "Pipette status"):
        """Log current pipette status."""
        status = self.get_status_summary()
        self.logger.info(f"{message}: {status}")


class WellState:
    """Query well state from the robot's built-in tracking."""
    
    def __init__(self, well_context):
        """
        Initialize with an Opentrons WellCore.
        
        :param well_context: opentrons.protocol_api.Well
        """
        self.well = well_context
        self.logger = logging.getLogger(f"state.{well_context.well_name}")
    
    @property
    def current_liquid_volume(self) -> float:
        """Get current liquid volume in well (µL). Requires liquid initialization."""
        try:
            return self.well.current_liquid_volume
        except Exception as e:
            self.logger.warning(f"Could not get liquid volume for {self.well.well_name}: {e}")
            return 0.0
    
    @property
    def current_liquid_height(self) -> float:
        """Get current liquid height in well (mm). Requires liquid initialization."""
        try:
            return self.well.current_liquid_height
        except Exception as e:
            self.logger.warning(f"Could not get liquid height for {self.well.well_name}: {e}")
            return 0.0
    
    @property
    def has_tip(self) -> bool:
        """Check if this tip rack well has an unused tip (for tip racks only)."""
        try:
            return self.well.has_tip
        except AttributeError:
            # Not a tip rack well
            return False
    
    @property
    def max_volume(self) -> float:
        """Get well maximum volume capacity (µL)."""
        return self.well.max_volume
    
    @property
    def diameter(self) -> Optional[float]:
        """Get well diameter (mm) for circular wells."""
        return self.well.diameter
    
    @property
    def depth(self) -> float:
        """Get well depth (mm)."""
        return self.well.depth
    
    @property
    def well_name(self) -> str:
        """Get well name (e.g., 'A1')."""
        return self.well.well_name
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive well status."""
        return {
            'well_name': self.well_name,
            'current_liquid_volume': self.current_liquid_volume,
            'current_liquid_height': self.current_liquid_height,
            'max_volume': self.max_volume,
            'has_tip': self.has_tip,
            'diameter': self.diameter,
            'depth': self.depth,
            'fill_percentage': (self.current_liquid_volume / self.max_volume) * 100 if self.max_volume > 0 else 0,
            'timestamp': datetime.now().isoformat()
        }
    
    def log_status(self, message: str = "Well status"):
        """Log current well status."""
        status = self.get_status_summary()
        self.logger.info(f"{message}: {status}")


class LabwareState:
    """Query labware state from the robot's built-in tracking."""
    
    def __init__(self, labware_context):
        """
        Initialize with an Opentrons LabwareCore.
        
        :param labware_context: opentrons.protocol_api.Labware
        """
        self.labware = labware_context
        self.logger = logging.getLogger(f"state.{labware_context.load_name}")
    
    @property
    def load_name(self) -> str:
        """Get labware load name."""
        return self.labware.load_name
    
    @property
    def parent(self) -> str:
        """Get parent location (deck slot or module)."""
        return str(self.labware.parent)
    
    def get_all_wells_status(self) -> List[Dict[str, Any]]:
        """Get status of all wells in this labware."""
        wells_status = []
        for well in self.labware.wells():
            well_state = WellState(well)
            wells_status.append(well_state.get_status_summary())
        return wells_status
    
    def get_wells_with_liquid(self) -> List[Dict[str, Any]]:
        """Get only wells that have liquid."""
        wells_with_liquid = []
        for well in self.labware.wells():
            well_state = WellState(well)
            if well_state.current_liquid_volume > 0:
                wells_with_liquid.append(well_state.get_status_summary())
        return wells_with_liquid
    
    def get_available_tips(self) -> List[str]:
        """Get list of tip positions that have tips (for tip racks only)."""
        available_tips = []
        for well in self.labware.wells():
            well_state = WellState(well)
            if well_state.has_tip:
                available_tips.append(well.well_name)
        return available_tips
    
    def get_used_tips(self) -> List[str]:
        """Get list of tip positions that have been used (for tip racks only)."""
        used_tips = []
        for well in self.labware.wells():
            well_state = WellState(well)
            if not well_state.has_tip:
                used_tips.append(well.well_name)
        return used_tips
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive labware status."""
        wells_status = self.get_all_wells_status()
        wells_with_liquid = len([w for w in wells_status if w['current_liquid_volume'] > 0])
        available_tips = len([w for w in wells_status if w['has_tip']])
        
        return {
            'load_name': self.load_name,
            'parent': self.parent,
            'total_wells': len(wells_status),
            'wells_with_liquid': wells_with_liquid,
            'available_tips': available_tips,
            'used_tips': len(wells_status) - available_tips,
            'timestamp': datetime.now().isoformat()
        }
    
    def log_status(self, message: str = "Labware status"):
        """Log current labware status."""
        status = self.get_status_summary()
        self.logger.info(f"{message}: {status}")
    
    def reset_tip_tracking(self):
        """Reset tip tracking for this labware (marks all tips as available)."""
        if hasattr(self.labware, 'reset'):
            self.labware.reset()
            self.logger.info(f"Reset tip tracking for {self.load_name}")


class DeckState:
    """Query deck state from the robot's built-in tracking."""
    
    def __init__(self, protocol_context):
        """
        Initialize with an Opentrons ProtocolContext.
        
        :param protocol_context: opentrons.protocol_api.ProtocolContext
        """
        self.protocol = protocol_context
        self.logger = logging.getLogger("state.deck")
    
    @property
    def loaded_labwares(self) -> Dict[str, Any]:
        """Get all loaded labware."""
        return self.protocol.loaded_labwares
    
    @property
    def loaded_modules(self) -> Dict[str, Any]:
        """Get all loaded modules."""
        return self.protocol.loaded_modules
    
    @property
    def loaded_instruments(self) -> Dict[str, Any]:
        """Get all loaded instruments."""
        return self.protocol.loaded_instruments
    
    def get_slot_contents(self, slot: Union[str, int]) -> Optional[Any]:
        """Get contents of a specific deck slot."""
        return self.protocol.deck[str(slot)]
    
    def get_empty_slots(self) -> List[str]:
        """Get list of empty deck slots."""
        empty_slots = []
        for slot, contents in self.protocol.deck.items():
            if contents is None:
                empty_slots.append(slot)
        return empty_slots
    
    def get_occupied_slots(self) -> Dict[str, str]:
        """Get dictionary of occupied slots and their contents."""
        occupied = {}
        for slot, contents in self.protocol.deck.items():
            if contents is not None:
                occupied[slot] = str(contents)
        return occupied
    
    def get_all_labware_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all loaded labware."""
        labware_status = {}
        for name, labware in self.loaded_labwares.items():
            labware_state = LabwareState(labware)
            labware_status[name] = labware_state.get_status_summary()
        return labware_status
    
    def get_all_pipette_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all loaded pipettes."""
        pipette_status = {}
        for mount, pipette in self.loaded_instruments.items():
            if pipette is not None:
                pipette_state = PipetteState(pipette)
                pipette_status[mount] = pipette_state.get_status_summary()
        return pipette_status
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive deck status."""
        return {
            'empty_slots': self.get_empty_slots(),
            'occupied_slots': self.get_occupied_slots(),
            'total_labware': len(self.loaded_labwares),
            'total_modules': len(self.loaded_modules),
            'total_instruments': len([i for i in self.loaded_instruments.values() if i is not None]),
            'labware_status': self.get_all_labware_status(),
            'pipette_status': self.get_all_pipette_status(),
            'timestamp': datetime.now().isoformat()
        }
    
    def log_status(self, message: str = "Deck status"):
        """Log current deck status."""
        status = self.get_status_summary()
        self.logger.info(f"{message}: {status}")


class ModuleState:
    """Query module state from the robot's built-in tracking."""
    
    def __init__(self, module_context):
        """
        Initialize with an Opentrons ModuleContext.
        
        :param module_context: opentrons.protocol_api module (e.g., TemperatureModuleContext)
        """
        self.module = module_context
        self.logger = logging.getLogger(f"state.{module_context.model}")
    
    def get_temperature_status(self) -> Dict[str, Any]:
        """Get temperature module status (if applicable)."""
        status = {}
        if hasattr(self.module, 'current_temperature'):
            status['current_temperature'] = self.module.current_temperature
        if hasattr(self.module, 'target_temperature'):
            status['target_temperature'] = self.module.target_temperature
        if hasattr(self.module, 'status'):
            status['status'] = self.module.status
        return status
    
    def get_heater_shaker_status(self) -> Dict[str, Any]:
        """Get heater-shaker module status (if applicable)."""
        status = {}
        if hasattr(self.module, 'current_temperature'):
            status['current_temperature'] = self.module.current_temperature
        if hasattr(self.module, 'target_temperature'):
            status['target_temperature'] = self.module.target_temperature
        if hasattr(self.module, 'current_speed'):
            status['current_speed'] = self.module.current_speed
        if hasattr(self.module, 'target_speed'):
            status['target_speed'] = self.module.target_speed
        return status
    
    def get_thermocycler_status(self) -> Dict[str, Any]:
        """Get thermocycler module status (if applicable)."""
        status = {}
        if hasattr(self.module, 'block_temperature'):
            status['block_temperature'] = self.module.block_temperature
        if hasattr(self.module, 'lid_temperature'):
            status['lid_temperature'] = self.module.lid_temperature
        if hasattr(self.module, 'lid_position'):
            status['lid_position'] = self.module.lid_position
        return status
    
    def get_magnetic_status(self) -> Dict[str, Any]:
        """Get magnetic module status (if applicable)."""
        status = {}
        if hasattr(self.module, 'status'):
            status['status'] = self.module.status
        return status
    
    def get_status_summary(self) -> Dict[str, Any]:
        """Get comprehensive module status."""
        base_status = {
            'model': self.module.model,
            'serial_number': getattr(self.module, 'serial_number', 'unknown'),
            'timestamp': datetime.now().isoformat()
        }
        
        # Add module-specific status
        base_status.update(self.get_temperature_status())
        base_status.update(self.get_heater_shaker_status())
        base_status.update(self.get_thermocycler_status())
        base_status.update(self.get_magnetic_status())
        
        return base_status
    
    def log_status(self, message: str = "Module status"):
        """Log current module status."""
        status = self.get_status_summary()
        self.logger.info(f"{message}: {status}")


# Convenience functions for quick state queries
def check_pipette_state(pipette_context) -> Dict[str, Any]:
    """Quick function to get pipette status."""
    state = PipetteState(pipette_context)
    return state.get_status_summary()


def check_well_state(well_context) -> Dict[str, Any]:
    """Quick function to get well status."""
    state = WellState(well_context)
    return state.get_status_summary()


def check_labware_state(labware_context) -> Dict[str, Any]:
    """Quick function to get labware status."""
    state = LabwareState(labware_context)
    return state.get_status_summary()


def check_deck_state(protocol_context) -> Dict[str, Any]:
    """Quick function to get deck status."""
    state = DeckState(protocol_context)
    return state.get_status_summary()


def check_module_state(module_context) -> Dict[str, Any]:
    """Quick function to get module status."""
    state = ModuleState(module_context)
    return state.get_status_summary()


# Export main classes
__all__ = [
    'PipetteState',
    'WellState', 
    'LabwareState',
    'DeckState',
    'ModuleState',
    'check_pipette_state',
    'check_well_state',
    'check_labware_state',
    'check_deck_state',
    'check_module_state'
] 