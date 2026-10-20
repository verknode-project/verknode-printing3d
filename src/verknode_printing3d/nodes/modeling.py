"""Modelling: shapes, extruded outlines, OpenSCAD, transforms, booleans, repair, combining and measuring meshes."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import numpy as np
import polars as pl

from verknode.node import F, TABLE, InvalidSettingsError, NodeModel, Port

from ..mesh import MESH, MeshPortObject, require_mesh
from ..profile import printer

CAT = "3D Printing/Modeling"
SHAPES = ["Box", "Cylinder", "Sphere", "Cone", "Torus", "Capsule", "Tube"]


def place_on_bed(m, center: bool = True):
    """The mesh standing on the build plate (z = 0), centred on it."""
    lo, hi = m.bounds
    p = printer()
    shift = np.array([0.0, 0.0, -lo[2]])
    if center:
        shift[0] = p["bed_x"] / 2 - (lo[0] + hi[0]) / 2
        shift[1] = p["bed_y"] / 2 - (lo[1] + hi[1]) / 2
    m.apply_translation(shift)
    return m


class Shape(NodeModel):
    factory = "printing3d.Shape"
    name = "Shape"
    category = CAT
    node_type = "Source"
    icon = "▣"
    short_description = "A box, cylinder, sphere, cone, torus, capsule or tube, standing on the build plate."
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("shape", "enum", "Shape", "Box", options=SHAPES),
        F("x", "float", "Width X (mm)", 20.0, show_if={"shape": "Box"}),
        F("y", "float", "Depth Y (mm)", 20.0, show_if={"shape": "Box"}),
        F("z", "float", "Height Z (mm)", 20.0, show_if={"shape": ["Box", "Cylinder", "Cone", "Capsule", "Tube"]}),
        F("radius", "float", "Radius (mm)", 10.0, show_if={"shape": ["Cylinder", "Sphere", "Cone", "Torus", "Capsule", "Tube"]}),
        F("inner", "float", "Inner radius / tube radius (mm)", 5.0, "Tube: the hole's radius; torus: the ring's thickness.",
          show_if={"shape": ["Torus", "Tube"]}),
        F("segments", "int", "Segments around", 64),
        F("on_bed", "bool", "Stand it on the middle of the build plate", True),
        F("name", "string", "Name", ""),
    ]

    def execute(self, inputs, s, ctx):
        import trimesh

        k = s.get("shape") or "Box"
        n = max(8, int(s.get("segments") or 64))
        r, h, inner = float(s.get("radius") or 10), float(s.get("z") or 20), float(s.get("inner") or 5)
        if k == "Box":
            m = trimesh.creation.box(extents=[float(s.get("x") or 20), float(s.get("y") or 20), h])
        elif k == "Cylinder":
            m = trimesh.creation.cylinder(radius=r, height=h, sections=n)
        elif k == "Sphere":
            m = trimesh.creation.uv_sphere(radius=r, count=[n, n])
        elif k == "Cone":
            m = trimesh.creation.cone(radius=r, height=h, sections=n)
        elif k == "Torus":
            m = trimesh.creation.torus(major_radius=r, minor_radius=inner, major_sections=n, minor_sections=max(8, n // 2))
        elif k == "Capsule":
            m = trimesh.creation.capsule(height=max(0.0, h - 2 * r), radius=r, count=[n, n])
        else:
            if inner >= r:
                raise InvalidSettingsError("The tube's inner radius must be less than its radius")
            m = trimesh.creation.annulus(r_min=inner, r_max=r, height=h, sections=n)
        if s.get("on_bed", True):
            place_on_bed(m)
        return [MeshPortObject.from_trimesh(m, (s.get("name") or "").strip() or k.lower())]


class ExtrudeOutline(NodeModel):
    factory = "printing3d.ExtrudeOutline"
    name = "Extrude Outline"
    category = CAT
    icon = "⇡"
    short_description = "Extrudes the closed outlines of a drawing (DXF Reader) into a solid: holes stay holes."
    description = (
        "Closed outlines (x, y point lists) become polygons; outlines inside others are holes (even-odd), "
        "and the result is extruded to the height."
    )
    in_ports = [Port(TABLE, "Outlines", "x and y (point lists) and closed per outline, as DXF Reader gives them")]
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("height", "float", "Height (mm)", 5.0),
        F("only_closed", "bool", "Only closed outlines", True),
        F("on_bed", "bool", "Stand it on the middle of the build plate", True),
        F("name", "string", "Name", "extrusion"),
    ]

    def configure(self, in_specs, s, ctx):
        spec = in_specs[0]
        if spec is not None and not {"x", "y"} <= set(spec):
            raise InvalidSettingsError("The outlines need x and y (point list) columns")
        return [MeshPortObject(name=s.get("name") or "extrusion")]

    def execute(self, inputs, s, ctx):
        if ctx.dry:
            return self.configure([inputs[0].schema], s, ctx)
        import trimesh
        from shapely.geometry import Polygon

        df = inputs[0]
        if s.get("only_closed", True) and "closed" in df.columns:
            df = df.filter(pl.col("closed"))
        polys = [Polygon(list(zip(x, y))) for x, y in zip(df["x"].to_list(), df["y"].to_list()) if x and len(x) >= 3]
        polys = [p.buffer(0) for p in polys if p.area > 1e-9]
        if not polys:
            raise InvalidSettingsError("No closed outline to extrude")
        shape = polys[0]
        for p in polys[1:]:  # (even-odd: an outline inside another is a hole)
            shape = shape.symmetric_difference(p)
        meshes = [trimesh.creation.extrude_polygon(g, float(s.get("height") or 5))
                  for g in getattr(shape, "geoms", [shape]) if g.geom_type == "Polygon" and g.area > 1e-9]
        m = trimesh.util.concatenate(meshes)
        if s.get("on_bed", True):
            place_on_bed(m)
        return [MeshPortObject.from_trimesh(m, s.get("name") or "extrusion")]


class OpenSCAD(NodeModel):
    factory = "printing3d.OpenSCAD"
    name = "OpenSCAD"
    category = CAT
    node_type = "Source"
    icon = "SCAD"
    short_description = "A model written in OpenSCAD code, rendered by the openscad program into a mesh."
    description = (
        "Code CAD: the dialog is the editor of the OpenSCAD script; running renders it with the openscad program "
        "(https://openscad.org, installed on the Jupyter server's machine) into a mesh. Flow variables can be put "
        "into the code as $${Sname}$$ (customizer-style parameters)."
    )
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("code", "code", "OpenSCAD code", "difference() {\n  cube([30, 30, 10], center = true);\n"
          "  cylinder(h = 12, r = 8, center = true, $fn = 64);\n}\n", insertables=True),
        F("program", "string", "openscad program", "", "Empty: openscad on the PATH."),
        F("timeout", "int", "Timeout (s)", 120),
        F("on_bed", "bool", "Stand it on the middle of the build plate", True),
        F("name", "string", "Name", "openscad"),
    ]

    def _program(self, s) -> str:
        prog = (s.get("program") or "").strip() or shutil.which("openscad") or shutil.which("openscad-nightly")
        if not prog:
            raise InvalidSettingsError("The openscad program is not installed (https://openscad.org/downloads.html), "
                                       "or set its path")
        return prog

    def configure(self, in_specs, s, ctx):
        self._program(s)
        return [MeshPortObject(name=s.get("name") or "openscad")]

    def execute(self, inputs, s, ctx):
        prog = self._program(s)
        if ctx.dry:
            return [MeshPortObject(name=s.get("name") or "openscad")]
        import trimesh
        from verknode.node import substitute_variables

        code = substitute_variables(s.get("code") or "", ctx.variables())
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "model.scad"), os.path.join(d, "model.stl")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(code)
            r = subprocess.run([prog, "-o", out, src], capture_output=True, text=True, timeout=int(s.get("timeout") or 120))
            if r.returncode != 0 or not os.path.isfile(out):
                raise InvalidSettingsError("OpenSCAD: " + (r.stderr.strip().splitlines() or ["failed"])[-1])
            m = trimesh.load(out, file_type="stl")
        if s.get("on_bed", True):
            place_on_bed(m)
        return [MeshPortObject.from_trimesh(m, s.get("name") or "openscad")]


class MeshTransform(NodeModel):
    factory = "printing3d.MeshTransform"
    name = "Mesh Transform"
    category = CAT
    icon = "⤧"
    short_description = "Moves, rotates, scales or mirrors a mesh; puts it on the build plate, or fits it into a size."
    in_ports = [Port(MESH, "Mesh")]
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("rx", "float", "Rotate about X (°)", 0.0), F("ry", "float", "Rotate about Y (°)", 0.0),
        F("rz", "float", "Rotate about Z (°)", 0.0),
        F("scale", "float", "Scale", 1.0, "1: as it is; 0.5: half; 2: double."),
        F("fit", "float", "Fit into (mm)", 0.0, "The largest side becomes this long (0: no fitting)."),
        F("mirror", "enum", "Mirror", "None", options=["None", "X", "Y", "Z"]),
        F("tx", "float", "Move X (mm)", 0.0), F("ty", "float", "Move Y (mm)", 0.0), F("tz", "float", "Move Z (mm)", 0.0),
        F("place", "enum", "Then", "On the build plate, centred", options=["On the build plate, centred",
                                                                         "On the build plate", "Keep it where it is"]),
    ]

    def execute(self, inputs, s, ctx):
        mesh = require_mesh(inputs[0])
        if ctx.dry or mesh.empty():
            return [mesh.spec() if ctx.dry else mesh]
        import trimesh

        m = mesh.trimesh()
        c = m.bounds.mean(axis=0)
        for axis, key in ((0, "rx"), (1, "ry"), (2, "rz")):
            a = float(s.get(key) or 0)
            if a:
                vec = [0, 0, 0]
                vec[axis] = 1
                m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(a), vec, c))
        k = float(s.get("scale") or 1.0)
        if float(s.get("fit") or 0) > 0:
            k = float(s["fit"]) / max(float(np.max(m.extents)), 1e-9)
        if k != 1.0:
            m.apply_scale(k)
        if s.get("mirror") in ("X", "Y", "Z"):
            M = np.eye(4)
            i = "XYZ".index(s["mirror"])
            M[i, i] = -1
            m.apply_transform(M)
            m.invert()  # (keep the normals outward)
        m.apply_translation([float(s.get("tx") or 0), float(s.get("ty") or 0), float(s.get("tz") or 0)])
        place = s.get("place") or "On the build plate, centred"
        if place != "Keep it where it is":
            place_on_bed(m, center=place.endswith("centred"))
        return [MeshPortObject.from_trimesh(m, mesh.name)]


class MeshBoolean(NodeModel):
    factory = "printing3d.MeshBoolean"
    name = "Mesh Boolean"
    category = CAT
    icon = "∪"
    short_description = "Union, difference or intersection of two solids (manifold3d)."
    in_ports = [Port(MESH, "Mesh A"), Port(MESH, "Mesh B")]
    out_ports = [Port(MESH, "Result")]
    requires = ["manifold3d"]
    fields = [F("operation", "enum", "Operation", "Difference (A - B)",
                options=["Union (A + B)", "Difference (A - B)", "Intersection (A and B)"])]

    def execute(self, inputs, s, ctx):
        a, b = require_mesh(inputs[0], "first input"), require_mesh(inputs[1], "second input")
        if ctx.dry:
            return [a.spec()]
        import trimesh

        op = (s.get("operation") or "Difference").split()[0].lower()
        fn = {"union": trimesh.boolean.union, "difference": trimesh.boolean.difference,
              "intersection": trimesh.boolean.intersection}[op]
        m = fn([a.trimesh(process=True), b.trimesh(process=True)], engine="manifold")
        return [MeshPortObject.from_trimesh(m, f"{a.name} {op} {b.name}")]


class MeshRepair(NodeModel):
    factory = "printing3d.MeshRepair"
    name = "Mesh Repair"
    category = CAT
    icon = "✚"
    short_description = "Merges duplicate vertices, removes broken faces, fixes normals and fills holes."
    in_ports = [Port(MESH, "Mesh")]
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("merge", "bool", "Merge duplicate vertices", True),
        F("degenerate", "bool", "Remove degenerate and duplicate faces", True),
        F("normals", "bool", "Fix normals (outward, consistent winding)", True),
        F("holes", "bool", "Fill holes", True),
        F("largest", "bool", "Keep only the largest body", False),
    ]

    def execute(self, inputs, s, ctx):
        mesh = require_mesh(inputs[0])
        if ctx.dry or mesh.empty():
            return [mesh.spec() if ctx.dry else mesh]
        import trimesh

        m = mesh.trimesh()
        before = (len(m.faces), m.is_watertight)
        if s.get("merge", True):
            m.merge_vertices()
        if s.get("degenerate", True):
            m.update_faces(m.nondegenerate_faces())
            m.update_faces(m.unique_faces())
            m.remove_unreferenced_vertices()
        if s.get("normals", True):
            trimesh.repair.fix_normals(m)
        if s.get("holes", True):
            trimesh.repair.fill_holes(m)
        if s.get("largest"):
            parts = m.split(only_watertight=False)
            if len(parts) > 1:
                m = max(parts, key=lambda p: len(p.faces))
        ctx.log(f"{before[0]} -> {len(m.faces)} triangles; watertight: {before[1]} -> {m.is_watertight}")
        return [MeshPortObject.from_trimesh(m, mesh.name)]


class MeshCombine(NodeModel):
    factory = "printing3d.MeshCombine"
    name = "Mesh Combine"
    category = CAT
    icon = "⊞"
    short_description = "Puts meshes together: into one mesh as they are, or side by side on the build plate."
    in_ports = [Port(MESH, "Mesh 1"), Port(MESH, "Mesh 2"), Port(MESH, "Mesh 3", optional=True),
                Port(MESH, "Mesh 4", optional=True)]
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("arrange", "enum", "Placement", "Side by side on the build plate", options=["Side by side on the build plate",
                                                                                    "As they are"]),
        F("gap", "float", "Gap (mm)", 5.0, show_if={"arrange": "Side by side on the build plate"}),
        F("name", "string", "Name", "combined"),
    ]

    def execute(self, inputs, s, ctx):
        meshes = [m for m in inputs if isinstance(m, MeshPortObject)]
        if len(meshes) < 2:
            raise InvalidSettingsError("Connect at least two meshes")
        if ctx.dry:
            return [MeshPortObject(name=s.get("name") or "combined")]
        import trimesh

        ms = [m.trimesh() for m in meshes if not m.empty()]
        if (s.get("arrange") or "").startswith("Side"):
            x, gap = 0.0, float(s.get("gap") or 5)
            for m in ms:
                lo, hi = m.bounds
                m.apply_translation([x - lo[0], -(lo[1] + hi[1]) / 2, -lo[2]])
                x += (hi[0] - lo[0]) + gap
            out = trimesh.util.concatenate(ms)
            place_on_bed(out)
        else:
            out = trimesh.util.concatenate(ms)
        return [MeshPortObject.from_trimesh(out, s.get("name") or "combined")]


INFO = {"name": pl.String, "triangles": pl.Int64, "vertices": pl.Int64, "bodies": pl.Int64, "size_x": pl.Float64,
        "size_y": pl.Float64, "size_z": pl.Float64, "volume_cm3": pl.Float64, "area_cm2": pl.Float64,
        "watertight": pl.Boolean, "consistent_winding": pl.Boolean, "solid_weight_g": pl.Float64,
        "fits_printer": pl.Boolean}


class MeshInfo(NodeModel):
    factory = "printing3d.MeshInfo"
    name = "Mesh Info"
    category = CAT
    icon = "ⓘ"
    short_description = "Measures a mesh: triangles, bodies, size, volume, surface, watertight, weight, fits the printer."
    in_ports = [Port(MESH, "Mesh")]
    out_ports = [Port(TABLE, "Info")]
    fields = []

    def configure(self, in_specs, s, ctx):
        return [pl.Schema(INFO)]

    def execute(self, inputs, s, ctx):
        mesh = require_mesh(inputs[0])
        if ctx.dry:
            return [pl.DataFrame(schema=INFO)]
        m = mesh.trimesh(process=True)
        ext = m.extents if len(m.faces) else np.zeros(3)
        p = printer()
        vol = abs(float(m.volume)) / 1000 if m.is_watertight else None
        row = {"name": mesh.name, "triangles": len(m.faces), "vertices": len(m.vertices),
               "bodies": len(m.split(only_watertight=False)) if len(m.faces) else 0,
               "size_x": round(float(ext[0]), 3), "size_y": round(float(ext[1]), 3), "size_z": round(float(ext[2]), 3),
               "volume_cm3": round(vol, 4) if vol is not None else None, "area_cm2": round(float(m.area) / 100, 3),
               "watertight": bool(m.is_watertight), "consistent_winding": bool(m.is_winding_consistent),
               "solid_weight_g": round(vol * p["filament_density"], 2) if vol is not None else None,
               "fits_printer": bool(ext[0] <= p["bed_x"] and ext[1] <= p["bed_y"] and ext[2] <= p["bed_z"])}
        return [pl.DataFrame([row], schema=INFO)]
