import json
from pathlib import Path
from typing import Union, Dict, List, Optional
import ast
from .logging_config import get_logger


class LabwareGenerator:
    """
    Generates Opentrons-compatible labware definition files from a dictionary.

    This class takes a Python dictionary that describes a piece of labware,
    validates its dimensions, and constructs a JSON definition file that can be
    used in Opentrons protocols. It is designed to be run as a standalone script.

    Attributes:
        MAX_DIMENSIONS (Dict[str, float]): The maximum allowed dimensions for labware.
        logger: A logger for recording messages.
        template (Dict): The base template for the labware definition.
        design (Dict): The user-provided dictionary describing the labware.
    """
    MAX_DIMENSIONS = {
        "x": 127, "y": 85.5, "z": 200
    }

    def __init__(
            self,
            well_plate_design: Dict
    ):
        """
        Initializes the LabwareGenerator with a labware design.

        Args:
            well_plate_design (Dict): A dictionary containing the specifications
                                      for the labware to be generated.
        """
        self.logger = get_logger(__name__)
        self._load_template()
        self.design = well_plate_design
        self._row_count = 0
        self._col_count = 0

    def _load_template(self, template_path: Path = None):
        """
        Loads the JSON template file for creating labware definitions.

        Args:
            template_path (Path, optional): The path to the template file.
                                            If not provided, it defaults to
                                            'user_scripts/labware_template.json'.

        Raises:
            FileExistsError: If the template file cannot be found.
        """
        if template_path is None:
            template_path = Path("user_scripts") / "labware_template.json"
        if not template_path.exists():
            raise FileExistsError(f"Template not found at {template_path}!")
        with open(template_path, "r") as f:
            self.template = json.load(f)

    def create_plate(self):
        """
        Sets the overall dimensions of the labware based on the design.

        It calculates the maximum x, y, and z dimensions from all the plates
        defined in the design and warns if they exceed the maximum allowed values.
        """
        for dimen in ("x", "y", "z"):
            dimen_max = max([plate[f"{dimen}Dimension"] for plate in self.design["plates"]])
            if dimen_max > self.MAX_DIMENSIONS[dimen]:
                self.logger.warning(f"{dimen} dimension value {dimen_max} out of range: 0 - {self.MAX_DIMENSIONS[dimen]} mm")
            self.template["dimensions"][f"{dimen}Dimension"] = dimen_max

    def metadata(self, display_name: str = None, display_category: str = None, tags: List = None):
        """
        Populates the metadata for the labware definition.

        Args:
            display_name (str, optional): The name to display in the Opentrons App.
            display_category (str, optional): The category of the labware.
            tags (List, optional): A list of tags for filtering.
        """
        if display_name is None:
            display_name = self.design["display_name"]
        if display_category is None:
            display_category = self.design["display_category"]
            if display_category not in ["tipRack","wellPlate","reservoir","aluminumBlock"]:
                raise ValueError("Unsupported display category")

        if tags is None:
            tags = self.design["tags"]
        self.template["metadata"] = {
            "displayName": display_name,
            "displayCategory": display_category,
            "displayVolumeUnits": "\u00b5L",
            "tags": tags
        }
        self.logger.info(f"metadata updated {self.template['metadata']}")

    def parameters(self,
                   load_name: str = None,
                   format: str="irregular",
                   magnetic: bool=False):
        """
        Sets the parameters for the labware definition.

        Args:
            load_name (str, optional): The API load name for the labware.
            format (str, optional): The format of the labware (e.g., 'irregular').
            magnetic (bool, optional): Whether it is compatible with a magnetic module.
        """
        if load_name is None:
            load_name = self.design["load_name"]
        self.template["parameters"] = {
            "format": format,
            "quirks": [],
            "isMagneticModuleCompatible": magnetic,
            "loadName": load_name
        }
        if self.design["display_category"] == "tipRack":
            self.template["parameters"]["isTiprack"] = True
            self.template["parameters"]["tipLength"] = self.design["plates"][0]["well_depth"]
        else:
            self.template["parameters"]["isTiprack"] = False
        self.logger.info(f"parameters updated {self.template['parameters']}")

    def _generate_wells_for_plate(self, plate: Dict):
        """
        Generates the well definitions for a single plate section.

        This method calculates the coordinates and properties for each well in a
        given plate section and adds them to the main labware definition.

        Args:
            plate (Dict): A dictionary describing a single plate or section of the labware.
        """
        # Ensure the 'ordering' list is large enough for the columns
        while len(self.template["ordering"]) < plate["cols"]:
            self.template["ordering"].append([])

        for col_num in range(plate["cols"]):
            row_count_offset = 0
            for row_num in range(plate["rows"]):
                # Generate standard A1, B1, ... style well names
                row_name_high_count = (self._row_count + row_count_offset) // 26
                row_name_high = chr(ord("A") + row_name_high_count - 1) if row_name_high_count else ""
                row_name = row_name_high + chr(ord("A") + (self._row_count + row_count_offset) % 26)
                row_count_offset += 1

                well_name = f"{row_name}{col_num + 1}"
                well = {
                    "depth": plate["well_depth"],
                    "totalLiquidVolume": plate["volume"],
                    "shape": plate["well_shape"],
                    "x": plate["x_offset"] + col_num * plate["x_spacing"],
                    "y": plate["yDimension"] - plate["y_offset"] - row_num * plate["y_spacing"],
                    "z": plate["zDimension"] - plate["well_depth"]
                }
                if plate["well_shape"] == "circular":
                    well["diameter"] = plate["well_diameter"]
                elif plate["well_shape"] == "rectangular":
                    well["xDimension"] = plate["well_x"]
                    well["yDimension"] = plate["well_y"]
                else:
                    raise ValueError(f"Unsupported well shape: {plate['well_shape']}")

                self.template["wells"][well_name] = well
                self.template["ordering"][col_num].append(well_name)
                if "wells" not in self.template["groups"][0]:
                    self.template["groups"][0]["wells"] = []
                self.template["groups"][0]["wells"].append(well_name)

        if plate.get("bottom_shape") in ["flat", "u", "v"]:
            self.template["groups"][0]["metadata"]["wellBottomShape"] = plate["bottom_shape"]
        self._row_count += row_count_offset

    def generate_definition(self) -> Dict:
        """
        Generates the final labware definition dictionary.

        This method processes all plate sections defined in the design and
        returns the complete, Opentrons-compatible labware definition.

        Returns:
            Dict: The final labware definition.
        """
        for plate in self.design["plates"]:
            self._generate_wells_for_plate(plate)
        return self.template


def _find_labware_definitions(tree: ast.Module) -> Optional[List[Dict]]:
    """
    Safely finds and parses the LABWARE_DEFINITIONS list from a parsed file.

    Args:
        tree (ast.Module): The abstract syntax tree of the definitions file.

    Returns:
        Optional[List[Dict]]: The parsed list of labware definitions, or None.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'LABWARE_DEFINITIONS':
                    try:
                        return ast.literal_eval(node.value)
                    except (ValueError, SyntaxError) as e:
                        raise ValueError(f"Could not parse 'LABWARE_DEFINITIONS'. Ensure it is a valid Python list. Error: {e}")
    return None


if __name__ == "__main__":
    # Get the project root directory
    project_root = Path(__file__).parent.parent.parent
    
    # --- Load Labware Definitions Safely ---
    definitions_file = project_root / "user_scripts" / "labware_definitions.py"
    if not definitions_file.is_file():
        raise FileNotFoundError(f"Labware definitions file not found at: {definitions_file}")

    file_content = definitions_file.read_text()
    
    try:
        parsed_tree = ast.parse(file_content, filename=str(definitions_file))
        LABWARE_DEFINITIONS = _find_labware_definitions(parsed_tree)
        if LABWARE_DEFINITIONS is None:
            raise ValueError("Could not find 'LABWARE_DEFINITIONS' variable in the definitions file.")
    except Exception as e:
        print(f"Error processing labware definitions file: {e}")
        exit(1)

    output_dir = project_root / "user_scripts" / "generated_labware"
    output_dir.mkdir(exist_ok=True)
    print(f"Generating labware definitions in: {output_dir}")

    if not LABWARE_DEFINITIONS:
        print("No labware definitions found.")

    for definition in LABWARE_DEFINITIONS:
        load_name = definition.get("load_name")
        if not load_name:
            print(f"--> Skipping entry with missing 'load_name': {definition.get('display_name', 'N/A')}")
            continue

        print(f"--> Processing: {load_name}")

        try:
            generator = LabwareGenerator(definition)
            generator.create_plate()
            generator.metadata()
            generator.parameters()
            labware_definition = generator.generate_definition()

            file_path = output_dir / f"{load_name}.json"
            with open(file_path, "w") as f:
                json.dump(labware_definition, f, indent=4)
            print(f"    `-> Saved to: {file_path.name}")

        except Exception as e:
            print(f"    `-> ERROR: Could not generate labware for '{load_name}'. Reason: {e}")

    print("Done.")
        