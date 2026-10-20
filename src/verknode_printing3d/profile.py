"""The printer: set up once for all workflows (Settings › Node defaults › 3D printer) - the build volume
the viewers draw and the slicer checks against, the nozzle, the filament and the default temperatures."""
from verknode.node import F, node_defaults, register_defaults

FIELDS = [
    F("name", "string", "Printer", "Generic FDM printer"),
    F("bed_x", "float", "Build volume X (mm)", 220.0),
    F("bed_y", "float", "Build volume Y (mm)", 220.0),
    F("bed_z", "float", "Build volume Z (mm)", 250.0),
    F("nozzle", "float", "Nozzle diameter (mm)", 0.4),
    F("filament_diameter", "float", "Filament diameter (mm)", 1.75),
    F("filament_density", "float", "Filament density (g/cm³)", 1.24, "PLA 1.24, PETG 1.27, ABS 1.04."),
    F("nozzle_temperature", "int", "Nozzle temperature (°C)", 205),
    F("bed_temperature", "int", "Bed temperature (°C)", 60),
    F("print_speed", "float", "Print speed (mm/s)", 50.0),
    F("travel_speed", "float", "Travel speed (mm/s)", 150.0),
]
register_defaults("printing3d", "3D printer", FIELDS,
                  description="The printer the 3D printing nodes slice for and the viewers show (verknode-printing3d).",
                  order=60)


def printer() -> dict:
    return node_defaults("printing3d")
