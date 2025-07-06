"""
Multi-Instrument Workflow Orchestrator using Prefect
Coordinates OT-2 robots with other laboratory instruments
"""

from prefect import flow, task, get_run_logger
from prefect.tasks import task_input_hash
from prefect.client.schemas import FlowRun
from prefect.server.schemas.states import StateType
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import asyncio
import json
import time

# Import instrument clients
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from opentrons_workflows.robust_ssh_client import RobustSSHClient

class InstrumentManager:
    """Manages connections to multiple laboratory instruments"""
    
    def __init__(self):
        self.ot2_robots: Dict[str, RobustSSHClient] = {}
        self.other_instruments: Dict[str, Any] = {}  # Placeholder for other instruments
        
    def add_ot2(self, robot_id: str, host_alias: str, password: str = "accelerate"):
        """Add OT-2 robot connection"""
        client = RobustSSHClient(
            host_alias=host_alias,
            password=password,
            max_retries=3,
            command_timeout=60
        )
        if client.connect():
            self.ot2_robots[robot_id] = client
            return True
        return False
    
    def get_ot2(self, robot_id: str) -> RobustSSHClient:
        """Get OT-2 robot client"""
        if robot_id not in self.ot2_robots:
            raise ValueError(f"OT-2 robot {robot_id} not connected")
        return self.ot2_robots[robot_id]
    
    def add_instrument(self, instrument_id: str, instrument_client: Any):
        """Add other instrument (HPLC, spectrophotometer, etc.)"""
        self.other_instruments[instrument_id] = instrument_client
    
    def get_instrument(self, instrument_id: str):
        """Get other instrument client"""
        if instrument_id not in self.other_instruments:
            raise ValueError(f"Instrument {instrument_id} not connected")
        return self.other_instruments[instrument_id]

# Global instrument manager
instrument_manager = InstrumentManager()

# OT-2 Specific Tasks
@task(cache_key_fn=task_input_hash, cache_expiration=timedelta(minutes=5))
def initialize_ot2_protocol(robot_id: str, api_version: str = "2.21"):
    """Initialize OT-2 protocol API"""
    logger = get_run_logger()
    logger.info(f"Initializing OT-2 protocol on robot {robot_id}")
    
    client = instrument_manager.get_ot2(robot_id)
    
    commands = [
        "from opentrons import execute",
        "from opentrons.types import Point, Location",
        "from opentrons import protocol_api",
        f"protocol = execute.get_protocol_api('{api_version}')",
        "protocol.home()"
    ]
    
    for cmd in commands:
        response = client.invoke_with_retry(cmd, timeout=30)
        logger.info(f"Executed: {cmd}")
    
    return {"robot_id": robot_id, "status": "initialized", "api_version": api_version}

@task
def load_ot2_labware(robot_id: str, labware_configs: List[Dict]):
    """Load labware on OT-2"""
    logger = get_run_logger()
    logger.info(f"Loading {len(labware_configs)} labware items on robot {robot_id}")
    
    client = instrument_manager.get_ot2(robot_id)
    loaded_labware = []
    
    for labware in labware_configs:
        if labware.get("ot_default", True):
            cmd = f"{labware['nickname']} = protocol.load_labware(load_name='{labware['loadname']}', location='{labware['location']}')"
        else:
            cmd = f"{labware['nickname']} = protocol.load_labware_from_definition(labware_def={labware['config']}, location='{labware['location']}')"
        
        client.invoke_with_retry(cmd)
        loaded_labware.append(labware['nickname'])
        logger.info(f"Loaded labware: {labware['nickname']}")
    
    return {"robot_id": robot_id, "loaded_labware": loaded_labware}

@task
def load_ot2_instruments(robot_id: str, instrument_configs: List[Dict]):
    """Load instruments on OT-2"""
    logger = get_run_logger()
    logger.info(f"Loading {len(instrument_configs)} instruments on robot {robot_id}")
    
    client = instrument_manager.get_ot2(robot_id)
    loaded_instruments = []
    
    for instrument in instrument_configs:
        cmd = f"{instrument['nickname']} = protocol.load_instrument(instrument_name='{instrument['instrument_name']}', mount='{instrument['mount']}')"
        client.invoke_with_retry(cmd)
        loaded_instruments.append(instrument['nickname'])
        logger.info(f"Loaded instrument: {instrument['nickname']}")
    
    return {"robot_id": robot_id, "loaded_instruments": loaded_instruments}

@task
def execute_ot2_liquid_handling(robot_id: str, operations: List[Dict]):
    """Execute liquid handling operations on OT-2"""
    logger = get_run_logger()
    logger.info(f"Executing {len(operations)} liquid handling operations on robot {robot_id}")
    
    client = instrument_manager.get_ot2(robot_id)
    results = []
    
    for i, operation in enumerate(operations):
        logger.info(f"Operation {i+1}: {operation.get('description', 'Unknown operation')}")
        
        op_type = operation['type']
        
        if op_type == 'pick_up_tip':
            cmd = f"location = {operation['labware']}['{operation['position']}'].top(0)"
            client.invoke_with_retry(cmd)
            cmd = f"{operation['pipette']}.pick_up_tip(location=location)"
            
        elif op_type == 'aspirate':
            location_offset = operation.get('offset', {})
            if location_offset.get('bottom'):
                cmd = f"location = {operation['labware']}['{operation['position']}'].bottom({location_offset['bottom']})"
            elif location_offset.get('top'):
                cmd = f"location = {operation['labware']}['{operation['position']}'].top({location_offset['top']})"
            else:
                cmd = f"location = {operation['labware']}['{operation['position']}'].center()"
            client.invoke_with_retry(cmd)
            cmd = f"{operation['pipette']}.aspirate(volume={operation['volume']}, location=location)"
            
        elif op_type == 'dispense':
            location_offset = operation.get('offset', {})
            if location_offset.get('bottom'):
                cmd = f"location = {operation['labware']}['{operation['position']}'].bottom({location_offset['bottom']})"
            elif location_offset.get('top'):
                cmd = f"location = {operation['labware']}['{operation['position']}'].top({location_offset['top']})"
            else:
                cmd = f"location = {operation['labware']}['{operation['position']}'].center()"
            client.invoke_with_retry(cmd)
            cmd = f"{operation['pipette']}.dispense(volume={operation['volume']}, location=location)"
            
        elif op_type == 'drop_tip':
            cmd = f"{operation['pipette']}.drop_tip()"
            
        elif op_type == 'delay':
            cmd = f"protocol.delay(seconds={operation['seconds']})"
            
        else:
            # Custom command
            cmd = operation.get('command', '')
        
        if cmd:
            response = client.invoke_with_retry(cmd, timeout=operation.get('timeout', 30))
            results.append({
                "operation": i+1,
                "type": op_type,
                "command": cmd,
                "response": response.strip() if response else "",
                "timestamp": datetime.now().isoformat()
            })
    
    return {"robot_id": robot_id, "operations_completed": len(results), "results": results}

# Generic Instrument Tasks
@task
def execute_instrument_command(instrument_id: str, command: str, parameters: Dict = None):
    """Execute command on any connected instrument"""
    logger = get_run_logger()
    logger.info(f"Executing command on instrument {instrument_id}: {command}")
    
    # This is a placeholder - implement based on your specific instruments
    # Examples: HPLC, spectrophotometer, plate reader, etc.
    
    instrument = instrument_manager.get_instrument(instrument_id)
    
    # Simulate command execution
    time.sleep(1)  # Replace with actual instrument communication
    
    result = {
        "instrument_id": instrument_id,
        "command": command,
        "parameters": parameters,
        "status": "completed",
        "timestamp": datetime.now().isoformat()
    }
    
    logger.info(f"Command completed on {instrument_id}")
    return result

@task
def wait_for_instrument(instrument_id: str, timeout: int = 300):
    """Wait for instrument to become ready"""
    logger = get_run_logger()
    logger.info(f"Waiting for instrument {instrument_id} to be ready")
    
    # Implement instrument-specific ready check
    time.sleep(2)  # Placeholder
    
    return {"instrument_id": instrument_id, "status": "ready"}

# Coordination Tasks
@task
def transfer_samples(source_robot: str, dest_instrument: str, sample_info: Dict):
    """Coordinate sample transfer between instruments"""
    logger = get_run_logger()
    logger.info(f"Transferring samples from {source_robot} to {dest_instrument}")
    
    # This would coordinate physical sample transfer
    # Could involve robotic arms, conveyor belts, or manual intervention
    
    return {
        "transfer_id": f"{source_robot}_to_{dest_instrument}_{int(time.time())}",
        "source": source_robot,
        "destination": dest_instrument,
        "samples": sample_info,
        "status": "completed"
    }

@task
def collect_results(instrument_id: str, analysis_type: str):
    """Collect analysis results from instrument"""
    logger = get_run_logger()
    logger.info(f"Collecting {analysis_type} results from {instrument_id}")
    
    # Implement result collection based on instrument type
    results = {
        "instrument_id": instrument_id,
        "analysis_type": analysis_type,
        "data": {},  # Actual data would go here
        "timestamp": datetime.now().isoformat()
    }
    
    return results

# High-Level Workflow Flows
@flow
def sample_preparation_workflow(
    robot_id: str,
    samples: List[Dict],
    preparation_steps: List[Dict]
):
    """Complete sample preparation workflow on OT-2"""
    logger = get_run_logger()
    logger.info(f"Starting sample preparation workflow for {len(samples)} samples")
    
    # Initialize robot
    init_result = initialize_ot2_protocol(robot_id)
    
    # Load labware and instruments
    labware_result = load_ot2_labware(robot_id, preparation_steps[0].get('labware', []))
    instrument_result = load_ot2_instruments(robot_id, preparation_steps[0].get('instruments', []))
    
    # Execute preparation steps
    prep_results = []
    for step in preparation_steps:
        if 'operations' in step:
            result = execute_ot2_liquid_handling(robot_id, step['operations'])
            prep_results.append(result)
    
    return {
        "workflow_type": "sample_preparation",
        "robot_id": robot_id,
        "samples_processed": len(samples),
        "initialization": init_result,
        "labware": labware_result,
        "instruments": instrument_result,
        "preparation_results": prep_results,
        "completed_at": datetime.now().isoformat()
    }

@flow
def analytical_workflow(
    prep_robot_id: str,
    analysis_instrument_id: str,
    samples: List[Dict],
    analysis_parameters: Dict
):
    """Complete analytical workflow across multiple instruments"""
    logger = get_run_logger()
    logger.info(f"Starting analytical workflow for {len(samples)} samples")
    
    # Sample preparation on OT-2
    prep_steps = analysis_parameters.get('preparation_steps', [])
    prep_result = sample_preparation_workflow(prep_robot_id, samples, prep_steps)
    
    # Wait for preparation to complete before analysis
    wait_result = wait_for_instrument(analysis_instrument_id)
    
    # Transfer samples to analytical instrument
    transfer_result = transfer_samples(
        prep_robot_id, 
        analysis_instrument_id, 
        {"samples": samples}
    )
    
    # Run analysis
    analysis_result = execute_instrument_command(
        analysis_instrument_id,
        analysis_parameters.get('analysis_command', 'run_analysis'),
        analysis_parameters.get('parameters', {})
    )
    
    # Collect results
    results = collect_results(
        analysis_instrument_id,
        analysis_parameters.get('analysis_type', 'unknown')
    )
    
    return {
        "workflow_type": "analytical",
        "prep_robot": prep_robot_id,
        "analysis_instrument": analysis_instrument_id,
        "samples_analyzed": len(samples),
        "preparation": prep_result,
        "transfer": transfer_result,
        "analysis": analysis_result,
        "results": results,
        "completed_at": datetime.now().isoformat()
    }

@flow
def high_throughput_screening_workflow(
    robot_ids: List[str],
    compound_library: List[Dict],
    assay_parameters: Dict
):
    """High-throughput screening workflow using multiple OT-2 robots"""
    logger = get_run_logger()
    logger.info(f"Starting HTS workflow with {len(robot_ids)} robots for {len(compound_library)} compounds")
    
    # Divide compounds among available robots
    compounds_per_robot = len(compound_library) // len(robot_ids)
    
    screening_results = []
    
    for i, robot_id in enumerate(robot_ids):
        start_idx = i * compounds_per_robot
        end_idx = start_idx + compounds_per_robot if i < len(robot_ids) - 1 else len(compound_library)
        
        robot_compounds = compound_library[start_idx:end_idx]
        
        # Run screening on this robot
        result = sample_preparation_workflow(
            robot_id,
            robot_compounds,
            assay_parameters.get('preparation_steps', [])
        )
        
        screening_results.append(result)
    
    return {
        "workflow_type": "high_throughput_screening",
        "robots_used": robot_ids,
        "total_compounds": len(compound_library),
        "compounds_per_robot": compounds_per_robot,
        "screening_results": screening_results,
        "completed_at": datetime.now().isoformat()
    }

# Utility functions for workflow management
def register_ot2_robot(robot_id: str, host_alias: str, password: str = "accelerate"):
    """Register an OT-2 robot with the instrument manager"""
    return instrument_manager.add_ot2(robot_id, host_alias, password)

def register_instrument(instrument_id: str, instrument_client: Any):
    """Register another laboratory instrument"""
    instrument_manager.add_instrument(instrument_id, instrument_client)

if __name__ == "__main__":
    # Example usage
    print("Workflow Orchestrator initialized")
    print("Use register_ot2_robot() and register_instrument() to add instruments")
    print("Then call the workflow flows with appropriate parameters") 