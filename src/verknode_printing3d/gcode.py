"""G-code as a table of moves: read (with absolute / relative positioning and extrusion, G92 resets and the
slicers' ;TYPE: feature comments), written back, and measured (time, filament, layers)."""
from __future__ import annotations

import math
import re

import numpy as np
import polars as pl

MOVES = {"line": pl.Int64, "layer": pl.Int64, "kind": pl.String, "feature": pl.String, "x": pl.Float64,
         "y": pl.Float64, "z": pl.Float64, "e": pl.Float64, "f": pl.Float64, "extruded": pl.Float64}
WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def parse(text: str) -> pl.DataFrame:
    """The moves (G0 / G1 / G2 / G3 end points) of G-code, one row each: where the head is after the move,
    the extrusion of the move (mm of filament), its kind (travel, extrude, retract, unretract) and feature."""
    rows = {k: [] for k in MOVES}
    pos = {"x": 0.0, "y": 0.0, "z": 0.0, "e": 0.0}
    f = 0.0
    absolute, abs_e = True, True
    feature, layer, last_z = "", 0, None
    for n, raw in enumerate(text.splitlines(), start=1):
        code, _, comment = raw.partition(";")
        comment = comment.strip()
        if comment.upper().startswith("TYPE:"):
            feature = comment[5:].strip().lower()
        elif comment.upper().startswith("LAYER:") or comment.upper().startswith("LAYER_CHANGE"):
            pass
        code = code.strip()
        if not code:
            continue
        words = WORD.findall(code)
        if not words:
            continue
        letter, number = words[0][0].upper(), words[0][1]
        args = {k.upper(): float(v) for k, v in words[1:]}
        cmd = f"{letter}{int(float(number))}"
        if cmd == "G90":
            absolute, abs_e = True, True
        elif cmd == "G91":
            absolute, abs_e = False, False
        elif cmd == "M82":
            abs_e = True
        elif cmd == "M83":
            abs_e = False
        elif cmd == "G92":
            for k in ("X", "Y", "Z", "E"):
                if k in args:
                    pos[k.lower()] = args[k]
        elif cmd == "G28":
            for k in ("x", "y", "z"):
                if not args or k.upper() in args:
                    pos[k] = 0.0
        elif cmd in ("G0", "G1", "G2", "G3"):
            if "F" in args:
                f = args["F"]
            de = 0.0
            for k in ("X", "Y", "Z"):
                if k in args:
                    pos[k.lower()] = args[k] if absolute else pos[k.lower()] + args[k]
            if "E" in args:
                de = args["E"] - pos["e"] if abs_e else args["E"]
                pos["e"] = args["E"] if abs_e else pos["e"] + args["E"]
            moved = any(k in args for k in ("X", "Y", "Z"))
            kind = ("extrude" if de > 0 else "retract" if de < 0 else "travel") if moved else (
                "retract" if de < 0 else "unretract" if de > 0 else "travel")
            if kind == "extrude" and (last_z is None or pos["z"] > last_z + 1e-6):
                layer += 1
                last_z = pos["z"]
            for k, v in (("line", n), ("layer", layer), ("kind", kind), ("feature", feature if kind == "extrude" else ""),
                         ("x", pos["x"]), ("y", pos["y"]), ("z", pos["z"]), ("e", pos["e"]), ("f", f),
                         ("extruded", de)):
                rows[k].append(v)
    return pl.DataFrame(rows, schema=MOVES)


def to_gcode(moves: pl.DataFrame, start: str = "", end: str = "", relative_e: bool = False) -> str:
    """G-code of a moves table (absolute positions; E absolute unless relative_e)."""
    out = [start.rstrip("\n")] if start.strip() else []
    out.append("G90")
    out.append("M83" if relative_e else "M82")
    if not relative_e:
        out.append("G92 E0")
    last = {"x": None, "y": None, "z": None, "f": None}
    e_total = 0.0
    feature = None
    for r in moves.iter_rows(named=True):
        if r.get("feature") and r["feature"] != feature:
            feature = r["feature"]
            out.append(f";TYPE:{feature.upper()}")
        words = ["G0" if r["kind"] == "travel" else "G1"]
        for k in ("x", "y", "z"):
            v = r.get(k)
            if v is not None and (last[k] is None or abs(v - last[k]) > 1e-6):
                words.append(f"{k.upper()}{v:.3f}")
                last[k] = v
        de = r.get("extruded") or 0.0
        if de:
            e_total += de
            words.append(f"E{de:.5f}" if relative_e else f"E{e_total:.5f}")
        f = r.get("f")
        if f and f != last["f"]:
            words.append(f"F{f:.0f}")
            last["f"] = f
        if len(words) > 1:
            out.append(" ".join(words))
    if end.strip():
        out.append(end.rstrip("\n"))
    return "\n".join(out) + "\n"


def stats(moves: pl.DataFrame, filament_diameter: float = 1.75, density: float = 1.24) -> tuple[dict, pl.DataFrame]:
    """The print: time (moves at their feed rates, no acceleration), filament (length, weight), layers, size;
    and a table per layer."""
    if moves.height == 0:
        return {"moves": 0}, pl.DataFrame(schema={"layer": pl.Int64, "z": pl.Float64, "seconds": pl.Float64,
                                                    "filament_mm": pl.Float64})
    x, y, z = (moves[c].to_numpy() for c in ("x", "y", "z"))
    d = np.sqrt(np.diff(x, prepend=x[0]) ** 2 + np.diff(y, prepend=y[0]) ** 2 + np.diff(z, prepend=z[0]) ** 2)
    f = moves["f"].fill_null(0).to_numpy()
    seconds = np.where(f > 0, d / np.maximum(f, 1e-9) * 60.0, 0.0)
    ext = moves["extruded"].fill_null(0).to_numpy()
    filament = float(ext[ext > 0].sum())
    area = math.pi * (filament_diameter / 2) ** 2
    grams = filament * area / 1000 * density
    xe = moves.filter(pl.col("kind") == "extrude")
    per_layer = (moves.with_columns(pl.Series("seconds", seconds), pl.Series("pos", np.maximum(ext, 0)))
                 .filter(pl.col("layer") > 0)
                 .group_by("layer").agg(pl.col("z").max(), pl.col("seconds").sum(), pl.col("pos").sum().alias("filament_mm"))
                 .sort("layer"))
    summary = {
        "moves": moves.height, "layers": int(moves["layer"].max() or 0), "time_s": round(float(seconds.sum()), 1),
        "time": _hms(float(seconds.sum())), "filament_m": round(filament / 1000, 3), "filament_g": round(grams, 2),
        "size_x": round(float(xe["x"].max() - xe["x"].min()), 2) if xe.height else 0.0,
        "size_y": round(float(xe["y"].max() - xe["y"].min()), 2) if xe.height else 0.0,
        "height": round(float(xe["z"].max()), 2) if xe.height else 0.0,
    }
    return summary, per_layer


def _hms(s: float) -> str:
    h, rem = divmod(int(round(s)), 3600)
    return f"{h}h {rem // 60:02d}m {rem % 60:02d}s"


def segments(moves: pl.DataFrame, limit: int = 600_000) -> dict:
    """The viewer's data: line segments of the moves (from the previous position), their layer and feature."""
    import base64

    if moves.height < 2:
        return {"segments": "", "layers": "", "features": "", "names": [], "kinds": "", "maxLayer": 0}
    x, y, z = (moves[c].to_numpy().astype(np.float32) for c in ("x", "y", "z"))
    seg = np.stack([x[:-1], y[:-1], z[:-1], x[1:], y[1:], z[1:]], axis=1)[:limit]
    layers = moves["layer"].to_numpy()[1:][:limit].astype(np.uint16)
    kinds = moves["kind"].to_list()[1:][:limit]
    feats = moves["feature"].fill_null("").to_list()[1:][:limit]
    names = sorted({f for f in feats if f})
    fidx = np.array([names.index(f) + 1 if f else 0 for f in feats], dtype=np.uint8)
    kidx = np.array([1 if k == "extrude" else 0 for k in kinds], dtype=np.uint8)

    def b64(a):
        return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")

    return {"segments": b64(seg), "layers": b64(layers), "features": b64(fidx), "names": names, "kinds": b64(kidx),
            "maxLayer": int(layers.max()) if len(layers) else 0, "truncated": moves.height - 1 > limit}
