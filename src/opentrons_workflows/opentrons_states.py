"""
Opentrons State Tracking Module

This module provides simple access to the robot's built-in state tracking using
official Opentrons API structures. Perfect for UIs and real-time monitoring.

Structure:
- Deck: Dictionary of slots -> labware/modules
- Labware: List of wells with properties
- Wells: Simple dictionaries with all properties
- Pipettes: Simple dictionaries with state info

All functions return simple Python data structures (dicts, lists) for easy UI integration.
"""

from typing import Dict, List, Any, Optional
import logging
from datetime import datetime


def get_deck_state(protocol_context) -> Dict[str, Any]:
    """
    Get complete deck state as a simple dictionary.
    
    Returns:
        {
            'slots': {
                '1': {'type': 'labware', 'name': 'plate_name', 'load_name': '...'},
                '2': {'type': 'module', 'name': 'temp_mod', 'status': '...'},
                '3': None,  # Empty slot
                ...
            },
            'loaded_labwares': {...},
            'loaded_modules': {...},
            'loaded_instruments': {...},
            'timestamp': '...'
        }
    """
    logger = logging.getLogger("deck_state")
    
    # Get all 12 deck slots
    slots = {}
    for slot_num in range(1, 13):
        slot = str(slot_num)
        try:
            # Check if something is loaded in this slot
            deck_item = protocol_context.deck[slot]
            if deck_item is not None:
                # Determine what type of item it is
                if hasattr(deck_item, 'load_name'):  # Labware
                    slots[slot] = {
                        'type': 'labware',
                        'name': getattr(deck_item, 'name', deck_item.load_name),
                        'load_name': deck_item.load_name,
                        'is_tiprack': getattr(deck_item, 'is_tiprack', False),
                        'uri': getattr(deck_item, 'uri', None)
                    }
                elif hasattr(deck_item, 'module_name'):  # Module
                    slots[slot] = {
                        'type': 'module',
                        'name': getattr(deck_item, 'name', 'Unknown Module'),
                        'module_name': deck_item.module_name,
                        'status': getattr(deck_item, 'status', 'unknown'),
                        'serial_number': getattr(deck_item, 'serial_number', None)
                    }
                else:
                    slots[slot] = {'type': 'unknown', 'object': str(deck_item)}
            else:
                slots[slot] = None  # Empty slot
        except (KeyError, AttributeError):
            slots[slot] = None  # Empty slot
    
    # Get loaded items
    loaded_labwares = {}
    try:
        for slot, labware in protocol_context.loaded_labwares.items():
            loaded_labwares[slot] = {
                'name': getattr(labware, 'name', labware.load_name),
                'load_name': labware.load_name,
                'is_tiprack': getattr(labware, 'is_tiprack', False),
                'well_count': len(labware.wells()) if hasattr(labware, 'wells') else 0
            }
    except AttributeError:
        loaded_labwares = {}
    
    loaded_modules = {}
    try:
        for slot, module in protocol_context.loaded_modules.items():
            loaded_modules[slot] = {
                'name': getattr(module, 'name', 'Unknown'),
                'module_name': getattr(module, 'module_name', 'unknown'),
                'status': getattr(module, 'status', 'unknown')
            }
    except AttributeError:
        loaded_modules = {}
    
    loaded_instruments = {}
    try:
        for mount, instrument in protocol_context.loaded_instruments.items():
            loaded_instruments[mount] = {
                'name': instrument.name,
                'max_volume': instrument.max_volume,
                'min_volume': instrument.min_volume,
                'has_tip': instrument.has_tip,
                'current_volume': instrument.current_volume
            }
    except AttributeError:
        loaded_instruments = {}
    
    return {
        'slots': slots,
        'loaded_labwares': loaded_labwares,
        'loaded_modules': loaded_modules,
        'loaded_instruments': loaded_instruments,
        'total_slots': 12,
        'occupied_slots': len([s for s in slots.values() if s is not None]),
        'empty_slots': len([s for s in slots.values() if s is None]),
        'timestamp': datetime.now().isoformat()
    }


def get_labware_state(labware_context) -> Dict[str, Any]:
    """
    Get complete labware state as a simple dictionary with list of wells.
    
    Returns:
        {
            'info': {'name': '...', 'load_name': '...', 'is_tiprack': bool, ...},
            'wells': [
                {'name': 'A1', 'max_volume': 200, 'has_tip': True, ...},
                {'name': 'A2', 'max_volume': 200, 'has_tip': False, ...},
                ...
            ],
            'dimensions': {'rows': 8, 'columns': 12},
            'summary': {'total_wells': 96, 'available_tips': 95, ...}
        }
    """
    logger = logging.getLogger(f"labware_state.{labware_context.load_name}")
    
    # Basic labware info
    info = {
        'name': getattr(labware_context, 'name', labware_context.load_name),
        'load_name': labware_context.load_name,
        'parent': str(labware_context.parent),
        'is_tiprack': getattr(labware_context, 'is_tiprack', False),
        'uri': getattr(labware_context, 'uri', None),
        'tip_length': getattr(labware_context, 'tip_length', None)
    }
    
    # Get all wells as a simple list
    wells = []
    available_tips = 0
    wells_with_liquid = 0
    
    try:
        for well in labware_context.wells():
            well_data = {
                'name': well.well_name,
                'display_name': getattr(well, 'display_name', well.well_name),
                'max_volume': well.max_volume,
                'depth': well.depth,
                'diameter': getattr(well, 'diameter', None),
                'shape': getattr(well, 'shape', 'unknown')
            }
            
            # Add tip status for tip racks
            if info['is_tiprack']:
                has_tip = getattr(well, 'has_tip', True)
                well_data['has_tip'] = has_tip
                if has_tip:
                    available_tips += 1
            
            # Add liquid volume if available
            try:
                liquid_volume = well.current_liquid_volume
                well_data['current_liquid_volume'] = liquid_volume
                if liquid_volume > 0:
                    wells_with_liquid += 1
            except:
                well_data['current_liquid_volume'] = 0
            
            # Add position info
            try:
                position = well.geometry.position
                well_data['position'] = {'x': position.x, 'y': position.y, 'z': position.z}
            except:
                well_data['position'] = None
            
            wells.append(well_data)
    
    except Exception as e:
        logger.warning(f"Error getting wells for {labware_context.load_name}: {e}")
        wells = []
    
    # Calculate dimensions
    try:
        rows = labware_context.rows()
        columns = labware_context.columns()
        dimensions = {
            'rows': len(rows),
            'columns': len(columns),
            'total_wells': len(wells)
        }
    except:
        dimensions = {'rows': 0, 'columns': 0, 'total_wells': len(wells)}
    
    # Summary stats
    summary = {
        'total_wells': len(wells),
        'wells_with_liquid': wells_with_liquid,
        'available_tips': available_tips if info['is_tiprack'] else None,
        'used_tips': (len(wells) - available_tips) if info['is_tiprack'] else None
    }
    
    return {
        'info': info,
        'wells': wells,
        'dimensions': dimensions,
        'summary': summary,
        'timestamp': datetime.now().isoformat()
    }


def get_pipette_state(pipette_context) -> Dict[str, Any]:
    """
    Get complete pipette state as a simple dictionary.
    
    Returns:
        {
            'name': 'p300_single_gen2',
            'mount': 'right',
            'has_tip': True,
            'current_volume': 100.0,
            'max_volume': 300.0,
            'min_volume': 30.0,
            'tip_racks': ['tip_rack_1', ...],
            'flow_rates': {'aspirate': 150, 'dispense': 300, ...},
            'well_bottom_clearance': {'aspirate': 1.0, 'dispense': 1.0},
            'timestamp': '...'
        }
    """
    logger = logging.getLogger(f"pipette_state.{pipette_context.name}")
    
    # Get tip rack info
    tip_racks = []
    try:
        for tip_rack in pipette_context.tip_racks:
            tip_racks.append({
                'name': getattr(tip_rack, 'name', tip_rack.load_name),
                'load_name': tip_rack.load_name,
                'parent': str(tip_rack.parent)
            })
    except:
        tip_racks = []
    
    # Get flow rates
    flow_rates = {}
    try:
        flow_rates = {
            'aspirate': pipette_context.flow_rate.aspirate,
            'dispense': pipette_context.flow_rate.dispense,
            'blow_out': pipette_context.flow_rate.blow_out
        }
    except:
        flow_rates = {'aspirate': 0, 'dispense': 0, 'blow_out': 0}
    
    # Get well bottom clearance
    clearance = {}
    try:
        clearance = {
            'aspirate': pipette_context.well_bottom_clearance.aspirate,
            'dispense': pipette_context.well_bottom_clearance.dispense
        }
    except:
        clearance = {'aspirate': 1.0, 'dispense': 1.0}
    
    return {
        'name': pipette_context.name,
        'mount': str(pipette_context.mount),
        'has_tip': pipette_context.has_tip,
        'current_volume': pipette_context.current_volume,
        'max_volume': pipette_context.max_volume,
        'min_volume': pipette_context.min_volume,
        'tip_racks': tip_racks,
        'flow_rates': flow_rates,
        'well_bottom_clearance': clearance,
        'starting_tip': str(getattr(pipette_context, 'starting_tip', None)),
        'channels': getattr(pipette_context, 'channels', 1),
        'timestamp': datetime.now().isoformat()
    }


def get_well_state(well_context) -> Dict[str, Any]:
    """
    Get complete well state as a simple dictionary.
    
    Returns:
        {
            'name': 'A1',
            'display_name': 'A1 of Plate on 1',
            'max_volume': 200.0,
            'current_liquid_volume': 150.0,
            'depth': 10.5,
            'diameter': 6.85,
            'shape': 'circular',
            'position': {'x': 14.38, 'y': 74.24, 'z': 10.5},
            'has_tip': True,  # For tip racks only
            'parent_labware': 'plate_name',
            'timestamp': '...'
        }
    """
    logger = logging.getLogger(f"well_state.{well_context.well_name}")
    
    # Basic well properties
    well_data = {
        'name': well_context.well_name,
        'display_name': getattr(well_context, 'display_name', well_context.well_name),
        'max_volume': well_context.max_volume,
        'depth': well_context.depth,
        'diameter': getattr(well_context, 'diameter', None),
        'shape': getattr(well_context, 'shape', 'unknown'),
        'parent_labware': well_context.parent.load_name if hasattr(well_context.parent, 'load_name') else str(well_context.parent)
    }
    
    # Add liquid volume
    try:
        well_data['current_liquid_volume'] = well_context.current_liquid_volume
    except:
        well_data['current_liquid_volume'] = 0.0
    
    # Add tip status for tip racks
    try:
        well_data['has_tip'] = well_context.has_tip
    except AttributeError:
        well_data['has_tip'] = None  # Not a tip rack well
    
    # Add position
    try:
        position = well_context.geometry.position
        well_data['position'] = {'x': position.x, 'y': position.y, 'z': position.z}
    except:
        well_data['position'] = None
    
    # Add dimensions for rectangular wells
    try:
        if hasattr(well_context, 'length'):
            well_data['length'] = well_context.length
        if hasattr(well_context, 'width'):
            well_data['width'] = well_context.width
    except:
        pass
    
    well_data['timestamp'] = datetime.now().isoformat()
    
    return well_data


def get_module_state(module_context) -> Dict[str, Any]:
    """
    Get complete module state as a simple dictionary.
    
    Returns:
        {
            'name': 'Temperature Module',
            'module_name': 'temperature module gen2',
            'status': 'idle',
            'serial_number': 'TMG20210101A1',
            'temperature': 25.0,  # For temp modules
            'target': None,
            'labware': {...},  # If labware loaded
            'timestamp': '...'
        }
    """
    logger = logging.getLogger(f"module_state.{getattr(module_context, 'name', 'unknown')}")
    
    module_data = {
        'name': getattr(module_context, 'name', 'Unknown Module'),
        'module_name': getattr(module_context, 'module_name', 'unknown'),
        'status': getattr(module_context, 'status', 'unknown'),
        'serial_number': getattr(module_context, 'serial_number', None)
    }
    
    # Add temperature info for temperature modules
    if hasattr(module_context, 'temperature'):
        module_data['temperature'] = module_context.temperature
        module_data['target'] = getattr(module_context, 'target', None)
    
    # Add heater shaker info
    if hasattr(module_context, 'current_speed'):
        module_data['current_speed'] = module_context.current_speed
        module_data['target_speed'] = getattr(module_context, 'target_speed', None)
    
    # Add magnetic module info
    if hasattr(module_context, 'engaged'):
        module_data['engaged'] = module_context.engaged
    
    # Add loaded labware info
    try:
        if hasattr(module_context, 'labware') and module_context.labware:
            module_data['labware'] = {
                'name': getattr(module_context.labware, 'name', module_context.labware.load_name),
                'load_name': module_context.labware.load_name
            }
        else:
            module_data['labware'] = None
    except:
        module_data['labware'] = None
    
    module_data['timestamp'] = datetime.now().isoformat()
    
    return module_data


# Convenience functions for quick access
def get_all_states(protocol_context) -> Dict[str, Any]:
    """Get complete robot state in one call."""
    return {
        'deck': get_deck_state(protocol_context),
        'pipettes': {mount: get_pipette_state(pipette) 
                    for mount, pipette in protocol_context.loaded_instruments.items()},
        'labwares': {slot: get_labware_state(labware) 
                    for slot, labware in protocol_context.loaded_labwares.items()},
        'modules': {slot: get_module_state(module) 
                   for slot, module in getattr(protocol_context, 'loaded_modules', {}).items()},
        'timestamp': datetime.now().isoformat()
    }


def print_deck_summary(protocol_context):
    """Print a nice summary of deck state."""
    deck = get_deck_state(protocol_context)
    
    print("🗂️ Deck Summary")
    print("=" * 40)
    
    for slot in range(1, 13):
        slot_str = str(slot)
        item = deck['slots'][slot_str]
        if item:
            print(f"Slot {slot:2d}: {item['type'].title()} - {item['name']}")
        else:
            print(f"Slot {slot:2d}: Empty")
    
    print(f"\n📊 Total: {deck['occupied_slots']}/12 slots occupied")
    print(f"   Labwares: {len(deck['loaded_labwares'])}")
    print(f"   Modules: {len(deck['loaded_modules'])}")
    print(f"   Instruments: {len(deck['loaded_instruments'])}")


def print_labware_summary(labware_context):
    """Print a nice summary of labware state."""
    labware = get_labware_state(labware_context)
    
    print(f"🧪 {labware['info']['name']} Summary")
    print("=" * 40)
    print(f"Type: {labware['info']['load_name']}")
    print(f"Location: {labware['info']['parent']}")
    print(f"Dimensions: {labware['dimensions']['rows']} x {labware['dimensions']['columns']} = {labware['dimensions']['total_wells']} wells")
    
    if labware['info']['is_tiprack']:
        print(f"Tips available: {labware['summary']['available_tips']}/{labware['summary']['total_wells']}")
    
    if labware['summary']['wells_with_liquid'] > 0:
        print(f"Wells with liquid: {labware['summary']['wells_with_liquid']}")


def print_pipette_summary(pipette_context):
    """Print a nice summary of pipette state."""
    pipette = get_pipette_state(pipette_context)
    
    print(f"🔬 {pipette['name']} Summary")
    print("=" * 40)
    print(f"Mount: {pipette['mount']}")
    print(f"Has tip: {pipette['has_tip']}")
    print(f"Volume: {pipette['current_volume']}/{pipette['max_volume']} µL")
    print(f"Tip racks: {len(pipette['tip_racks'])}") 