# =============================================================================
# LABWARE DEFINITIONS
# =============================================================================
# Add all labware definition dictionaries to this list.
# The `labware_generator.py` script will iterate through this list 
# and generate a JSON file for each entry, entries that already exist
# will be skipped.

# The JSON file will be saved in the `generated_labware` directory.
# 
# A `load_name` is required and will be used as the filename.
# =============================================================================

LABWARE_DEFINITIONS = [
    {
        # This is an example of a tip rack.
        "display_name": "Matterlab 20ul Tip Rack",
        "display_category": "tipRack",
        "load_name": "matterlab_tip_rack_20",
        "tags": ["tiprack", "matterlab"],
        "plates": [{
            "rows": 8, "cols": 12, "well_depth": 35, "volume": 20,
            "well_shape": "circular", "well_diameter": 4, "bottom_shape": "v",
            "xDimension": 127.0, "yDimension": 85.5, "zDimension": 50.0,
            "x_offset": 10, "y_offset": 10, "x_spacing": 9, "y_spacing": 9
        }]
    },
    # --- Add your other labware definitions below ---
    #
    # {
    #   "display_name": "My Custom 96-Well Plate",
    #   "display_category": "wellPlate",
    #   "load_name": "my_custom_96_wellplate",
    #   "tags": ["wellplate", "custom"],
    #   "plates": [...]
    # },
] 