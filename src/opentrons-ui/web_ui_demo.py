#!/usr/bin/env python3
"""
Web UI Demo for OT-2 State Monitoring
Shows how to create a real-time web interface using your existing OT-2 control architecture
"""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import json
import threading
import time
from ot2_state_monitor import StatefulOT2, PureSimulationOT2

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global robot instance
robot = None
monitoring_active = False


@app.route('/')
def index():
    """Main UI page"""
    return render_template('ot2_dashboard.html')


@app.route('/api/connect', methods=['POST'])
def connect_robot():
    """Connect to OT-2 robot"""
    global robot
    
    data = request.get_json()
    host_alias = data.get('host_alias', 'ot2_local')
    simulation = data.get('simulation', True)
    
    try:
        # For simulation mode, use pure simulation without SSH
        if simulation:
            robot = PureSimulationOT2()
        else:
            robot = StatefulOT2(host_alias=host_alias, simulation=False)
        
        # Get initial state
        initial_state = robot.get_state_for_ui()
        
        return jsonify({
            'status': 'success',
            'message': f'Connected to OT-2 {"(Pure Simulation)" if simulation else "(Live)"}',
            'robot_state': initial_state
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Failed to connect: {str(e)}'
        }), 500


@app.route('/api/disconnect', methods=['POST'])
def disconnect_robot():
    """Disconnect from robot"""
    global robot, monitoring_active
    
    monitoring_active = False
    
    if robot:
        try:
            robot.close()
            robot = None
            return jsonify({'status': 'success', 'message': 'Disconnected'})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    
    return jsonify({'status': 'success', 'message': 'Already disconnected'})


@app.route('/api/state')
def get_robot_state():
    """Get current robot state"""
    if not robot:
        return jsonify({'status': 'error', 'message': 'Not connected'}), 400
    
    try:
        state = robot.get_state_for_ui()
        return jsonify({'status': 'success', 'robot_state': state})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/execute', methods=['POST'])
def execute_command():
    """Execute a single command and return updated state"""
    if not robot:
        return jsonify({'status': 'error', 'message': 'Not connected'}), 400
    
    data = request.get_json()
    command = data.get('command', '')
    
    if not command:
        return jsonify({'status': 'error', 'message': 'No command provided'}), 400
    
    try:
        result = robot.execute_single_command(command, get_state_after=True)
        
        # Emit real-time update via WebSocket
        if result.get('robot_state'):
            socketio.emit('state_update', result['robot_state'])
        
        return jsonify({
            'status': 'success',
            'command_result': result,
            'robot_state': result.get('robot_state')
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/deck_layout')
def get_deck_layout():
    """Get current deck layout"""
    if not robot:
        return jsonify({'status': 'error', 'message': 'Not connected'}), 400
    
    try:
        deck_layout = robot.get_deck_layout()
        return jsonify({'status': 'success', 'deck_layout': deck_layout})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# WebSocket events for real-time updates
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print('Client connected')
    emit('status', {'message': 'Connected to OT-2 Dashboard'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print('Client disconnected')


@socketio.on('start_monitoring')
def handle_start_monitoring():
    """Start real-time state monitoring"""
    global monitoring_active
    
    if not robot:
        emit('error', {'message': 'Robot not connected'})
        return
    
    monitoring_active = True
    
    def monitor_state():
        """Background task to monitor robot state"""
        while monitoring_active and robot:
            try:
                state = robot.get_state_for_ui()
                socketio.emit('state_update', state)
                time.sleep(2)  # Update every 2 seconds
            except Exception as e:
                socketio.emit('error', {'message': f'Monitoring error: {str(e)}'})
                break
    
    # Start monitoring in background thread
    threading.Thread(target=monitor_state, daemon=True).start()
    emit('status', {'message': 'Started real-time monitoring'})


@socketio.on('stop_monitoring')
def handle_stop_monitoring():
    """Stop real-time state monitoring"""
    global monitoring_active
    monitoring_active = False
    emit('status', {'message': 'Stopped real-time monitoring'})


# Quick command shortcuts for UI
@app.route('/api/quick_commands/<command_type>', methods=['POST'])
def quick_command(command_type):
    """Execute common commands quickly"""
    if not robot:
        return jsonify({'status': 'error', 'message': 'Not connected'}), 400
    
    commands = {
        'home': 'ctx.home()',
        'lights_on': 'ctx.set_rail_lights(True)',
        'lights_off': 'ctx.set_rail_lights(False)',
        'load_p1000_left': "ctx.load_instrument('p1000_single_gen2', 'left')",
        'load_p300_right': "ctx.load_instrument('p300_single_gen2', 'right')",
        'load_tips_1': "ctx.load_labware('opentrons_96_tiprack_1000ul', '1')",
        'load_plate_2': "ctx.load_labware('corning_96_wellplate_360ul_flat', '2')",
    }
    
    command = commands.get(command_type)
    if not command:
        return jsonify({'status': 'error', 'message': 'Unknown command'}), 400
    
    try:
        result = robot.execute_single_command(command, get_state_after=True)
        
        # Emit real-time update
        if result.get('robot_state'):
            socketio.emit('state_update', result['robot_state'])
        
        return jsonify({
            'status': 'success',
            'command': command,
            'result': result
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    print("🌐 Starting OT-2 Web Dashboard...")
    print("📊 Access dashboard at: http://localhost:8080")
    print("🔧 API endpoints available at /api/*")
    
    # Create templates directory if it doesn't exist
    import os
    os.makedirs('templates', exist_ok=True)
    
    socketio.run(app, debug=True, host='0.0.0.0', port=8080) 