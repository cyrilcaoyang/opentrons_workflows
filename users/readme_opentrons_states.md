# Opentrons State Tracking

## Overview

The state-readers module (`src/opentrons_server/control/state_readers.py`, formerly `opentrons_states.py`) provides **simple, direct access** to the robot's official state tracking functionality. All functions return **simple Python data structures** (dictionaries and lists) that are perfect for UIs, real-time monitoring, and JSON serialization. The gateway ships this module's *source* over the SSH REPL for each snapshot (see the repo README), so it deliberately has no `opentrons_server` imports of its own; the functions are re-exported from the package root as shown below.

## Key Design Principles

✅ **Simple Data Structures**: Functions return `dict` and `list` objects, not custom classes  
✅ **Official Robot State**: Direct access to `protocol.deck`, `labware.wells()`, `pipette.has_tip`, etc.  
✅ **UI-Friendly**: All data is JSON-serializable for web UIs and dashboards  
✅ **Real-Time**: Reflects current robot state instantly  
✅ **Simulation Compatible**: Works perfectly in simulation mode  

## Core Functions

### 🗂️ Deck State
```python
from opentrons_server import get_deck_state

deck = get_deck_state(protocol)
# Returns:
# {
#     'slots': {
#         '1': {'type': 'labware', 'name': 'tip_rack', 'is_tiprack': True},
#         '2': {'type': 'labware', 'name': 'plate', 'is_tiprack': False},
#         '3': None,  # Empty slot
#         ...
#     },
#     'occupied_slots': 2,
#     'empty_slots': 10,
#     'loaded_labwares': {...},
#     'loaded_instruments': {...}
# }
```

### 🧪 Labware State  
```python
from opentrons_server import get_labware_state

labware = get_labware_state(tip_rack)
# Returns:
# {
#     'info': {'name': 'tip_rack', 'is_tiprack': True, 'load_name': '...'},
#     'wells': [
#         {'name': 'A1', 'has_tip': True, 'max_volume': 300},
#         {'name': 'A2', 'has_tip': False, 'max_volume': 300},
#         ...
#     ],
#     'dimensions': {'rows': 8, 'columns': 12, 'total_wells': 96},
#     'summary': {'available_tips': 95, 'used_tips': 1}
# }
```

### 🔬 Pipette State
```python
from opentrons_server import get_pipette_state

pipette = get_pipette_state(p300)
# Returns:
# {
#     'name': 'p300_single_gen2',
#     'mount': 'right',
#     'has_tip': True,
#     'current_volume': 100.0,
#     'max_volume': 300.0,
#     'tip_racks': [{'name': 'tip_rack_1', 'load_name': '...'}],
#     'flow_rates': {'aspirate': 150, 'dispense': 300}
# }
```

### 💧 Well State
```python
from opentrons_server import get_well_state

well = get_well_state(plate['A1'])
# Returns:
# {
#     'name': 'A1',
#     'max_volume': 200.0,
#     'current_liquid_volume': 150.0,
#     'depth': 10.5,
#     'diameter': 6.85,
#     'position': {'x': 14.38, 'y': 74.24, 'z': 10.5},
#     'has_tip': None  # Only for tip racks
# }
```

## Perfect for UIs 🖥️

The simple data structures make it easy to build real-time dashboards:

```python
# Get complete robot state
from opentrons_server import get_all_states

state = get_all_states(protocol)
# Returns complete state as nested dictionaries

# Send to web UI as JSON
import json
json_state = json.dumps(state, indent=2)

# Perfect for:
# - React/Vue.js components
# - Real-time monitoring dashboards  
# - WebSocket state updates
# - Database storage
# - API endpoints
```

## Example: Real-Time Tip Tracking

```python
# Monitor tip usage in real-time
tip_rack = protocol.load_labware('opentrons_96_tiprack_300ul', 1)

def check_tips():
    state = get_labware_state(tip_rack)
    available = state['summary']['available_tips']
    total = state['summary']['total_wells']
    print(f"Tips: {available}/{total} available")
    
    # Show which specific tips are used
    for well in state['wells']:
        if not well['has_tip']:
            print(f"  Used: {well['name']}")

# Check before and after operations
check_tips()  # 96/96 available
pipette.pick_up_tip()
check_tips()  # 95/96 available, Used: A1
```

## Example: Deck Slot Management

```python
# Perfect for UI slot management
deck = get_deck_state(protocol)

print("🗂️ Deck Layout:")
for slot in range(1, 13):
    slot_str = str(slot)
    item = deck['slots'][slot_str]
    if item:
        print(f"Slot {slot:2d}: {item['type']} - {item['name']}")
    else:
        print(f"Slot {slot:2d}: [EMPTY - Available]")

# Easy to build drag-drop UI:
# - Green slots = available
# - Blue slots = labware
# - Orange slots = modules
```

## Simulation Mode Support ✅

Everything works perfectly in simulation mode:

```python
from opentrons import simulate

# Simulation mode (no hardware motion; `execute` is the real-hardware path)
protocol = simulate.get_protocol_api('2.21')

# All state tracking works identically
deck = get_deck_state(protocol)  # ✅ Works
pipette = get_pipette_state(p300)  # ✅ Works  
labware = get_labware_state(plate)  # ✅ Works

# Perfect for:
# - Interactive Jupyter development
# - Protocol testing without hardware
# - UI development and testing
# - Automated testing pipelines
```

## Why This Approach? 🎯

### ❌ Old Approach (Class-Based)
```python
# Complex, object-oriented
pipette_state = PipetteState(pipette)
status = pipette_state.get_status_summary()  # Returns custom object
```

### ✅ New Approach (Function-Based)
```python  
# Simple, direct
status = get_pipette_state(pipette)  # Returns simple dict
```

**Benefits:**
- **Simpler**: No classes to instantiate or manage
- **Direct**: Uses robot's official state properties 
- **UI-Friendly**: JSON-serializable data structures
- **Faster**: No overhead of object creation
- **Flexible**: Easy to extend or modify
- **Compatible**: Works with any web framework or database

## Migration Guide

### Old Code
```python
from opentrons_server import PipetteState, LabwareState

pipette_state = PipetteState(pipette)
has_tip = pipette_state.has_tip
volume = pipette_state.current_volume

labware_state = LabwareState(tip_rack)
summary = labware_state.get_status_summary()
```

### New Code  
```python
from opentrons_server import get_pipette_state, get_labware_state

pipette_data = get_pipette_state(pipette)
has_tip = pipette_data['has_tip']
volume = pipette_data['current_volume']

labware_data = get_labware_state(tip_rack)
summary = labware_data['summary']
```

## Use Cases

🔬 **Protocol Development**: Real-time state monitoring in Jupyter notebooks  
🖥️ **Web UIs**: Live dashboards showing robot status  
📊 **Data Analysis**: Export state data for protocol optimization  
🧪 **Testing**: Automated verification of protocol state changes  
🤖 **Automation**: State-driven decision making in workflows  

The new approach is **simpler, faster, and more flexible** while providing **complete access** to the robot's official state tracking! 🚀 