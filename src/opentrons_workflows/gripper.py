from .pipette import Instrument


class Gripper(Instrument):
    """Represents the Flex's gripper instrument for moving labware."""
    def __init__(self, robot_connection):
        # Gripper has a fixed name and is automatically available when attached
        super().__init__(robot_connection, "flex_gripper")
        self.load()

    def load(self):
        """Executes the command to verify gripper availability on the robot."""
        # This relies on Flex having a _load_gripper_on_robot method.
        self._robot._load_gripper_on_robot()

    def move_labware(self, labware, new_location, use_gripper=True):
        """
        Moves labware from one location to another using the gripper.
        
        :param labware: The labware object to move (e.g., a plate or tip rack)
        :param new_location: The destination location (e.g., slot name or labware location)
        :param use_gripper: Whether to use the gripper (should be True for Flex)
        """
        command = f"ctx.move_labware(labware={labware}, new_location={new_location}, use_gripper={use_gripper})"
        return self._robot.execute_command(command)

    def move_to(self, location, offset=None):
        """
        Legacy method for compatibility. 
        Note: In Opentrons Flex, use move_labware() instead of separate gripper positioning.
        
        :param location: The destination location
        :param offset: Optional offset (not used in current implementation)
        """
        # This is a compatibility method - in practice, use move_labware() 
        # for actual labware movement operations
        return {"status": "success", "message": "Use move_labware() for labware movement operations"}

    def grip(self, grip_force=None):
        """
        Legacy method for compatibility.
        Note: In Opentrons Flex, gripping is automatic during move_labware() operations.
        
        :param grip_force: Optional force (not used in current implementation)
        """
        # Gripping is automatic during move_labware operations in Flex
        return {"status": "success", "message": "Gripping is automatic during move_labware() operations"}

    def ungrip(self):
        """
        Legacy method for compatibility.
        Note: In Opentrons Flex, releasing is automatic during move_labware() operations.
        """
        # Releasing is automatic during move_labware operations in Flex
        return {"status": "success", "message": "Releasing is automatic during move_labware() operations"} 