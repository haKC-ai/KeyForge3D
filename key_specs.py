"""
key_specs.py

Key blank specification database.
Each entry defines the physical parameters needed to correctly
decode bitting from an image and generate an accurate STL.

depth_increment_mm: mm per bitting depth step
depths: valid bitting values (shallowest to deepest)
num_cuts: number of bitting positions on the blade
tip_to_first_cut_mm: distance from tip to center of first cut
cut_spacing_mm: center-to-center spacing between cuts
blade_width_mm: blade height at bow shoulder
"""

KEY_SPECS: dict[str, dict] = {
    "KW1": {
        "brand": "Kwikset",
        "num_cuts": 5,
        "depths": [1, 2, 3, 4, 5, 6, 7],
        "depth_increment_mm": 0.333,
        "tip_to_first_cut_mm": 6.86,
        "cut_spacing_mm": 3.81,
        "blade_width_mm": 6.9,
        "key_thickness_mm": 2.0,
    },
    "KW10": {
        "brand": "Kwikset",
        "num_cuts": 6,
        "depths": [1, 2, 3, 4, 5, 6, 7],
        "depth_increment_mm": 0.333,
        "tip_to_first_cut_mm": 6.86,
        "cut_spacing_mm": 3.81,
        "blade_width_mm": 6.9,
        "key_thickness_mm": 2.0,
    },
    "SC1": {
        "brand": "Schlage",
        "num_cuts": 6,
        "depths": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "depth_increment_mm": 0.375,
        "tip_to_first_cut_mm": 7.62,
        "cut_spacing_mm": 3.96,
        "blade_width_mm": 7.5,
        "key_thickness_mm": 2.3,
    },
    "SC4": {
        "brand": "Schlage",
        "num_cuts": 6,
        "depths": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "depth_increment_mm": 0.375,
        "tip_to_first_cut_mm": 7.62,
        "cut_spacing_mm": 3.96,
        "blade_width_mm": 7.5,
        "key_thickness_mm": 2.3,
    },
    "Y1": {
        "brand": "Yale",
        "num_cuts": 5,
        "depths": [1, 2, 3, 4, 5],
        "depth_increment_mm": 0.381,
        "tip_to_first_cut_mm": 6.35,
        "cut_spacing_mm": 3.81,
        "blade_width_mm": 7.0,
        "key_thickness_mm": 2.0,
    },
    "M1": {
        "brand": "Master Lock",
        "num_cuts": 4,
        "depths": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "depth_increment_mm": 0.381,
        "tip_to_first_cut_mm": 5.08,
        "cut_spacing_mm": 5.08,
        "blade_width_mm": 7.5,
        "key_thickness_mm": 2.3,
    },
    "WR3": {
        "brand": "Weiser",
        "num_cuts": 5,
        "depths": [1, 2, 3, 4, 5, 6, 7],
        "depth_increment_mm": 0.333,
        "tip_to_first_cut_mm": 6.86,
        "cut_spacing_mm": 3.81,
        "blade_width_mm": 7.0,
        "key_thickness_mm": 2.0,
    },
    "CO87": {
        "brand": "Corbin Russwin",
        "num_cuts": 6,
        "depths": [1, 2, 3, 4, 5, 6, 7, 8],
        "depth_increment_mm": 0.381,
        "tip_to_first_cut_mm": 8.26,
        "cut_spacing_mm": 3.96,
        "blade_width_mm": 8.0,
        "key_thickness_mm": 2.3,
    },
    "BEST": {
        "brand": "BEST / Arrow",
        "num_cuts": 6,
        "depths": [1, 2, 3, 4, 5, 6, 7],
        "depth_increment_mm": 0.381,
        "tip_to_first_cut_mm": 7.62,
        "cut_spacing_mm": 3.96,
        "blade_width_mm": 7.9,
        "key_thickness_mm": 2.3,
    },
    "UNKNOWN": {
        "brand": "Unknown",
        "num_cuts": 5,
        "depths": [1, 2, 3, 4, 5, 6, 7],
        "depth_increment_mm": 0.333,
        "tip_to_first_cut_mm": 6.86,
        "cut_spacing_mm": 3.81,
        "blade_width_mm": 7.0,
        "key_thickness_mm": 2.0,
    },
}

KNOWN_FORMATS = list(KEY_SPECS.keys())


def get_spec(key_format: str) -> dict:
    return KEY_SPECS.get(key_format.upper(), KEY_SPECS["UNKNOWN"])
