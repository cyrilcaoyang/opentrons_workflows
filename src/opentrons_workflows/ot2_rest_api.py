"""
OT-2 REST API with Prefect Integration
Provides HTTP endpoints for OT-2 robot control and workflow orchestration
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import asyncio
import logging
import uuid
from datetime import datetime
from enum import Enum
import json

# Prefect imports
from prefect import flow, task, get_run_logger
from prefect.client.schemas import FlowRun
from prefect.server.schemas.states import StateType

# Import our robust SSH client
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from opentrons_workflows.robust_ssh_client import RobustSSHClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="OT-2 REST API",
    description="REST API for Opentrons OT-2 robot control with Prefect workflow integration",
    version="1.0.0"
)

# CORS middleware for web frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state management
class RobotManager:
    def __init__(self):
        self.connections: Dict[str, RobustSSHClient] = {}
        self.active_flows: Dict[str, str] = {}  # flow_id -> robot_id
        
    def get_connection(self, robot_id: str) -> RobustSSHClient:
        if robot_id not in self.connections:
            raise HTTPException(status_code=404, detail=f"Robot {robot_id} not connected")
        return self.connections[robot_id]
    
    def add_connection(self, robot_id: str, client: RobustSSHClient):
        self.connections[robot_id] = client
    
    def remove_connection(self, robot_id: str):
        if robot_id in self.connections:
            try:
                self.connections[robot_id].close()
            except:
                pass
            del self.connections[robot_id]

robot_manager = RobotManager()

# Pydantic models for API
class RobotConnectionConfig(BaseModel):
    robot_id: str = Field(..., description="Unique identifier for the robot")
    host_alias: str = Field(..., description="SSH host alias from ~/.ssh/config")
    password: str = Field(default="accelerate", description="SSH password")
    max_retries: int = Field(default=3, description="Maximum connection retry attempts")
    command_timeout: int = Field(default=30, description="Command timeout in seconds")

class CommandRequest(BaseModel):
    command: str = Field(..., description="Python command to execute on robot")
    timeout: Optional[int] = Field(default=30, description="Command timeout in seconds")

class LabwareConfig(BaseModel):
    nickname: str
    loadname: Optional[str] = None
    location: str
    ot_default: bool = True
    config: Dict = Field(default_factory=dict)

class InstrumentConfig(BaseModel):
    nickname: str
    instrument_name: str
    mount: str
    ot_default: bool = True
    config: Dict = Field(default_factory=dict)

class ModuleConfig(BaseModel):
    nickname: str
    module_name: str
    location: str
    adapter: str

class ProtocolSetupRequest(BaseModel):
    labware: List[LabwareConfig] = Field(default_factory=list)
    instruments: List[InstrumentConfig] = Field(default_factory=list)
    modules: List[ModuleConfig] = Field(default_factory=list)

class PipetteMovement(BaseModel):
    labware_nickname: str
    position: str
    top: float = 0
    bottom: float = 0
    center: bool = False

class AspirationRequest(BaseModel):
    pip_name: str
    volume: float
    location: PipetteMovement

class DispenseRequest(BaseModel):
    pip_name: str
    volume: float
    location: PipetteMovement
    push_out: Optional[float] = None

class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class WorkflowResponse(BaseModel):
    workflow_id: str
    status: WorkflowStatus
    robot_id: str
    created_at: datetime
    updated_at: datetime
    result: Optional[Dict] = None
    error: Optional[str] = None

# Robot Connection Endpoints
@app.post("/robots/{robot_id}/connect")
async def connect_robot(robot_id: str, config: RobotConnectionConfig):
    """Connect to an OT-2 robot"""
    try:
        client = RobustSSHClient(
            host_alias=config.host_alias,
            password=config.password,
            max_retries=config.max_retries,
            command_timeout=config.command_timeout
        )
        
        if client.connect():
            robot_manager.add_connection(robot_id, client)
            return {
                "status": "connected",
                "robot_id": robot_id,
                "connection_info": client.get_connection_status()
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to connect to robot")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")

@app.delete("/robots/{robot_id}/disconnect")
async def disconnect_robot(robot_id: str):
    """Disconnect from an OT-2 robot"""
    try:
        robot_manager.remove_connection(robot_id)
        return {"status": "disconnected", "robot_id": robot_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Disconnection error: {str(e)}")

@app.get("/robots/{robot_id}/status")
async def get_robot_status(robot_id: str):
    """Get robot connection status"""
    try:
        client = robot_manager.get_connection(robot_id)
        return {
            "robot_id": robot_id,
            "status": client.get_connection_status(),
            "ping": client.ping()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status check error: {str(e)}")

@app.get("/robots")
async def list_robots():
    """List all connected robots"""
    robots = []
    for robot_id, client in robot_manager.connections.items():
        try:
            status = client.get_connection_status()
            robots.append({
                "robot_id": robot_id,
                "connected": status["connected"],
                "hostname": status["hostname"]
            })
        except:
            robots.append({
                "robot_id": robot_id,
                "connected": False,
                "hostname": "unknown"
            })
    
    return {"robots": robots}

# Direct Command Execution
@app.post("/robots/{robot_id}/execute")
async def execute_command(robot_id: str, request: CommandRequest):
    """Execute a raw Python command on the robot"""
    try:
        client = robot_manager.get_connection(robot_id)
        response = client.invoke_with_retry(request.command, timeout=request.timeout)
        
        return {
            "robot_id": robot_id,
            "command": request.command,
            "response": response,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Command execution error: {str(e)}")

# High-level Robot Operations
@app.post("/robots/{robot_id}/home")
async def home_robot(robot_id: str):
    """Home the robot"""
    try:
        client = robot_manager.get_connection(robot_id)
        response = client.invoke_with_retry("protocol.home()")
        return {"robot_id": robot_id, "status": "homed", "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Homing error: {str(e)}")

@app.post("/robots/{robot_id}/setup")
async def setup_protocol(robot_id: str, setup: ProtocolSetupRequest):
    """Setup protocol with labware, instruments, and modules"""
    try:
        client = robot_manager.get_connection(robot_id)
        
        # Initialize protocol
        commands = [
            "from opentrons import execute",
            "from opentrons.types import Point, Location", 
            "from opentrons import protocol_api",
            "protocol = execute.get_protocol_api('2.21')"
        ]
        
        for cmd in commands:
            client.invoke_with_retry(cmd)
        
        # Load labware
        for labware in setup.labware:
            if labware.ot_default:
                cmd = f"{labware.nickname} = protocol.load_labware(load_name='{labware.loadname}', location='{labware.location}')"
            else:
                cmd = f"{labware.nickname} = protocol.load_labware_from_definition(labware_def={labware.config}, location='{labware.location}')"
            client.invoke_with_retry(cmd)
        
        # Load instruments
        for instrument in setup.instruments:
            cmd = f"{instrument.nickname} = protocol.load_instrument(instrument_name='{instrument.instrument_name}', mount='{instrument.mount}')"
            client.invoke_with_retry(cmd)
        
        # Load modules
        for module in setup.modules:
            cmd = f"{module.nickname} = protocol.load_module(module_name='{module.module_name}', location='{module.location}')"
            client.invoke_with_retry(cmd)
            cmd = f"{module.nickname}_adapter = {module.nickname}.load_adapter(name='{module.adapter}')"
            client.invoke_with_retry(cmd)
        
        return {
            "robot_id": robot_id,
            "status": "setup_complete",
            "labware_count": len(setup.labware),
            "instrument_count": len(setup.instruments),
            "module_count": len(setup.modules)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Setup error: {str(e)}")

@app.post("/robots/{robot_id}/aspirate")
async def aspirate(robot_id: str, request: AspirationRequest):
    """Aspirate liquid with pipette"""
    try:
        client = robot_manager.get_connection(robot_id)
        
        # Set location
        if request.location.top:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].top({request.location.top})"
        elif request.location.bottom:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].bottom({request.location.bottom})"
        elif request.location.center:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].center()"
        else:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].top(0)"
        
        client.invoke_with_retry(location_cmd)
        
        # Aspirate
        aspirate_cmd = f"{request.pip_name}.aspirate(volume={request.volume}, location=location)"
        response = client.invoke_with_retry(aspirate_cmd)
        
        return {
            "robot_id": robot_id,
            "action": "aspirate",
            "pipette": request.pip_name,
            "volume": request.volume,
            "location": f"{request.location.labware_nickname}[{request.location.position}]",
            "response": response
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Aspiration error: {str(e)}")

@app.post("/robots/{robot_id}/dispense")
async def dispense(robot_id: str, request: DispenseRequest):
    """Dispense liquid with pipette"""
    try:
        client = robot_manager.get_connection(robot_id)
        
        # Set location
        if request.location.top:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].top({request.location.top})"
        elif request.location.bottom:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].bottom({request.location.bottom})"
        elif request.location.center:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].center()"
        else:
            location_cmd = f"location = {request.location.labware_nickname}['{request.location.position}'].top(0)"
        
        client.invoke_with_retry(location_cmd)
        
        # Dispense
        dispense_cmd = f"{request.pip_name}.dispense(volume={request.volume}, location=location"
        if request.push_out is not None:
            dispense_cmd += f", push_out={request.push_out}"
        dispense_cmd += ")"
        
        response = client.invoke_with_retry(dispense_cmd)
        
        return {
            "robot_id": robot_id,
            "action": "dispense", 
            "pipette": request.pip_name,
            "volume": request.volume,
            "location": f"{request.location.labware_nickname}[{request.location.position}]",
            "push_out": request.push_out,
            "response": response
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dispense error: {str(e)}")

# Prefect Integration for Workflows
@task
def execute_robot_command(robot_id: str, command: str, timeout: int = 30):
    """Prefect task to execute robot command"""
    logger = get_run_logger()
    logger.info(f"Executing command on robot {robot_id}: {command}")
    
    try:
        client = robot_manager.get_connection(robot_id)
        response = client.invoke_with_retry(command, timeout=timeout)
        logger.info(f"Command completed successfully")
        return response
    except Exception as e:
        logger.error(f"Command failed: {str(e)}")
        raise

@task
def setup_robot_protocol(robot_id: str, setup_config: Dict):
    """Prefect task to setup robot protocol"""
    logger = get_run_logger()
    logger.info(f"Setting up protocol on robot {robot_id}")
    
    try:
        client = robot_manager.get_connection(robot_id)
        
        # Initialize protocol
        commands = [
            "from opentrons import execute",
            "from opentrons.types import Point, Location",
            "from opentrons import protocol_api", 
            "protocol = execute.get_protocol_api('2.21')"
        ]
        
        for cmd in commands:
            client.invoke_with_retry(cmd)
            
        logger.info("Protocol setup completed")
        return "setup_complete"
        
    except Exception as e:
        logger.error(f"Setup failed: {str(e)}")
        raise

@flow
def liquid_handling_workflow(robot_id: str, steps: List[Dict]):
    """Prefect flow for liquid handling workflow"""
    logger = get_run_logger()
    logger.info(f"Starting liquid handling workflow on robot {robot_id}")
    
    results = []
    
    # Setup protocol
    setup_result = setup_robot_protocol(robot_id, {})
    results.append({"step": "setup", "result": setup_result})
    
    # Execute each step
    for i, step in enumerate(steps):
        logger.info(f"Executing step {i+1}: {step.get('description', 'Unknown step')}")
        
        command = step.get('command')
        if command:
            result = execute_robot_command(
                robot_id, 
                command, 
                timeout=step.get('timeout', 30)
            )
            results.append({
                "step": f"step_{i+1}",
                "description": step.get('description'),
                "command": command,
                "result": result
            })
    
    logger.info("Workflow completed successfully")
    return results

@app.post("/workflows/liquid-handling")
async def start_liquid_handling_workflow(
    robot_id: str,
    steps: List[Dict],
    background_tasks: BackgroundTasks
):
    """Start a liquid handling workflow using Prefect"""
    try:
        # Validate robot connection
        robot_manager.get_connection(robot_id)
        
        # Generate workflow ID
        workflow_id = str(uuid.uuid4())
        
        # Start Prefect flow
        def run_workflow():
            flow_run = liquid_handling_workflow(robot_id, steps)
            robot_manager.active_flows[workflow_id] = robot_id
            return flow_run
        
        background_tasks.add_task(run_workflow)
        
        return {
            "workflow_id": workflow_id,
            "robot_id": robot_id,
            "status": "started",
            "steps_count": len(steps),
            "created_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workflow start error: {str(e)}")

@app.get("/workflows/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get workflow status"""
    try:
        if workflow_id not in robot_manager.active_flows:
            raise HTTPException(status_code=404, detail="Workflow not found")
        
        # In a real implementation, you'd query Prefect's API for flow run status
        return {
            "workflow_id": workflow_id,
            "status": "running",  # This would come from Prefect
            "robot_id": robot_manager.active_flows[workflow_id],
            "updated_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Status check error: {str(e)}")

# Health check endpoint
@app.get("/health")
async def health_check():
    """API health check"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "connected_robots": len(robot_manager.connections),
        "active_workflows": len(robot_manager.active_flows)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 