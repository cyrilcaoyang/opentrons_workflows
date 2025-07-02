#!/usr/bin/env python3
"""
OT-2 State Monitor
Extends the existing opentrons_control.py with real-time state monitoring capabilities for UI
"""

import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opentrons_workflows.opentrons_control import OpenTronsBase


@dataclass
class RobotState:
    """Data class representing the current state of the OT-2 robot"""
    pipettes: Dict[str, Dict[str, Any]]
    deck_slots: Dict[str, Optional[Dict[str, Any]]]
    current_position: Dict[str, float]
    tips_attached: Dict[str, bool]
    door_state: str
    lights_on: bool
    is_homed: bool
    last_updated: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'pipettes': self.pipettes,
            'deck_slots': self.deck_slots,
            'current_position': self.current_position,
            'tips_attached': self.tips_attached,
            'door_state': self.door_state,
            'lights_on': self.lights_on,
            'is_homed': self.is_homed,
            'last_updated': self.last_updated
        }


class PureSimulationOT2:
    """
    Pure simulation OT-2 for UI testing without SSH connection
    Simulates robot state changes locally
    """
    
    def __init__(self):
        self.simulation = True
        self._state = {
            'pipettes': {'left': None, 'right': None},
            'deck_slots': {str(i): None for i in range(1, 13)},
            'tips_attached': {'left': False, 'right': False},
            'lights_on': True,
            'is_homed': True,
            'door_state': 'closed'
        }
        self._cached_state = None
        self._state_cache_duration = 2.0
        
    def execute_command(self, command: str) -> Dict[str, Any]:
        """Simulate command execution and return mock results"""
        try:
            # Simulate different command types
            if 'load_instrument' in command:
                # Extract pipette name and mount
                if "'left'" in command:
                    mount = 'left'
                elif "'right'" in command:
                    mount = 'right'
                else:
                    mount = 'left'
                
                if 'p1000' in command:
                    pipette_name = 'p1000_single_gen2'
                    max_volume = 1000
                elif 'p300' in command:
                    pipette_name = 'p300_single_gen2'
                    max_volume = 300
                else:
                    pipette_name = 'unknown_pipette'
                    max_volume = 0
                
                self._state['pipettes'][mount] = {
                    'name': pipette_name,
                    'max_volume': max_volume,
                    'min_volume': 20,
                    'channels': 1,
                    'has_tip': False
                }
                
            elif 'load_labware' in command:
                # Extract slot number
                import re
                slot_match = re.search(r"'(\d+)'", command)
                if slot_match:
                    slot = slot_match.group(1)
                    
                    if 'tiprack' in command:
                        labware_name = 'Tip Rack'
                        load_name = 'opentrons_96_tiprack_1000ul'
                    elif 'wellplate' in command:
                        labware_name = 'Well Plate'
                        load_name = 'corning_96_wellplate_360ul_flat'
                    else:
                        labware_name = 'Unknown Labware'
                        load_name = 'unknown'
                    
                    self._state['deck_slots'][slot] = {
                        'name': labware_name,
                        'load_name': load_name,
                        'display_name': labware_name,
                        'wells_count': 96
                    }
            
            elif 'pick_up_tip' in command:
                # Simulate tip pickup
                for mount in ['left', 'right']:
                    if self._state['pipettes'][mount]:
                        self._state['tips_attached'][mount] = True
                        self._state['pipettes'][mount]['has_tip'] = True
                        break
            
            elif 'drop_tip' in command:
                # Simulate tip drop
                for mount in ['left', 'right']:
                    if self._state['pipettes'][mount]:
                        self._state['tips_attached'][mount] = False
                        self._state['pipettes'][mount]['has_tip'] = False
                        break
            
            elif 'home' in command:
                self._state['is_homed'] = True
            
            elif 'set_rail_lights' in command:
                if 'True' in command:
                    self._state['lights_on'] = True
                else:
                    self._state['lights_on'] = False
            
            return {
                'status': 'success',
                'data': 'Command executed successfully (simulated)',
                'error': None,
                'traceback': None
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'data': None,
                'error': str(e),
                'traceback': str(e)
            }
    
    def get_state_for_ui(self) -> Dict[str, Any]:
        """Get robot state formatted for UI consumption"""
        ui_state = {
            'connected': True,
            'robot_type': 'OT-2',
            'simulation_mode': True,
            'pipettes': {},
            'deck': {},
            'status': {
                'door_closed': self._state['door_state'] == 'closed',
                'lights_on': self._state['lights_on'],
                'homed': self._state['is_homed'],
                'last_updated': time.time()
            }
        }
        
        # Format pipettes for UI
        for mount in ['left', 'right']:
            pipette_info = self._state['pipettes'][mount]
            if pipette_info:
                ui_state['pipettes'][mount] = {
                    'loaded': True,
                    'name': pipette_info['name'],
                    'has_tip': pipette_info.get('has_tip', False),
                    'max_volume': pipette_info['max_volume'],
                    'channels': pipette_info['channels']
                }
            else:
                ui_state['pipettes'][mount] = {
                    'loaded': False,
                    'name': None,
                    'has_tip': False
                }
        
        # Format deck for UI
        for slot in range(1, 13):
            slot_str = str(slot)
            labware_info = self._state['deck_slots'][slot_str]
            if labware_info:
                ui_state['deck'][slot_str] = {
                    'occupied': True,
                    'labware_name': labware_info['name'],
                    'load_name': labware_info['load_name'],
                    'wells_count': labware_info['wells_count']
                }
            else:
                ui_state['deck'][slot_str] = {
                    'occupied': False,
                    'labware_name': None
                }
        
        return ui_state
    
    def execute_single_command(self, command: str, get_state_after: bool = True) -> Dict[str, Any]:
        """Execute a single command and optionally return updated state"""
        result = self.execute_command(command)
        
        if get_state_after:
            result['robot_state'] = self.get_state_for_ui()
        
        return result
    
    def close(self):
        """Close connection (no-op for simulation)"""
        pass


class StatefulOT2(OpenTronsBase):
    """
    Enhanced OT-2 class with state monitoring capabilities for UI development
    Extends your existing approach with real-time state querying
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, robot_type='OT-2', **kwargs)
        self._cached_state: Optional[RobotState] = None
        self._state_cache_duration = 2.0  # Cache state for 2 seconds
        
    def get_robot_state(self, use_cache: bool = True) -> RobotState:
        """
        Get the current state of the OT-2 robot
        
        :param use_cache: If True, use cached state if recent enough
        :return: RobotState object with current robot information
        """
        current_time = time.time()
        
        # Return cached state if recent enough
        if (use_cache and self._cached_state and 
            current_time - self._cached_state.last_updated < self._state_cache_duration):
            return self._cached_state
        
        # Query fresh state from robot
        state_command = """
# Get comprehensive robot state
import json

state_info = {
    'pipettes': {},
    'deck_slots': {},
    'current_position': {},
    'tips_attached': {},
    'door_state': 'unknown',
    'lights_on': False,
    'is_homed': True
}

# Get pipette information
try:
    for mount in ['left', 'right']:
        pipette = None
        try:
            # Try to access pipette if it exists
            pipette = getattr(ctx.loaded_instruments, mount, None)
            if pipette:
                state_info['pipettes'][mount] = {
                    'name': pipette.name,
                    'model': getattr(pipette, 'model', 'unknown'),
                    'max_volume': getattr(pipette, 'max_volume', 0),
                    'min_volume': getattr(pipette, 'min_volume', 0),
                    'channels': getattr(pipette, 'channels', 1),
                    'has_tip': pipette.has_tip if hasattr(pipette, 'has_tip') else False
                }
                state_info['tips_attached'][mount] = pipette.has_tip if hasattr(pipette, 'has_tip') else False
            else:
                state_info['pipettes'][mount] = None
                state_info['tips_attached'][mount] = False
        except Exception as e:
            state_info['pipettes'][mount] = {'error': str(e)}
            state_info['tips_attached'][mount] = False
except Exception as e:
    state_info['pipettes'] = {'error': str(e)}

# Get deck slot information
try:
    for slot_num in range(1, 13):  # OT-2 has slots 1-11
        slot_name = str(slot_num)
        try:
            # Check if something is loaded in this slot
            labware = getattr(ctx.loaded_labwares, slot_name, None)
            if labware:
                state_info['deck_slots'][slot_name] = {
                    'name': getattr(labware, 'name', 'unknown'),
                    'load_name': getattr(labware, 'load_name', 'unknown'),
                    'display_name': getattr(labware, 'display_name', 'unknown'),
                    'wells_count': len(labware.wells) if hasattr(labware, 'wells') else 0
                }
            else:
                state_info['deck_slots'][slot_name] = None
        except Exception as e:
            state_info['deck_slots'][slot_name] = {'error': str(e)}
except Exception as e:
    state_info['deck_slots'] = {'error': str(e)}

# Try to get current position (this might not be available in all contexts)
try:
    # This is approximate - actual position tracking requires more complex setup
    state_info['current_position'] = {
        'x': 0.0,
        'y': 0.0, 
        'z': 0.0,
        'note': 'Position tracking requires additional setup'
    }
except Exception as e:
    state_info['current_position'] = {'error': str(e)}

# Check door state and lights (limited access in protocol context)
try:
    # These may not be directly accessible in protocol context
    state_info['door_state'] = 'closed'  # Assumption for protocol execution
    state_info['lights_on'] = True      # Usually on during protocol
    state_info['is_homed'] = True       # Assumption for active protocol
except Exception as e:
    pass

# Output the state
result = state_info
"""
        
        try:
            response = self.execute_command(state_command)
            
            if response.get('status') == 'success':
                # Parse the state data from the response
                state_data = response.get('data', {})
                
                # If data is a string (JSON), parse it
                if isinstance(state_data, str):
                    try:
                        state_data = json.loads(state_data)
                    except json.JSONDecodeError:
                        state_data = {}
                
                # Create RobotState object
                robot_state = RobotState(
                    pipettes=state_data.get('pipettes', {}),
                    deck_slots=state_data.get('deck_slots', {}),
                    current_position=state_data.get('current_position', {}),
                    tips_attached=state_data.get('tips_attached', {}),
                    door_state=state_data.get('door_state', 'unknown'),
                    lights_on=state_data.get('lights_on', False),
                    is_homed=state_data.get('is_homed', False),
                    last_updated=current_time
                )
                
                # Cache the state
                self._cached_state = robot_state
                return robot_state
                
            else:
                # Return empty state on error
                return self._create_empty_state(current_time)
                
        except Exception as e:
            self.logger.error(f"Failed to get robot state: {e}")
            return self._create_empty_state(current_time)
    
    def _create_empty_state(self, timestamp: float) -> RobotState:
        """Create an empty/error state"""
        return RobotState(
            pipettes={},
            deck_slots={},
            current_position={},
            tips_attached={},
            door_state='unknown',
            lights_on=False,
            is_homed=False,
            last_updated=timestamp
        )
    
    def get_pipette_state(self, mount: str) -> Dict[str, Any]:
        """Get detailed state for a specific pipette mount"""
        state = self.get_robot_state()
        return state.pipettes.get(mount, {})
    
    def get_deck_layout(self) -> Dict[str, Any]:
        """Get current deck layout for UI visualization"""
        state = self.get_robot_state()
        return state.deck_slots
    
    def is_pipette_tip_attached(self, mount: str) -> bool:
        """Check if a specific pipette has a tip attached"""
        state = self.get_robot_state()
        return state.tips_attached.get(mount, False)
    
    def execute_single_command(self, command: str, get_state_after: bool = True) -> Dict[str, Any]:
        """
        Execute a single command and optionally return updated state
        Perfect for stepwise UI operations
        
        :param command: Single command to execute
        :param get_state_after: If True, include updated robot state in response
        :return: Dictionary with command result and optional state
        """
        # Execute the command using your existing method
        result = self.execute_command(command)
        
        # Add state information if requested
        if get_state_after:
            try:
                # Force refresh state cache
                updated_state = self.get_robot_state(use_cache=False)
                result['robot_state'] = updated_state.to_dict()
            except Exception as e:
                self.logger.warning(f"Could not get updated state after command: {e}")
                result['robot_state'] = None
        
        return result
    
    def get_state_for_ui(self) -> Dict[str, Any]:
        """
        Get robot state formatted for UI consumption
        Returns a clean, UI-friendly representation
        """
        state = self.get_robot_state()
        
        # Format for UI
        ui_state = {
            'connected': True,
            'robot_type': 'OT-2',
            'simulation_mode': self.simulation,
            'pipettes': {},
            'deck': {},
            'status': {
                'door_closed': state.door_state == 'closed',
                'lights_on': state.lights_on,
                'homed': state.is_homed,
                'last_updated': state.last_updated
            }
        }
        
        # Format pipettes for UI
        for mount, pipette_info in state.pipettes.items():
            if pipette_info and not isinstance(pipette_info, dict) or 'error' not in pipette_info:
                ui_state['pipettes'][mount] = {
                    'loaded': True,
                    'name': pipette_info.get('name', 'Unknown'),
                    'has_tip': state.tips_attached.get(mount, False),
                    'max_volume': pipette_info.get('max_volume', 0),
                    'channels': pipette_info.get('channels', 1)
                }
            else:
                ui_state['pipettes'][mount] = {
                    'loaded': False,
                    'name': None,
                    'has_tip': False
                }
        
        # Format deck for UI
        for slot, labware_info in state.deck_slots.items():
            if labware_info and 'error' not in labware_info:
                ui_state['deck'][slot] = {
                    'occupied': True,
                    'labware_name': labware_info.get('name', 'Unknown'),
                    'load_name': labware_info.get('load_name', 'unknown'),
                    'wells_count': labware_info.get('wells_count', 0)
                }
            else:
                ui_state['deck'][slot] = {
                    'occupied': False,
                    'labware_name': None
                }
        
        return ui_state


# Example usage and testing functions
def demo_state_monitoring():
    """Demonstrate the state monitoring capabilities"""
    
    print("🔍 OT-2 State Monitoring Demo")
    print("=" * 50)
    
    # Connect to robot (use your existing settings)
    try:
        robot = StatefulOT2(host_alias='your_robot_alias', simulation=True)
        
        print("📊 Getting initial robot state...")
        state = robot.get_state_for_ui()
        print(json.dumps(state, indent=2))
        
        print("\n🔧 Loading a pipette...")
        result = robot.execute_single_command(
            "ctx.load_instrument('p1000_single_gen2', 'left')",
            get_state_after=True
        )
        
        if result.get('robot_state'):
            print("✅ Updated state after loading pipette:")
            print(json.dumps(result['robot_state'], indent=2))
        
        print("\n🧪 Loading labware...")
        robot.execute_single_command(
            "ctx.load_labware('corning_96_wellplate_360ul_flat', '1')",
            get_state_after=True
        )
        
        print("\n📋 Final deck layout:")
        deck_layout = robot.get_deck_layout()
        print(json.dumps(deck_layout, indent=2))
        
        robot.close()
        
    except Exception as e:
        print(f"❌ Demo failed: {e}")


if __name__ == "__main__":
    demo_state_monitoring() 