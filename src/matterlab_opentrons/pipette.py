from abc import ABC, abstractmethod


class Instrument(ABC):
    """Abstract base class for all robot instruments (pipettes, gripper, etc.)."""
    def __init__(self, robot_connection, instrument_name):
        self._robot = robot_connection
        self.name = instrument_name

    @abstractmethod
    def load(self):
        """Method to execute the actual loading command on the robot."""
        pass


class Pipette(Instrument):
    """Represents a pipette instrument, handling all liquid handling commands."""
    def __init__(self, robot_connection, pipette_name, mount):
        super().__init__(robot_connection, pipette_name)
        if mount not in ['left', 'right']:
            raise ValueError("Mount must be 'left' or 'right'.")
        self.mount = mount
        self.load()  # Load the instrument on initialization

    def load(self):
        """Executes the command to load the pipette on the robot."""
        self._robot._load_pipette_on_robot(self.name, self.mount)

    def pick_up_tip(self):
        """Picks up a tip from the next available position in its associated tip rack."""
        command = f"self.ctx.loaded_instruments['{self.mount}'].pick_up_tip()"
        return self._robot.execute_command(command)

    def drop_tip(self):
        """Drops the current tip into the trash."""
        command = f"self.ctx.loaded_instruments['{self.mount}'].drop_tip()"
        return self._robot.execute_command(command)

    def aspirate(self, volume, location):
        """
        Aspirates a specific volume of liquid from a given location.

        :param volume: The volume to aspirate, in microliters (µL).
        :param location: The source location (a `Well` object).
        """
        command = f"self.ctx.loaded_instruments['{self.mount}'].aspirate({volume}, {location})"
        return self._robot.execute_command(command)

    def dispense(self, volume, location):
        """
        Dispenses a specific volume of liquid into a given location.

        :param volume: The volume to dispense, in microliters (µL).
        :param location: The destination location (a `Well` object).
        """
        command = f"self.ctx.loaded_instruments['{self.mount}'].dispense({volume}, {location})"
        return self._robot.execute_command(command)

    def mix(self, repetitions, volume, location):
        """
        Mixes a solution by repeatedly aspirating and dispensing.

        :param repetitions: The number of mix cycles to perform.
        :param volume: The volume to aspirate and dispense in each cycle.
        :param location: The location to perform the mix (a `Well` object).
        """
        command = f"self.ctx.loaded_instruments['{self.mount}'].mix({repetitions}, {volume}, {location})"
        return self._robot.execute_command(command) 