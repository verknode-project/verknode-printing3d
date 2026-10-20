"""3D views and editors on the canvas: a mesh viewer, a mesh editor (place, rotate and scale the part on the
build plate, by hand) and a G-code viewer (the toolpaths, layer by layer)."""
from __future__ import annotations

import numpy as np

from verknode.node import F, TABLE, InvalidSettingsError, OutputWidget, Port

from ..gcode import segments
from ..mesh import MESH, MeshPortObject, editor_page, gcode_page, mesh_page, require_mesh

CAT = "3D Printing/Views"


class MeshViewer(OutputWidget):
    factory = "printing3d.MeshViewer"
    name = "Mesh Viewer"
    category = CAT
    icon = "◈"
    renderer = "html"
    width, height = 380, 300
    short_description = "Shows a mesh in 3D on the canvas, on the printer's build plate (drag to turn, wheel to zoom)."
    in_ports = [Port(MESH, "Mesh")]
    widget_fields = [F("color", "string", "Colour", "#e07b00"), F("plate", "bool", "Show the build plate", True)]

    def render(self, obj, s, ctx):
        mesh = require_mesh(obj)
        if mesh.empty():
            return {"html": "<p style='font: 12px sans-serif; color: #888'>No mesh yet</p>"}
        return {"html": mesh_page(mesh, color=s.get("color") or "#e07b00", plate=bool(s.get("plate", True)))}


def apply_placement(mesh: MeshPortObject, p: dict | None) -> MeshPortObject:
    """The mesh moved, rotated (degrees, Euler XYZ as three.js has them, about its centre) and scaled as placed in the editor."""
    if not p or mesh.empty():
        return mesh
    import trimesh

    m = mesh.trimesh()
    c = m.bounds.mean(axis=0)
    s = np.asarray(p.get("scale") or [1, 1, 1], dtype=float)
    S = np.diag([*s, 1.0])
    rx, ry, rz = np.radians(np.asarray(p.get("rotation") or [0, 0, 0], dtype=float))
    R = trimesh.transformations.euler_matrix(rx, ry, rz, "rxyz")  # (three.js Euler "XYZ": Rx Ry Rz)
    T0 = trimesh.transformations.translation_matrix(-c)
    T1 = trimesh.transformations.translation_matrix(np.asarray(p.get("position") or c, dtype=float))
    m.apply_transform(T1 @ R @ S @ T0)
    lo = m.bounds[0]
    if p.get("drop", True) and lo[2] != 0:
        m.apply_translation([0, 0, -lo[2]])
    return MeshPortObject.from_trimesh(m, mesh.name)


class MeshEditor(OutputWidget):
    factory = "printing3d.MeshEditor"
    name = "Mesh Editor"
    category = CAT
    icon = "✥"
    renderer = "html"
    interactive = True
    width, height = 420, 340
    short_description = "Places a part on the build plate by hand: move, rotate and scale it in 3D; out: the placed mesh."
    description = (
        "An editor on the canvas: drag the arrows to move the part, switch to rotate (R) or scale (S); 'Lay flat' "
        "and 'Center' help. The placement is kept in the workflow, and the second port gives the placed mesh, "
        "standing on the plate. Clear the placement to start over."
    )
    in_ports = [Port(MESH, "Mesh")]
    out_ports = [Port(MESH, "Mesh", "The input, unchanged"), Port(MESH, "Placed mesh", "As placed in the editor")]
    widget_fields = [F("color", "string", "Colour", "#2f8fdd")]

    def render(self, obj, s, ctx):
        mesh = require_mesh(obj)
        if mesh.empty():
            return {"html": "<p style='font: 12px sans-serif; color: #888'>No mesh yet</p>"}
        return {"html": editor_page(mesh, color=s.get("color") or "#2f8fdd")}

    def select(self, obj, s, selection):
        return apply_placement(require_mesh(obj), selection)


class GCodeViewer(OutputWidget):
    factory = "printing3d.GCodeViewer"
    name = "G-code Viewer"
    category = CAT
    icon = "≋"
    renderer = "html"
    width, height = 420, 340
    short_description = "Shows the toolpaths of G-code moves in 3D, layer by layer, coloured by feature."
    in_ports = [Port(TABLE, "Moves", "From G-code Reader or Slicer")]
    widget_fields = [F("travel", "bool", "Show travel moves", False)]

    def render(self, obj, s, ctx):
        if obj is None or not {"x", "y", "z", "kind", "layer"} <= set(obj.columns):
            raise InvalidSettingsError("Connect a moves table (G-code Reader, Slicer)")
        if obj.height < 2:
            return {"html": "<p style='font: 12px sans-serif; color: #888'>No moves yet</p>"}
        return {"html": gcode_page(segments(obj), travel=bool(s.get("travel")))}
