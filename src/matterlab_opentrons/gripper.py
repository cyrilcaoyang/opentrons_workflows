from .pipette import Instrument


class Gripper(Instrument):
    """Represents the Flex's gripper instrument for moving labware."""
    def __init__(self, robot_connection):
        # Gripper has a fixed name and mount ('extension')
        super().__init__(robot_connection, "flex_gripper")
        self.load()

    def load(self):
        """Executes the command to load the gripper on the robot."""
        # This relies on Flex having a _load_gripper_on_robot method.
        self._robot._load_gripper_on_robot()

    def move_to(self, location, offset=None):
        """
        Moves the gripper to a specified location.

        :param location: The destination, typically a `Labware` object's location property.
        :param offset: An optional dictionary for x, y, z offset.
        """
        offset_str = f", offset={offset}" if offset else ""
        command = f"self.gripper.move_to({location}{offset_str})"
        return self._robot.execute_command(command)

    def grip(self, grip_force=None):
        """
        Closes the gripper jaw to grab a piece of labware.

        :param grip_force: Optional force in Pascals to apply.
        """
        force_str = f", grip_force={grip_force}" if grip_force else ""
        # Using a default force, can be overridden
        command = f"self.gripper.grip(force_pascals=50{force_str})"
        return self._robot.execute_command(command)

    def ungrip(self):
        """Opens the gripper jaw to release a piece of labware."""
        command = "self.gripper.ungrip()"
        return self._robot.execute_command(command) 