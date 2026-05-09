"""SSH transport layer.

This module is the new import location for the existing Paramiko-based
``SSHClient``. The implementation remains in the legacy module for now so
existing imports keep working while the package is reorganized.
"""

from ..opentrons_sshclient import SSHClient, SessionState

__all__ = ["SSHClient", "SessionState"]
