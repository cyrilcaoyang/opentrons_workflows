#!/usr/bin/env python3
"""
Demonstration of OT-2 REST API and Workflow Orchestrator
Shows how to integrate OT-2 with other instruments using Prefect
"""

import asyncio
import requests
import json
import time
from datetime import datetime
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.opentrons_workflows.workflow_orchestrator import (
    register_ot2_robot,
    register_instrument,
    sample_preparation_workflow,
    analytical_workflow,
    high_throughput_screening_workflow
)

# REST API Base URL (when running the FastAPI server)
API_BASE = "http://localhost:8000"

def demo_rest_api_usage():
    """Demonstrate REST API usage"""
    print("🌐 OT-2 REST API Demo")
    print("=" * 50)
    
    # Note: This requires the FastAPI server to be running
    # Start with: python src/opentrons_workflows/ot2_rest_api.py
    
    try:
        # Health check
        response = requests.get(f"{API_BASE}/health")
        if response.status_code == 200:
            print("✅ API server is running")
            print(f"   Status: {response.json()}")
        else:
            print("❌ API server not accessible")
            return
    except requests.exceptions.ConnectionError:
        print("❌ API server not running. Start with:")
        print("   python src/opentrons_workflows/ot2_rest_api.py")
        return
    
    # Connect to robot
    print("\n🔌 Connecting to OT-2...")
    connect_data = {
        "robot_id": "ot2_demo",
        "host_alias": "ot2_tailscale",
        "password": "accelerate",
        "max_retries": 3,
        "command_timeout": 30
    }
    
    response = requests.post(f"{API_BASE}/robots/ot2_demo/connect", json=connect_data)
    if response.status_code == 200:
        print("✅ Robot connected successfully")
        print(f"   Response: {response.json()}")
    else:
        print(f"❌ Connection failed: {response.text}")
        return
    
    # Get robot status
    print("\n📊 Checking robot status...")
    response = requests.get(f"{API_BASE}/robots/ot2_demo/status")
    if response.status_code == 200:
        print("✅ Robot status retrieved")
        print(f"   Status: {response.json()}")
    
    # Home the robot
    print("\n🏠 Homing robot...")
    response = requests.post(f"{API_BASE}/robots/ot2_demo/home")
    if response.status_code == 200:
        print("✅ Robot homed successfully")
    
    # Setup protocol
    print("\n⚙️  Setting up protocol...")
    setup_data = {
        "labware": [
            {
                "nickname": "tips_300",
                "loadname": "opentrons_96_tiprack_300ul", 
                "location": "1",
                "ot_default": True
            },
            {
                "nickname": "plate_96",
                "loadname": "corning_96_wellplate_360ul_flat",
                "location": "2", 
                "ot_default": True
            }
        ],
        "instruments": [
            {
                "nickname": "p300",
                "instrument_name": "p300_single_gen2",
                "mount": "right",
                "ot_default": True
            }
        ]
    }
    
    response = requests.post(f"{API_BASE}/robots/ot2_demo/setup", json=setup_data)
    if response.status_code == 200:
        print("✅ Protocol setup completed")
        print(f"   Setup: {response.json()}")
    
    # Execute liquid handling
    print("\n💧 Executing liquid handling...")
    aspirate_data = {
        "pip_name": "p300",
        "volume": 100,
        "location": {
            "labware_nickname": "plate_96",
            "position": "A1",
            "center": True
        }
    }
    
    # Note: This would require tips to be loaded first in a real scenario
    # response = requests.post(f"{API_BASE}/robots/ot2_demo/aspirate", json=aspirate_data)
    
    # Disconnect
    print("\n🔌 Disconnecting...")
    response = requests.delete(f"{API_BASE}/robots/ot2_demo/disconnect")
    if response.status_code == 200:
        print("✅ Robot disconnected")

def demo_workflow_orchestrator():
    """Demonstrate Prefect workflow orchestrator"""
    print("\n🔄 Workflow Orchestrator Demo")
    print("=" * 50)
    
    # Register instruments
    print("📝 Registering instruments...")
    
    # Register OT-2 robot
    if register_ot2_robot("ot2_main", "ot2_tailscale"):
        print("✅ OT-2 robot registered: ot2_main")
    else:
        print("❌ Failed to register OT-2 robot")
        return
    
    # Register mock instruments (replace with real instrument clients)
    class MockHPLC:
        def run_analysis(self, samples):
            return {"status": "completed", "results": f"Analyzed {len(samples)} samples"}
    
    class MockSpectrophotometer:
        def measure_absorbance(self, wavelength):
            return {"absorbance": 0.85, "wavelength": wavelength}
    
    register_instrument("hplc_01", MockHPLC())
    register_instrument("spec_01", MockSpectrophotometer())
    print("✅ Mock instruments registered")
    
    # Define sample preparation workflow
    print("\n🧪 Running sample preparation workflow...")
    
    samples = [
        {"id": "sample_001", "type": "compound_A"},
        {"id": "sample_002", "type": "compound_B"},
        {"id": "sample_003", "type": "compound_C"}
    ]
    
    preparation_steps = [
        {
            "labware": [
                {"nickname": "tips_300", "loadname": "opentrons_96_tiprack_300ul", "location": "1"},
                {"nickname": "source_plate", "loadname": "corning_96_wellplate_360ul_flat", "location": "2"},
                {"nickname": "dest_plate", "loadname": "corning_96_wellplate_360ul_flat", "location": "3"}
            ],
            "instruments": [
                {"nickname": "p300", "instrument_name": "p300_single_gen2", "mount": "right"}
            ],
            "operations": [
                {
                    "type": "pick_up_tip",
                    "description": "Pick up tip for transfers",
                    "labware": "tips_300",
                    "position": "A1",
                    "pipette": "p300"
                },
                {
                    "type": "aspirate",
                    "description": "Aspirate from source",
                    "labware": "source_plate",
                    "position": "A1",
                    "pipette": "p300",
                    "volume": 100,
                    "offset": {"bottom": 5}
                },
                {
                    "type": "dispense", 
                    "description": "Dispense to destination",
                    "labware": "dest_plate",
                    "position": "A1",
                    "pipette": "p300",
                    "volume": 100,
                    "offset": {"top": -1}
                },
                {
                    "type": "drop_tip",
                    "description": "Drop tip",
                    "pipette": "p300"
                }
            ]
        }
    ]
    
    try:
        # Run the workflow
        result = sample_preparation_workflow("ot2_main", samples, preparation_steps)
        print("✅ Sample preparation workflow completed")
        print(f"   Results: {result}")
        
    except Exception as e:
        print(f"❌ Workflow failed: {e}")
    
    # Demonstrate analytical workflow
    print("\n🔬 Running analytical workflow...")
    
    analysis_parameters = {
        "preparation_steps": preparation_steps,
        "analysis_command": "run_hplc_analysis",
        "analysis_type": "chromatography",
        "parameters": {
            "method": "reverse_phase",
            "runtime": 30,
            "flow_rate": 1.0
        }
    }
    
    try:
        result = analytical_workflow(
            "ot2_main",
            "hplc_01", 
            samples,
            analysis_parameters
        )
        print("✅ Analytical workflow completed")
        print(f"   Results: {result}")
        
    except Exception as e:
        print(f"❌ Analytical workflow failed: {e}")

def demo_high_throughput_screening():
    """Demonstrate high-throughput screening workflow"""
    print("\n🚀 High-Throughput Screening Demo")
    print("=" * 50)
    
    # Simulate compound library
    compound_library = []
    for i in range(96):  # Full 96-well plate
        compound_library.append({
            "id": f"compound_{i+1:03d}",
            "molecular_weight": 250 + (i * 2),
            "concentration": 10.0,  # mM
            "plate_position": f"{chr(65 + i//12)}{(i%12)+1}"
        })
    
    # Assay parameters
    assay_parameters = {
        "preparation_steps": [
            {
                "labware": [
                    {"nickname": "compound_plate", "loadname": "corning_96_wellplate_360ul_flat", "location": "1"},
                    {"nickname": "assay_plate", "loadname": "corning_96_wellplate_360ul_flat", "location": "2"},
                    {"nickname": "tips_20", "loadname": "opentrons_96_tiprack_20ul", "location": "3"}
                ],
                "instruments": [
                    {"nickname": "p20", "instrument_name": "p20_single_gen2", "mount": "left"}
                ],
                "operations": [
                    {
                        "type": "delay",
                        "description": "Equilibration delay",
                        "seconds": 30
                    }
                ]
            }
        ]
    }
    
    # Register multiple robots for parallel processing
    robot_ids = ["ot2_main"]  # In real scenario, you'd have multiple robots
    
    try:
        result = high_throughput_screening_workflow(
            robot_ids,
            compound_library,
            assay_parameters
        )
        print("✅ High-throughput screening completed")
        print(f"   Processed {len(compound_library)} compounds")
        print(f"   Used {len(robot_ids)} robots")
        print(f"   Results: {result}")
        
    except Exception as e:
        print(f"❌ HTS workflow failed: {e}")

def main():
    """Main demonstration function"""
    print("🧬 OT-2 Integration Demo")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Choose which demos to run
    print("Available demos:")
    print("1. REST API Usage (requires FastAPI server running)")
    print("2. Workflow Orchestrator (Prefect workflows)")
    print("3. High-Throughput Screening")
    print("4. All demos")
    
    choice = input("\nSelect demo (1-4): ").strip()
    
    if choice == "1":
        demo_rest_api_usage()
    elif choice == "2":
        demo_workflow_orchestrator()
    elif choice == "3":
        demo_high_throughput_screening()
    elif choice == "4":
        demo_rest_api_usage()
        demo_workflow_orchestrator()
        demo_high_throughput_screening()
    else:
        print("Invalid choice. Running workflow orchestrator demo...")
        demo_workflow_orchestrator()
    
    print("\n🎉 Demo completed!")
    print("\nNext steps:")
    print("1. Start FastAPI server: python src/opentrons_workflows/ot2_rest_api.py")
    print("2. Access API docs: http://localhost:8000/docs")
    print("3. Set up Prefect server for workflow monitoring")
    print("4. Integrate with your specific instruments")

if __name__ == "__main__":
    main() 