#!/usr/bin/env python3
"""
Simplified Logging Demo

Shows how to use the opentrons_workflows logging system:
1. Default logging to /logs folder
2. Custom logger usage
"""

import os
import sys
import logging
from pathlib import Path

# Add the package to the path for demo purposes
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import opentrons_workflows

def demo_default_logging():
    """Demo 1: Default logging to project /logs folder"""
    print("=== Demo 1: Default Logging ===")
    
    # Simple connect - uses default logging to /logs
    try:
        # This will set up default logging automatically
        from opentrons_workflows.logging_config import setup_default_logging
        logger = setup_default_logging()
        
        logger.info("This is using default logging to /logs folder")
        logger.warning("Warning message example")
        logger.error("Error message example")
        
        print("✓ Default logging configured - check logs/ folder for output")
        
    except Exception as e:
        print(f"Demo 1 failed: {e}")
        
def demo_custom_logger():
    """Demo 2: Using a custom logger"""
    print("\n=== Demo 2: Custom Logger ===")
    
    try:
        # Create a custom logger using the helper function
        from opentrons_workflows.logging_config import create_custom_logger
        
        custom_logger = create_custom_logger(
            name="my_custom_logger",
            log_file="custom_log.txt",  # Custom log file
            log_level=logging.DEBUG,
            console_output=True
        )
        
        custom_logger.info("This is a custom logger message")
        custom_logger.debug("Debug message (will show because level is DEBUG)")
        custom_logger.warning("Custom warning")
        
        print("✓ Custom logger configured - check custom_log.txt for output")
        
    except Exception as e:
        print(f"Demo 2 failed: {e}")

def demo_connect_with_custom_logger():
    """Demo 3: Using connect() with custom logger"""
    print("\n=== Demo 3: Connect with Custom Logger ===")
    
    try:
        # Create custom logger
        from opentrons_workflows.logging_config import create_custom_logger
        my_logger = create_custom_logger(
            name="robot_connection",
            log_file="robot_connection.log",
            console_output=True
        )
        
        # Note: This would normally connect to a real robot
        # For demo, we'll just show the logging setup
        print("Would call: opentrons_workflows.connect('192.168.1.100', custom_logger=my_logger)")
        my_logger.info("This shows how custom logger would be used with connect()")
        
        print("✓ Custom logger ready for robot connection")
        
    except Exception as e:
        print(f"Demo 3 failed: {e}")

def demo_simple_usage():
    """Demo 4: Most common usage patterns"""
    print("\n=== Demo 4: Common Usage Patterns ===")
    
    print("# Most common - just use defaults:")
    print("robot = opentrons_workflows.connect('192.168.1.100')")
    print("# Logs go to logs/ folder automatically")
    
    print("\n# With custom logger:")
    print("my_logger = opentrons_workflows.create_custom_logger('my_robot', 'my_robot.log')")
    print("robot = opentrons_workflows.connect('192.168.1.100', custom_logger=my_logger)")
    
    print("✓ Usage patterns shown")

if __name__ == "__main__":
    print("Opentrons Workflows - Simplified Logging Demo")
    print("=" * 50)
    
    demo_default_logging()
    demo_custom_logger() 
    demo_connect_with_custom_logger()
    demo_simple_usage()
    
    print("\n" + "=" * 50)
    print("Demo completed!")
    print("Check the logs/ folder and custom log files for output.")
    
    # Cleanup demo files
    for file in ["custom_log.txt", "robot_connection.log"]:
        if os.path.exists(file):
            os.remove(file)
            print(f"Cleaned up {file}") 