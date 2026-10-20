"""A small slicer for FDM printers: layers from the mesh's sections, walls (insets of the outlines), solid
top and bottom layers, line infill at alternating angles, a skirt, retraction on travels - into the moves
table of gcode.py. It is meant for simple parts and for showing the workflow; for production prints, use a
full slicer (PrusaSlicer, Cura, OrcaSlicer) and read its G-code with G-code Reader."""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from .gcode import MOVES


def _layers(mesh, height: float, first: float):
    import shapely
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    lo, hi = mesh.bounds
    # (layer tops from their index - no sum of rounding errors; the top surface is printed)
    n = int(math.floor((hi[2] - lo[2] - first) / height + 1e-6)) + 1 if hi[2] - lo[2] >= first - 1e-9 else 0
    zs = [lo[2] + first + k * height for k in range(n)]
    out = []
    for z in zs:
        sec = mesh.section(plane_origin=[0, 0, z - height / 2 if out else z - first / 2], plane_normal=[0, 0, 1])
        if sec is None:
            out.append((z, Polygon()))
            continue
        to_2d = np.eye(4)
        to_2d[2, 3] = -sec.vertices[:, 2].mean() if len(sec.vertices) else 0
        flat = getattr(sec, "to_2D", None) or sec.to_planar  # (trimesh 5: to_2D)
        planar, _ = flat(to_2D=to_2d, check=False)
        polys = [p for p in planar.polygons_full if p is not None and p.area > 1e-6]
        out.append((z, shapely.make_valid(unary_union(polys)) if polys else Polygon()))
    return out


def _rings(geom):
    """Exterior and interior rings of a (multi)polygon, as point arrays."""
    if geom.is_empty:
        return []
    polys = getattr(geom, "geoms", [geom])
    out = []
    for p in polys:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        out.append(np.asarray(p.exterior.coords))
        out += [np.asarray(r.coords) for r in p.interiors]
    return out


def _hatch(region, spacing: float, angle: float):
    """Infill lines over a region (a zigzag of segments)."""
    from shapely.affinity import rotate
    from shapely.geometry import LineString, MultiLineString

    if region.is_empty or spacing <= 0:
        return []
    r = rotate(region, -angle, origin=(0, 0), use_radians=False)
    minx, miny, maxx, maxy = r.bounds
    lines = [LineString([(minx - 1, y), (maxx + 1, y)]) for y in np.arange(miny + spacing / 2, maxy, spacing)]
    if not lines:
        return []
    cut = MultiLineString(lines).intersection(r)
    segs = [g for g in getattr(cut, "geoms", [cut]) if g.geom_type == "LineString" and g.length > 1e-3]
    segs.sort(key=lambda g: (round(g.coords[0][1], 3), g.coords[0][0]))
    out = []
    for i, g in enumerate(segs):
        c = np.asarray(rotate(g, angle, origin=(0, 0), use_radians=False).coords)
        out.append(c[::-1] if i % 2 else c)
    return out


def slice_mesh(mesh, s: dict, printer: dict, log=lambda t: None) -> pl.DataFrame:
    """The moves table (gcode.MOVES) of printing the mesh."""
    h, first = float(s["layer_height"]), float(s["first_layer_height"])
    w = float(s.get("line_width") or printer["nozzle"] * 1.125)
    walls = int(s["walls"])
    infill = max(0.0, min(1.0, float(s["infill"]) / 100))
    solid_n = int(s["solid_layers"])
    speed = float(s.get("print_speed") or printer["print_speed"]) * 60
    first_speed = speed * 0.5
    travel = float(printer["travel_speed"]) * 60
    fil_area = math.pi * (float(printer["filament_diameter"]) / 2) ** 2
    retract = float(s["retraction"])
    layers = _layers(mesh, h, first)
    log(f"{len(layers)} layers")
    rows = {k: [] for k in MOVES}
    state = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0, "retracted": False}

    def add(kind, x, y, z, de, f, feature, layer):
        state["e"] += de
        for k, v in (("line", 0), ("layer", layer), ("kind", kind), ("feature", feature), ("x", x), ("y", y), ("z", z),
                     ("e", state["e"]), ("f", f), ("extruded", de)):
            rows[k].append(v)
        state.update(x=x, y=y, z=z)

    def travel_to(x, y, z, layer):
        far = math.hypot(x - state["x"], y - state["y"]) > 2.0
        if far and retract and not state["retracted"]:
            add("retract", state["x"], state["y"], state["z"], -retract, 2400, "", layer)
            state["retracted"] = True
        add("travel", x, y, z, 0.0, travel, "", layer)

    def extrude_path(pts, z, lh, f, feature, layer, closed=False):
        pts = np.asarray(pts)[:, :2]
        if len(pts) < 2:
            return
        travel_to(pts[0][0], pts[0][1], z, layer)
        if state["retracted"]:
            add("unretract", state["x"], state["y"], z, retract, 2400, "", layer)
            state["retracted"] = False
        per_mm = w * lh / fil_area
        for p in pts[1:]:
            d = math.hypot(p[0] - state["x"], p[1] - state["y"])
            if d > 1e-4:
                add("extrude", float(p[0]), float(p[1]), z, d * per_mm, f, feature, layer)

    regions = [g for _, g in layers]
    for i, (z, region) in enumerate(layers):
        layer = i + 1
        lh = first if i == 0 else h
        f = first_speed if i == 0 else speed
        if region.is_empty:
            continue
        if i == 0 and s.get("skirt", True):
            hull = region.convex_hull.buffer(3.0 + w, join_style=2)
            for ring in _rings(hull):
                extrude_path(ring, z, lh, f, "skirt", layer, closed=True)
        for k in range(walls):  # walls, outside in
            inset = region.buffer(-w * (k + 0.5), join_style=2)
            for ring in _rings(inset):
                extrude_path(ring, z, lh, f, "wall-outer" if k == 0 else "wall-inner", layer, closed=True)
        inner = region.buffer(-w * walls, join_style=2)
        if inner.is_empty:
            continue
        below = regions[i - solid_n] if i - solid_n >= 0 else None
        above = regions[i + solid_n] if i + solid_n < len(regions) else None
        if below is None or above is None:
            solid, sparse = inner, None
        else:
            solid = inner.difference(below.intersection(above).buffer(-w * walls))
            sparse = inner.difference(solid)
        angle = 45 if i % 2 == 0 else -45
        for seg in _hatch(solid, w, angle):
            extrude_path(seg, z, lh, f, "skin", layer)
        if sparse is not None and not sparse.is_empty and infill > 0:
            for seg in _hatch(sparse, w / infill, angle):
                extrude_path(seg, z, lh, f, "fill", layer)
    return pl.DataFrame(rows, schema=MOVES).with_columns(pl.int_range(1, pl.len() + 1, dtype=pl.Int64).alias("line"))


def start_gcode(printer: dict) -> str:
    return "\n".join([
        f"; {printer.get('name', 'printer')} - sliced by verknode-printing3d",
        f"M140 S{printer['bed_temperature']}", f"M104 S{printer['nozzle_temperature']}",
        f"M190 S{printer['bed_temperature']}", f"M109 S{printer['nozzle_temperature']}",
        "G28", "G92 E0", "G1 Z5 F3000",
    ])


END_GCODE = "\n".join(["G91", "G1 E-2 F2400", "G1 Z10 F3000", "G90", "M104 S0", "M140 S0", "M107", "M84"])
