"""
Opentrons UI Package
Web-based interface for real-time OT-2 visualization and control
"""

from .ot2_state_monitor import StatefulOT2, PureSimulationOT2
from .web_ui_demo import app

__version__ = "1.0.0"
__author__ = "Opentrons Workflows"

__all__ = ['StatefulOT2', 'PureSimulationOT2', 'app'] 