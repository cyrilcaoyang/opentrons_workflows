# Opentrons UI

Web-based interface for real-time OT-2 visualization and control.

## Features

- 🔗 **Real-time Robot Connection** - Connect to OT-2 in simulation or live mode
- 🎨 **Visual Deck Layout** - Interactive 12-slot deck visualization
- 🧪 **Pipette Monitoring** - Real-time pipette status and tip tracking
- 💡 **Lights Control** - Toggle robot lights with visual feedback
- 🎮 **Quick Commands** - Buttons for common robot operations
- 💬 **Custom Commands** - Execute any Opentrons API command
- 📋 **Command Logging** - Real-time log of all operations
- 🌙 **Dark Mode** - Page background changes when lights are off

## Files

- `web_ui_demo.py` - Flask web application and API endpoints
- `ot2_state_monitor.py` - State monitoring and simulation classes
- `templates/ot2_dashboard.html` - Main dashboard UI template
- `__init__.py` - Package initialization
- `README.md` - This documentation

## Quick Start

1. **Start the web server:**
   ```bash
   # From project root:
   python user_scripts/start_ui.py
   
   # Or directly:
   cd src/opentrons-ui
   python web_ui_demo.py
   ```

2. **Open your browser:**
   ```
   http://localhost:8080
   ```

3. **Connect and control:**
   - Click "Connect (Simulation)" to start
   - Use the lights toggle and quick commands
   - Watch the deck layout update in real-time

## API Endpoints

- `POST /api/connect` - Connect to robot
- `POST /api/disconnect` - Disconnect from robot
- `GET /api/state` - Get current robot state
- `POST /api/execute` - Execute custom command
- `GET /api/deck_layout` - Get deck layout
- `POST /api/quick_commands/<command_type>` - Execute quick commands

## WebSocket Events

- `state_update` - Real-time state updates
- `status` - Status messages
- `error` - Error notifications

## Classes

### `PureSimulationOT2`
Local simulation without SSH connection for UI testing.

### `StatefulOT2`
Enhanced OT-2 class with real-time state monitoring capabilities.

## Usage

```python
from opentrons_ui import PureSimulationOT2

# Create simulation robot
robot = PureSimulationOT2()

# Execute commands and get state
result = robot.execute_single_command(
    "ctx.load_instrument('p1000_single_gen2', 'left')",
    get_state_after=True
)

# Get UI-formatted state
ui_state = robot.get_state_for_ui()
``` 