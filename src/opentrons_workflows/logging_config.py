"""
Simplified logging configuration for opentrons_workflows.

Always accepts external logger. If not given, stores in /logs under project.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def setup_default_logging(log_dir=None):
    """
    Set up default logging to project /logs folder.
    
    Args:
        log_dir (str, optional): Directory to store log files. 
                               Defaults to top-level 'logs' directory.
    """
    # Determine log directory - default to project root /logs
    if log_dir is None:
        package_root = Path(__file__).parent.parent.parent
        log_dir = package_root / "logs"
    
    log_dir = Path(log_dir)
    log_dir.mkdir(exist_ok=True)
    
    # Create log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = log_dir / f"opentrons_workflows_{timestamp}.log"
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Get root logger and clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    
    # Add file handler
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    return logging.getLogger('opentrons_workflows')


def get_logger(name=None, custom_logger: Optional[logging.Logger] = None):
    """
    Get a logger - either custom or default.
    
    Args:
        name (str, optional): Logger name
        custom_logger (logging.Logger, optional): Custom logger to use
        
    Returns:
        logging.Logger: Logger instance
    """
    # If custom logger provided, use it
    if custom_logger is not None:
        return custom_logger
    
    # Otherwise use default logger
    if name is None:
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get('__name__', 'opentrons_workflows')
        else:
            name = 'opentrons_workflows'
    
    return logging.getLogger(name)


def create_custom_logger(name: str, log_file: Optional[str] = None, 
                        log_level: int = logging.INFO, 
                        console_output: bool = True) -> logging.Logger:
    """
    Create a custom logger with specified configuration.
    
    Args:
        name (str): Logger name
        log_file (str, optional): Path to log file. If None, no file logging.
        log_level (int): Logging level
        console_output (bool): Whether to output to console
        
    Returns:
        logging.Logger: Custom configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.handlers.clear()
    
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Add file handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Add console handler if requested
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Prevent propagation to avoid duplicate messages
    logger.propagate = False
    
    return logger 