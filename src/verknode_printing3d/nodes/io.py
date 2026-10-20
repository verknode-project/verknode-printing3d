"""Reading and writing: meshes (STL, OBJ, PLY, OFF, 3MF, GLB / glTF), CAD (STEP, IGES, BREP; DXF outlines) and
G-code."""
from __future__ import annotations

import math
import os

import polars as pl

from verknode.node import F, TABLE, InvalidSettingsError, NodeModel, Port

from ..gcode import MOVES, parse, stats, to_gcode
from ..mesh import MESH, MeshPortObject, require_mesh
from ..profile import printer
from ..slicer import END_GCODE, start_gcode

CAT = "3D Printing/IO"
UNITS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4}
MESH_FORMATS = ["Auto", "STL", "OBJ", "PLY", "OFF", "3MF", "GLB", "GLTF", "STEP", "IGES", "BREP"]
CAD = {"step", "stp", "iges", "igs", "brep", "brp"}


def _kind(path: str, fmt: str) -> str:
    if fmt and fmt != "Auto":
        return fmt.lower()
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return {"stp": "step", "igs": "iges", "brp": "brep", "gltf": "gltf"}.get(ext, ext)


def load_mesh(path: str, kind: str, merge: bool = True):
    import trimesh

    if kind in ("step", "iges", "brep"):
        return _load_cad(path, kind)
    loaded = trimesh.load(path, file_type=kind, force=None, process=True)
    if isinstance(loaded, trimesh.Scene):
        geoms = [g for g in loaded.dump() if isinstance(g, trimesh.Trimesh)] if hasattr(loaded, "dump") else \
            list(loaded.geometry.values())
        if not geoms:
            raise InvalidSettingsError("The file has no triangle meshes")
        return trimesh.util.concatenate(geoms) if merge else geoms[0]
    if not isinstance(loaded, trimesh.Trimesh):
        raise InvalidSettingsError(f"Not a triangle mesh: {type(loaded).__name__}")
    return loaded


def _load_cad(path: str, kind: str):
    """STEP / IGES / BREP: tessellated by OpenCascade (cascadio) into GLB, then read."""
    import tempfile

    import cascadio
    import trimesh

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "model.glb")
        fn = {"step": "step_to_glb", "iges": "iges_to_glb", "brep": "brep_to_glb"}.get(kind)
        convert = getattr(cascadio, fn, None)
        if convert is None:
            raise InvalidSettingsError(f"This cascadio cannot read {kind.upper()} files")
        convert(path, out, 0.1, 0.5)
        scene = trimesh.load(out, file_type="glb", force="scene")
        geoms = list(scene.dump()) if hasattr(scene, "dump") else list(scene.geometry.values())
        if not geoms:
            raise InvalidSettingsError(f"No shapes in the {kind.upper()} file")
        return trimesh.util.concatenate(geoms)


class MeshReader(NodeModel):
    factory = "printing3d.MeshReader"
    name = "Mesh Reader"
    category = CAT
    node_type = "Source"
    icon = "STL"
    short_description = "Reads a 3D model: STL, OBJ, PLY, OFF, 3MF, GLB / glTF, and CAD files (STEP, IGES, BREP)."
    description = (
        "Reads a mesh file into a mesh port (millimetres): STL (binary or ASCII), OBJ, PLY, OFF, 3MF, GLB and "
        "glTF with trimesh; STEP, IGES and BREP CAD models are tessellated with OpenCascade (cascadio). Files "
        "with several bodies are merged into one mesh (or the first is taken). Other units are scaled to mm."
    )
    out_ports = [Port(MESH, "Mesh")]
    fields = [
        F("path", "string", "File", "", "An STL, OBJ, PLY, OFF, 3MF, GLB, glTF, STEP, IGES or BREP file.", path=True),
        F("format", "enum", "Format", "Auto", "Auto: from the file extension.", options=MESH_FORMATS),
        F("units", "enum", "Units of the file", "mm", options=list(UNITS)),
        F("merge", "bool", "Merge all bodies into one mesh", True),
    ]

    def configure(self, in_specs, s, ctx):
        if not (s.get("path") or "").strip():
            raise InvalidSettingsError("Choose a file")
        return [MeshPortObject(name=os.path.splitext(os.path.basename(s["path"]))[0])]

    def execute(self, inputs, s, ctx):
        if ctx.dry:
            return self.configure([], s, ctx)
        path = ctx.resolve_path(s["path"])
        if not os.path.isfile(path):
            raise InvalidSettingsError(f"No such file: {s['path']}")
        m = load_mesh(path, _kind(path, s.get("format")), bool(s.get("merge", True)))
        scale = UNITS.get(s.get("units") or "mm", 1.0)
        if scale != 1.0:
            m.apply_scale(scale)
        mesh = MeshPortObject.from_trimesh(m, os.path.splitext(os.path.basename(path))[0])
        ctx.log(f"{mesh.name}: {len(mesh.faces)} triangles")
        return [mesh]


class MeshWriter(NodeModel):
    factory = "printing3d.MeshWriter"
    name = "Mesh Writer"
    category = CAT
    node_type = "Sink"
    icon = "STL"
    short_description = "Writes a mesh as STL, OBJ, PLY, 3MF or GLB."
    in_ports = [Port(MESH, "Mesh")]
    out_ports = []
    fields = [
        F("path", "string", "File", "output/model.stl", "The format comes from the extension: .stl, .obj, .ply, "
          ".3mf, .glb, .off.", path=True),
        F("ascii", "bool", "STL as text (ASCII)", False),
        F("if_exists", "enum", "If the file exists", "Overwrite", options=["Overwrite", "Fail"]),
    ]

    def configure(self, in_specs, s, ctx):
        ext = os.path.splitext(s.get("path") or "")[1].lower()
        if ext not in (".stl", ".obj", ".ply", ".3mf", ".glb", ".off"):
            raise InvalidSettingsError("Name a .stl, .obj, .ply, .3mf, .glb or .off file")
        return []

    def execute(self, inputs, s, ctx):
        mesh = require_mesh(inputs[0])
        if ctx.dry:
            return []
        path = ctx.resolve_path(s["path"])
        if os.path.exists(path) and s.get("if_exists") == "Fail":
            raise InvalidSettingsError(f"The file exists: {s['path']}")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        m = mesh.trimesh()
        if ext == "stl" and s.get("ascii"):
            data = m.export(file_type="stl_ascii")
        else:
            data = m.export(file_type=ext)
        with open(path, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        ctx.log(f"Wrote {len(mesh.faces)} triangles to {s['path']}")
        return []


OUTLINES = {"id": pl.Int64, "entity": pl.String, "layer": pl.String, "closed": pl.Boolean,
            "x": pl.List(pl.Float64), "y": pl.List(pl.Float64)}


class DXFReader(NodeModel):
    factory = "printing3d.DXFReader"
    name = "DXF Reader"
    category = CAT
    node_type = "Source"
    icon = "DXF"
    short_description = "Reads the outlines of a 2D CAD drawing (DXF): polylines, lines, circles, arcs, splines."
    description = (
        "One row per outline of the DXF drawing (ezdxf): LWPOLYLINE, POLYLINE, LINE, CIRCLE, ARC, ELLIPSE and SPLINE "
        "entities as point lists (x, y in mm), their layer and whether they are closed. Extrude Outline turns the "
        "closed ones into a mesh."
    )
    out_ports = [Port(TABLE, "Outlines")]
    fields = [
        F("path", "string", "File", "", "A .dxf file.", path=True),
        F("layers", "string", "Layers", "", "Comma separated; empty: all."),
        F("segments", "int", "Segments per circle", 64),
        F("units", "enum", "Units of the drawing", "mm", options=list(UNITS)),
    ]

    def configure(self, in_specs, s, ctx):
        return [pl.Schema(OUTLINES)]

    def execute(self, inputs, s, ctx):
        if ctx.dry:
            return [pl.DataFrame(schema=OUTLINES)]
        import ezdxf

        path = ctx.resolve_path(s.get("path") or "")
        if not os.path.isfile(path):
            raise InvalidSettingsError(f"No such file: {s.get('path')}")
        doc = ezdxf.readfile(path)
        want = {x.strip() for x in (s.get("layers") or "").split(",") if x.strip()}
        k = UNITS.get(s.get("units") or "mm", 1.0)
        n = max(8, int(s.get("segments") or 64))
        rows = {c: [] for c in OUTLINES}
        for i, e in enumerate(doc.modelspace()):
            t = e.dxftype()
            if want and e.dxf.layer not in want:
                continue
            pts, closed = None, False
            if t == "LWPOLYLINE":
                pts, closed = [(p[0], p[1]) for p in e.get_points("xy")], bool(e.closed)
            elif t == "POLYLINE":
                pts, closed = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices], bool(e.is_closed)
            elif t == "LINE":
                pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
            elif t == "CIRCLE":
                c, r = e.dxf.center, e.dxf.radius
                pts = [(c.x + r * math.cos(2 * math.pi * j / n), c.y + r * math.sin(2 * math.pi * j / n)) for j in range(n)]
                closed = True
            elif t in ("ARC", "ELLIPSE", "SPLINE"):
                pts = [(p.x, p.y) for p in e.flattening(0.05)]
                closed = t != "ARC" and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6
            if not pts:
                continue
            for col, v in (("id", i), ("entity", t), ("layer", e.dxf.layer), ("closed", closed),
                           ("x", [p[0] * k for p in pts]), ("y", [p[1] * k for p in pts])):
                rows[col].append(v)
        df = pl.DataFrame(rows, schema=OUTLINES)
        ctx.log(f"{df.height} outlines ({int(df['closed'].sum() or 0)} closed)")
        return [df]


class GCodeReader(NodeModel):
    factory = "printing3d.GCodeReader"
    name = "G-code Reader"
    category = CAT
    node_type = "Source"
    icon = "G"
    short_description = "Reads G-code into a table of moves: position, extrusion, feed rate, layer, feature."
    description = (
        "Reads the moves of a G-code file (any slicer's: PrusaSlicer, Cura, OrcaSlicer, Simplify3D...): where the "
        "head is after each G0/G1/G2/G3, the filament it extrudes, the feed rate, the layer and the feature (from "
        ";TYPE: comments). Absolute and relative positioning (G90/G91, M82/M83) and G92 resets are followed. "
        "Flow variables: gcode_time_s, gcode_filament_g, gcode_layers."
    )
    out_ports = [Port(TABLE, "Moves")]
    fields = [F("path", "string", "File", "", "A .gcode / .gco / .g file.", path=True)]

    def configure(self, in_specs, s, ctx):
        return [pl.Schema(MOVES)]

    def execute(self, inputs, s, ctx):
        if ctx.dry:
            return [pl.DataFrame(schema=MOVES)]
        path = ctx.resolve_path(s.get("path") or "")
        if not os.path.isfile(path):
            raise InvalidSettingsError(f"No such file: {s.get('path')}")
        with open(path, encoding="utf-8", errors="replace") as fh:
            moves = parse(fh.read())
        p = printer()
        summary, _ = stats(moves, p["filament_diameter"], p["filament_density"])
        ctx.push_variable("gcode_time_s", float(summary.get("time_s", 0.0)), "DOUBLE")
        ctx.push_variable("gcode_filament_g", float(summary.get("filament_g", 0.0)), "DOUBLE")
        ctx.push_variable("gcode_layers", int(summary.get("layers", 0)), "INTEGER")
        ctx.log(f"{moves.height} moves, {summary.get('layers', 0)} layers, {summary.get('time', '')}")
        return [moves]


class GCodeWriter(NodeModel):
    factory = "printing3d.GCodeWriter"
    name = "G-code Writer"
    category = CAT
    node_type = "Sink"
    icon = "G"
    short_description = "Writes a table of moves (Slicer, G-code Reader) as G-code, with start and end G-code."
    in_ports = [Port(TABLE, "Moves", "x, y, z, extruded, f, kind (and feature) per move")]
    out_ports = []
    fields = [
        F("path", "string", "File", "output/print.gcode", path=True),
        F("start", "code", "Start G-code", "", "Empty: heat up to the printer's temperatures (Settings › Node "
          "defaults › 3D printer), home, and wait."),
        F("end", "code", "End G-code", END_GCODE),
        F("relative_e", "bool", "Relative extrusion (M83)", False),
    ]

    def configure(self, in_specs, s, ctx):
        spec = in_specs[0] if in_specs else None
        need = {"x", "y", "z", "extruded", "f", "kind"}
        if spec is not None and not need <= set(spec):
            raise InvalidSettingsError(f"The moves table needs the columns {', '.join(sorted(need))}")
        return []

    def execute(self, inputs, s, ctx):
        if ctx.dry:
            return []
        path = ctx.resolve_path(s["path"])
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        text = to_gcode(inputs[0], (s.get("start") or "").strip() or start_gcode(printer()), s.get("end") or "",
                        bool(s.get("relative_e")))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        ctx.log(f"Wrote {text.count(chr(10))} lines to {s['path']}")
        return []

