import json
from pathlib import Path
import logging
from typing import Union, Dict, List, Optional

filter_plate = {
    "display_name": "MatterLab_Filtration",
    "load_name":    "matterlab_filtration",
    "display_category": "wellPlate",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 60.0,
         "rows": 3, "cols": 4, "x_spacing": 28.0, "y_spacing": 28.0, "x_offset": 18.0, "y_offset": 18.0,
         "well_depth": 56.0, "well_diameter": 7.0, "volume": 20000.0, "well_shape": "circular", "bottom_shape": "flat"
         },
         {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 120.0,
         "rows": 3, "cols": 4, "x_spacing": 28.0, "y_spacing": 28.0, "x_offset": 31.0, "y_offset": 19.0,
         "well_depth": 43.0, "well_diameter": 16.0, "volume": 11000.0, "well_shape": "circular", "bottom_shape": "flat"
         }
    ]
}

tip_rack_200 = {
    "display_name": "MatterLab 96 Tips 200uL",
    "load_name":    "matterlab_96_tips_200ul",
    "display_category": "tipRack",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 65.0,
         "rows": 8, "cols": 12, "x_spacing": 9.0, "y_spacing": 9.0, "x_offset": 14.0, "y_offset": 11.0,
         "well_depth": 59.0, "well_diameter": 5.3, "volume": 200.0, "well_shape": "circular", "bottom_shape": None
         }
    ]
}

tip_rack_10 = {
    "display_name": "MatterLab 96 Tips 10uL",
    "load_name":    "matterlab_96_tips_10ul",
    "display_category": "tipRack",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 51.5,
         "rows": 8, "cols": 12, "x_spacing": 9.0, "y_spacing": 9.0, "x_offset": 14.2, "y_offset": 11.0,
         "well_depth": 31.9, "well_diameter": 3.3, "volume": 10.0, "well_shape": "circular", "bottom_shape": None
         }
    ]
}

tip_rack_20 = {
    "display_name": "MatterLab 96 Tips 20uL",
    "load_name":    "matterlab_96_tips_20ul",
    "display_category": "tipRack",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 51.5,
         "rows": 8, "cols": 12, "x_spacing": 9.0, "y_spacing": 9.0, "x_offset": 14.2, "y_offset": 11.0,
         "well_depth": 31.9, "well_diameter": 3.3, "volume": 20.0, "well_shape": "circular", "bottom_shape": None
         }
    ]
}

square_well = {
    "display_name": "Test 96 Well Plate 200 µL",
    "load_name":    "test_96_wellplate_200u",
    "display_category": "wellPlate",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 60.0,
         "rows": 8, "cols": 12, "x_spacing": 9.0, "y_spacing": 9.0, "x_offset": 14.0, "y_offset": 11.0,
         "well_depth": 59.0, "well_x": 8.0, "well_y": 8.0, "volume": 200.0, "well_shape": "rectangular", "bottom_shape": "v"
         }
    ]
}

vial_4ml = {
    "display_name": "MatterLab_24x4mL",
    "load_name":    "matterlab_24x4ml",
    "display_category": "wellPlate",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 48.50,
         "rows": 4, "cols": 6, "x_spacing": 20.0, "y_spacing": 20.0, "x_offset": 13.5, "y_offset": 12.5,
         "well_depth": 44.0, "well_diameter": 8.0, "volume": 4000.0, "well_shape": "circular", "bottom_shape": "flat"
        }
    ]
}

vial_20ml = {
    "display_name": "MatterLab 12 Vial Plate 20 mL",
    "load_name":    "matterlab_12_vialplate_20000_ul",
    "display_category": "wellPlate",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 60.50,
         "rows": 3, "cols": 4, "x_spacing": 28.5, "y_spacing": 28.5, "x_offset": 20.5, "y_offset": 14,
         "well_depth": 56.0, "well_diameter": 14.0, "volume": 20000.0, "well_shape": "circular", "bottom_shape": "flat"
        }
    ]
}

beaker_30ml = {
    "display_name": "MatterLab_beaker_40mL",
    "load_name":    "matterlab_beaker_40ml",
    "display_category": "wellPlate",
    "tags": [],
    "plates": [
        {"xDimension": 127.0, "yDimension": 85.0, "zDimension": 55.0,
         "rows": 1, "cols": 1, "x_spacing": 20.0, "y_spacing": 20.0, "x_offset": 63.5, "y_offset": 42.5,
         "well_depth": 50.0, "well_diameter": 33.0, "volume": 40000.0, "well_shape": "circular", "bottom_shape": "flat"
        }
    ]
}

class WellPlateGenerator(object):
    MAX_DIMENSIONS = {"x": 127, "y": 85.5, "z": 200}
    def __init__(self, well_plate_design: Dict):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel("INFO")
        self._load_template()
        self.grids = []
        self.design = well_plate_design
        self.cal_row_col_num()
        self._row_count = 0
        self._col_count = 0
        # self._group_count = 0

    def _load_template(self, template_path: Path = None):
        if template_path == None:
            template_path = Path(__file__).parent/"well_plate_template.json"
        if not template_path.exists():
            raise FileExistsError("Template not found!")
        with open(template_path, "r") as f:
            self.template = json.load(f)

    def create_plate(self):
        for dimen in ("x", "y", "z"):
            dimen_max = max([plate[f"{dimen}Dimension"] for plate in self.design["plates"]])
            if dimen_max > self.MAX_DIMENSIONS[dimen]:
                self.logger.warning(f"{dimen} dimension value {dimen_max} out of range: 0 - {self.MAX_DIMENSIONS[dimen]} mm")
            self.template["dimensions"][f"{dimen}Dimension"] = dimen_max
    
    def update_dimension(self, x: float = None, y: float = None, z: float= None):
        if x:
            if 0 < x <= self.MAX_DIMENSIONS["x"]:
                self.template["dimensions"]["xDimension"] = x
            else:
                self.logger.warning(f"x dimension value {x} out of range: 0 - {self.MAX_DIMENSIONS['x']} mm")
        if y:
            if 0 < y <= self.MAX_DIMENSIONS["y"]:
                self.template["dimensions"]["yDimension"] = y
            else:
                self.logger.warning(f"y dimension value {y} out of range: 0 - {self.MAX_DIMENSIONS['y']} mm")
        if x:
            if 0 < z <= self.MAX_DIMENSIONS["z"]:
                self.template["dimensions"]["xDimension"] = z
            else:
                self.logger.warning(f"z dimension value {z} out of range: 0 - {self.MAX_DIMENSIONS['z']} mm")

    def metadata(self, display_name: str = None, display_category: str = None, tags: List = None):
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
            # assuming there is only one type of tip, which SHOULD!
            self.template["parameters"]["tipLength"] = self.design["plates"][0]["well_depth"]
        else:
            self.template["parameters"]["isTiprack"] = False
        self.logger.info(f"parameters updated {self.template['parameters']}")

    def cal_row_col_num(self):
        self._total_rows = 0
        self._total_cols = 0
        for plate in self.design["plates"]:
            self._total_rows += plate["rows"]
            self._total_cols += plate["cols"]
    
    def single_plate(self, plate: Dict):            
        while (len(self.template["ordering"]) < plate["cols"]):
            self.template["ordering"].append([])
        for col_num in range(0, plate["cols"]):
            row_count_offset = 0
            for row_num in range(0, plate["rows"]):
                row_name_high_count = (self._row_count+row_count_offset) // 26
                if row_name_high_count:
                    row_name_high = chr(ord("A") + row_name_high_count -1)
                else:
                    row_name_high = ""
                row_name = row_name_high + chr(ord("A") + (self._row_count + row_count_offset) % 26)
                row_count_offset += 1
                well_name = f"{row_name}{col_num+1}"
                well = {
                    # "name": f"{row_name}{col_num+1}",
                    "depth": plate["well_depth"],
                    "totalLiquidVolume": plate["volume"],
                    "shape": plate["well_shape"],
                    # "diameter": plate["well_diameter"],
                    "x": plate["x_offset"] + col_num * plate["y_spacing"],
                    "y": plate["yDimension"] - plate["y_offset"] - row_num * plate["y_spacing"],
                    "z": plate["zDimension"] - plate["well_depth"]
                }
                if plate["well_shape"] == "circular":
                    well["diameter"] = plate["well_diameter"]
                elif plate["well_shape"] == "rectangular":
                    well["xDimension"] = plate["well_x"]
                    well["yDimension"] = plate["well_y"]
                else:
                    raise ValueError("Unsupported well shape")
                self.template["wells"][well_name] = well
                self.template["ordering"][col_num].append(well_name)
                self.template["groups"][0]["wells"].append(well_name)
        if plate["bottom_shape"] in ["flat", "u", "v"]:                 
            self.template["groups"][0]["metadata"]["wellBottomShape"] = plate["bottom_shape"]
        self._row_count += row_count_offset

    def multiple_plates(self):
        for plate in self.design["plates"]:
            self.single_plate(plate)
        return self.template
    
if __name__ == "__main__":
    test = WellPlateGenerator(tip_rack_20)
    test.create_plate()
    test.metadata()
    test.parameters()
    rtn = test.multiple_plates()
    with open(Path(r"c:\users\aag\Downloads")/"matterlab_96_tiprack_20ul.json", "w") as f:
        json.dump(rtn, f, indent=2)
        
                
