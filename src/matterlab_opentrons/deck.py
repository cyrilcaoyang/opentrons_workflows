import os
import json
import logging


class Well:
    """
    Represents a single well in a piece of labware.

    This class is primarily used to generate the correct string representation
    for use in robot commands.
    """
    def __init__(self, parent_labware, well_name):
        self._parent_labware = parent_labware
        self.name = well_name

    def __str__(self):
        # This creates the string representation needed for the robot commands
        return f"self.ctx.loaded_labware['{self._parent_labware.location}']['{self.name}']"


class Labware:
    """
    Represents a piece of labware loaded on the robot's deck.
    Provides access to individual wells.
    """
    def __init__(self, definition, location):
        self.definition = definition
        self.location = location
        # Handle getting loadName from both custom (nested) and standard (placeholder) definitions
        self.load_name = definition.get('parameters', {}).get('loadName') or definition.get('loadName')
        self.wells = {well_name: Well(self, well_name) for well_name in definition.get('wells', {})}

    def __getitem__(self, well_name):
        """Allows dictionary-style access to wells (e.g., my_plate['A1'])."""
        if well_name not in self.wells:
            raise KeyError(f"Well '{well_name}' not found in labware '{self.load_name}'.")
        return self.wells[well_name]


class Deck:
    """
    Represents the robot's deck, managing all labware definitions and loaded labware.
    """
    def __init__(self, robot_connection):
        self._robot = robot_connection
        self.labware = {}  # Stores Labware objects by location keyed by location string
        self._labware_definitions = self._load_labware_definitions()

    def _load_labware_definitions(self):
        """
        Loads all available labware definitions from the user_scripts/generated_labware directory.
        This is called once on initialization.
        """
        definitions = {}
        # Path relative to this file's location (src/matterlab_opentrons/deck.py)
        # Go up two directories to the project root, then into user_scripts/generated_labware
        labware_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'user_scripts', 'generated_labware'))
        
        if not os.path.exists(labware_dir):
            return definitions

        for filename in os.listdir(labware_dir):
            if filename.endswith(".json"):
                with open(os.path.join(labware_dir, filename)) as f:
                    definition = json.load(f)
                    # Correctly parse the loadName from within the 'parameters' dictionary
                    load_name = definition.get('parameters', {}).get('loadName')
                    if load_name:
                        definitions[load_name] = definition
        return definitions

    def load_labware(self, load_name, location):
        """
        Loads a labware onto the deck by its 'load_name' at a specific location.

        :param load_name: The unique name of the labware definition to load.
        :param location: The deck slot where the labware will be placed (e.g., 'D1').
        :return: A Labware object representing the newly loaded labware.
        """
        # The local check is primarily for providing a better error message early.
        # The robot is the final authority on whether a labware can be loaded.
        if load_name not in self._labware_definitions:
            logging.warning(f"Labware '{load_name}' not found in local custom definitions. Assuming it is a standard labware.")
        
        definition = self._labware_definitions.get(load_name, {"loadName": load_name, "wells": {}})

        # Tell the robot to actually load the labware
        command = f"ctx.load_labware('{load_name}', '{location}')"
        response = self._robot.execute_command(command)

        if response.get('status') != 'success':
            error_msg = response.get('error', 'Unknown error')
            tb = response.get('traceback', 'No traceback available')
            raise RuntimeError(f"Failed to load labware '{load_name}' on robot: {error_msg}\n{tb}")

        labware_obj = Labware(definition, location)
        self.labware[location] = labware_obj
        return labware_obj

    def __getitem__(self, location):
        """Allows dictionary-style access to loaded labware (e.g., deck['D1'])."""
        if location not in self.labware:
            raise KeyError(f"No labware loaded at location '{location}'.")
        return self.labware[location] 