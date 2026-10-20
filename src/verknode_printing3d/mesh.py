"""Meshes on verknode ports: the "mesh" port type (orange squares) and its port object, and the pages of the
3D viewers (three.js, from the package's static files)."""
from __future__ import annotations

import base64
import json

import numpy as np

from verknode.node import InvalidSettingsError
from verknode.package import PortObject, register_port_type

MESH = register_port_type("mesh", "3D mesh", "#e07b00", "A triangle mesh (vertices and faces), in millimetres")


class MeshPortObject(PortObject):
    """A triangle mesh: vertices (n x 3, float, mm) and faces (m x 3, vertex indices)."""

    port_type = MESH

    def __init__(self, vertices=None, faces=None, name: str = "mesh", units: str = "mm"):
        self.vertices = np.asarray(vertices if vertices is not None else np.zeros((0, 3)), dtype=np.float64).reshape(-1, 3)
        self.faces = np.asarray(faces if faces is not None else np.zeros((0, 3)), dtype=np.int64).reshape(-1, 3)
        self.name = name
        self.units = units

    # -- trimesh ------------------------------------------------------------------------------------
    @classmethod
    def from_trimesh(cls, m, name: str = "mesh") -> "MeshPortObject":
        return cls(np.asarray(m.vertices), np.asarray(m.faces), name)

    def trimesh(self, process: bool = False):
        import trimesh

        return trimesh.Trimesh(self.vertices.copy(), self.faces.copy(), process=process)

    # -- verknode -------------------------------------------------------------------------------------
    def spec(self):
        return MeshPortObject(name=self.name, units=self.units)

    def empty(self) -> bool:
        return len(self.faces) == 0

    def bounds(self):
        if not len(self.vertices):
            return np.zeros(3), np.zeros(3)
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def summary(self) -> dict:
        if self.empty():
            return {"Mesh": self.name, "Triangles": 0}
        lo, hi = self.bounds()
        size = hi - lo
        out = {"Mesh": self.name, "Triangles": int(len(self.faces)), "Vertices": int(len(self.vertices)),
               "Size (mm)": " x ".join(f"{v:.2f}" for v in size)}
        try:
            m = self.trimesh()
            out["Watertight"] = bool(m.is_watertight)
            if m.is_watertight:
                out["Volume (cm³)"] = round(float(abs(m.volume)) / 1000, 3)
            out["Surface (cm²)"] = round(float(m.area) / 100, 2)
        except Exception:  # noqa: BLE001 - the counts still show
            pass
        return out

    def view_html(self) -> str | None:
        return mesh_page(self, height="100%") if not self.empty() else None


def require_mesh(obj, what: str = "input") -> MeshPortObject:
    if not isinstance(obj, MeshPortObject):
        raise InvalidSettingsError(f"Connect a mesh to the {what}")
    return obj


# ---------------------------------------------------------------------------------------------------
# viewer pages (sandboxed frames: the scripts come from the package's static files)
# ---------------------------------------------------------------------------------------------------
MAX_VIEW_FACES = 400_000


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def mesh_data(mesh: MeshPortObject) -> dict:
    faces = mesh.faces[:MAX_VIEW_FACES]
    return {"positions": _b64(mesh.vertices.astype(np.float32)), "indices": _b64(faces.astype(np.uint32)),
            "name": mesh.name, "truncated": len(mesh.faces) > MAX_VIEW_FACES}


def _page(script: str, data: dict, options: dict, height: str) -> str:
    from . import package

    base = package.static_url("")
    payload = json.dumps(data).replace("</", "<\\/")
    opts = json.dumps(options).replace("</", "<\\/")
    return f"""<style>html, body {{ margin: 0; height: {height}; overflow: hidden; background: #f6f7f9;
font: 12px system-ui, sans-serif; }} #v {{ position: absolute; inset: 0; }}</style>
<script type="importmap">{{"imports": {{"three": "{base}three/three.module.min.js",
"three/addons/": "{base}three/addons/"}}}}</script>
<div id="v"></div>
<script type="module">
import {{ {script} }} from "{base}viewer.js";
{script}(document.getElementById("v"), {payload}, {opts});
</script>"""


def bed_options() -> dict:
    from .profile import printer

    p = printer()
    return {"bed": [p["bed_x"], p["bed_y"], p["bed_z"]]}


def mesh_page(mesh: MeshPortObject, height: str = "100%", **options) -> str:
    return _page("meshView", mesh_data(mesh), {**bed_options(), **options}, height)


def editor_page(mesh: MeshPortObject, height: str = "100%", **options) -> str:
    return _page("meshEditor", mesh_data(mesh), {**bed_options(), **options}, height)


def gcode_page(segments: dict, height: str = "100%", **options) -> str:
    return _page("gcodeView", segments, {**bed_options(), **options}, height)
