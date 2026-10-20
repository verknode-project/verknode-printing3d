"""Printing: slicing a mesh into G-code moves, and measuring G-code."""
from __future__ import annotations

import polars as pl

from verknode.node import F, TABLE, InvalidSettingsError, NodeModel, Port

from ..gcode import MOVES, stats
from ..mesh import MESH, require_mesh
from ..profile import printer
from ..slicer import slice_mesh
from .modeling import place_on_bed

CAT = "3D Printing/Printing"
SUMMARY = {"moves": pl.Int64, "layers": pl.Int64, "time_s": pl.Float64, "time": pl.String, "filament_m": pl.Float64,
           "filament_g": pl.Float64, "size_x": pl.Float64, "size_y": pl.Float64, "height": pl.Float64}
LAYERS = {"layer": pl.Int64, "z": pl.Float64, "seconds": pl.Float64, "filament_mm": pl.Float64}


class Slicer(NodeModel):
    factory = "printing3d.Slicer"
    name = "Slicer"
    category = CAT
    icon = "≋"
    short_description = "Slices a mesh into the G-code moves of an FDM print: walls, solid top and bottom, infill, skirt."
    description = (
        "A small built-in slicer for the printer of Settings › Node defaults › 3D printer: layers from the mesh's "
        "sections; walls; solid layers where the part has no layers above or below; line infill at alternating "
        "angles; a skirt; retraction on travels. Out: the moves table (G-code Writer writes it; G-code Viewer "
        "shows it; G-code Stats measures it). For production prints use a full slicer and read its G-code."
    )
    in_ports = [Port(MESH, "Mesh")]
    out_ports = [Port(TABLE, "Moves")]
    requires = ["shapely"]
    fields = [
        F("layer_height", "float", "Layer height (mm)", 0.2),
        F("first_layer_height", "float", "First layer height (mm)", 0.2),
        F("line_width", "float", "Line width (mm)", 0.0, "0: 1.125 x the nozzle."),
        F("walls", "int", "Walls", 2),
        F("solid_layers", "int", "Solid top / bottom layers", 4),
        F("infill", "float", "Infill (%)", 20.0),
        F("retraction", "float", "Retraction (mm)", 0.8),
        F("print_speed", "float", "Print speed (mm/s)", 0.0, "0: the printer's."),
        F("skirt", "bool", "Skirt", True),
        F("center", "bool", "Put it on the middle of the build plate first", True),
    ]

    def configure(self, in_specs, s, ctx):
        if float(s.get("layer_height") or 0) <= 0:
            raise InvalidSettingsError("The layer height must be more than 0")
        return [pl.Schema(MOVES)]

    def execute(self, inputs, s, ctx):
        mesh = require_mesh(inputs[0])
        if ctx.dry:
            return [pl.DataFrame(schema=MOVES)]
        if mesh.empty():
            raise InvalidSettingsError("The mesh is empty")
        p = printer()
        m = mesh.trimesh(process=True)
        if s.get("center", True):
            place_on_bed(m)
        ext = m.extents
        if ext[0] > p["bed_x"] or ext[1] > p["bed_y"] or ext[2] > p["bed_z"]:
            ctx.warn(f"The part ({ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm) is bigger than the printer's build "
                     f"volume ({p['bed_x']:.0f} x {p['bed_y']:.0f} x {p['bed_z']:.0f} mm)")
        if not m.is_watertight:
            ctx.warn("The mesh is not watertight: the slices may have gaps (Mesh Repair helps)")
        moves = slice_mesh(m, s, p, ctx.log)
        summary, _ = stats(moves, p["filament_diameter"], p["filament_density"])
        ctx.push_variable("print_time_s", float(summary.get("time_s", 0.0)), "DOUBLE")
        ctx.push_variable("print_filament_g", float(summary.get("filament_g", 0.0)), "DOUBLE")
        ctx.log(f"{summary.get('layers')} layers, {summary.get('time')}, {summary.get('filament_g')} g")
        return [moves]


class GCodeStats(NodeModel):
    factory = "printing3d.GCodeStats"
    name = "G-code Stats"
    category = CAT
    icon = "⏱"
    short_description = "Print time, filament length and weight, layers and size of a print - and per layer."
    in_ports = [Port(TABLE, "Moves")]
    out_ports = [Port(TABLE, "Summary", "One row"), Port(TABLE, "Layers", "One row per layer")]
    fields = [
        F("diameter", "float", "Filament diameter (mm)", 0.0, "0: the printer's (Settings › Node defaults › 3D printer)."),
        F("density", "float", "Filament density (g/cm³)", 0.0, "0: the printer's."),
    ]

    def configure(self, in_specs, s, ctx):
        return [pl.Schema(SUMMARY), pl.Schema(LAYERS)]

    def execute(self, inputs, s, ctx):
        if ctx.dry:
            return [pl.DataFrame(schema=SUMMARY), pl.DataFrame(schema=LAYERS)]
        p = printer()
        summary, layers = stats(inputs[0], float(s.get("diameter") or p["filament_diameter"]),
                                float(s.get("density") or p["filament_density"]))
        row = {k: summary.get(k) for k in SUMMARY}
        return [pl.DataFrame([row], schema=SUMMARY), layers.cast(LAYERS)]
