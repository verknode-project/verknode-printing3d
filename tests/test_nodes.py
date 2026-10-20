"""The nodes, run as verknode runs them (without a server): shapes to G-code and back, files, views."""
import os

import numpy as np
import polars as pl
import pytest

from verknode.node import ExecContext

from verknode_printing3d.mesh import MeshPortObject
from verknode_printing3d.nodes import io, modeling, printing, views


def run(cls, inputs=(), **settings):
    node = cls()
    s = {**node.default_settings(), **settings}
    return node.execute(list(inputs), s, ExecContext({}, os.getcwd()))


@pytest.fixture()
def tmp_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_shapes_are_solids_on_the_plate():
    for shape in modeling.SHAPES:
        mesh = run(modeling.Shape, shape=shape)[0]
        assert isinstance(mesh, MeshPortObject) and len(mesh.faces) > 0, shape
        lo, hi = mesh.bounds()
        assert abs(lo[2]) < 1e-6, shape
        assert mesh.trimesh(process=True).is_watertight, shape


def test_box_volume_and_info():
    box = run(modeling.Shape, shape="Box", x=10, y=20, z=30)[0]
    info = run(modeling.MeshInfo, [box])[0].row(0, named=True)
    assert info["volume_cm3"] == pytest.approx(6.0, rel=1e-6)
    assert (info["size_x"], info["size_y"], info["size_z"]) == (10, 20, 30)
    assert info["watertight"] and info["fits_printer"]


def test_boolean_transform_repair():
    box = run(modeling.Shape, shape="Box", x=30, y=30, z=10)[0]
    hole = run(modeling.Shape, shape="Cylinder", radius=5, z=20)[0]
    plate = run(modeling.MeshBoolean, [box, hole], operation="Difference (A - B)")[0]
    vol = abs(plate.trimesh(process=True).volume)
    assert vol == pytest.approx(30 * 30 * 10 - np.pi * 25 * 10, rel=0.01)
    turned = run(modeling.MeshTransform, [plate], rx=90, scale=2)[0]
    ext = turned.trimesh().extents
    assert sorted(np.round(ext)) == [20, 60, 60]
    fixed = run(modeling.MeshRepair, [turned])[0]
    assert fixed.trimesh(process=True).is_watertight


def test_slice_write_read_stats(tmp_cwd):
    box = run(modeling.Shape, shape="Box", x=20, y=20, z=2)[0]
    moves = run(printing.Slicer, [box], layer_height=0.2, infill=20)[0]
    assert moves.height > 100
    assert set(moves["feature"].unique().to_list()) >= {"wall-outer", "wall-inner", "skin", "skirt"}
    assert moves["layer"].max() == 10
    run(io.GCodeWriter, [moves], path="print.gcode")
    back = run(io.GCodeReader, path="print.gcode")[0]
    assert back.filter(pl.col("kind") == "extrude").height == moves.filter(pl.col("kind") == "extrude").height
    summary, layers = run(printing.GCodeStats, [back])
    s = summary.row(0, named=True)
    assert s["layers"] == 10 and s["filament_g"] > 0 and s["time_s"] > 0
    assert layers.height == 10
    # the filament is about the part's volume (solid part: walls and skin)
    assert s["filament_g"] == pytest.approx(20 * 20 * 2 / 1000 * 1.24, rel=0.35)


@pytest.mark.parametrize("ext", ["stl", "obj", "ply", "3mf"])
def test_mesh_files_round_trip(tmp_cwd, ext):
    torus = run(modeling.Shape, shape="Torus", radius=15, inner=4)[0]
    run(io.MeshWriter, [torus], path=f"out/t.{ext}")
    back = run(io.MeshReader, path=f"out/t.{ext}")[0]
    assert abs(back.trimesh(process=True).volume) == pytest.approx(abs(torus.trimesh(process=True).volume), rel=1e-3)


def test_dxf_outlines_extruded(tmp_cwd):
    import ezdxf

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
    msp.add_circle((10, 10), 4)
    msp.add_line((50, 0), (60, 0))
    doc.saveas("part.dxf")
    outlines = run(io.DXFReader, path="part.dxf")[0]
    assert outlines.height == 3 and outlines["closed"].sum() == 2
    mesh = run(modeling.ExtrudeOutline, [outlines], height=5)[0]
    vol = abs(mesh.trimesh(process=True).volume)
    assert vol == pytest.approx((40 * 20 - np.pi * 16) * 5, rel=0.01)


def test_views_and_editor():
    sphere = run(modeling.Shape, shape="Sphere", radius=10)[0]
    node = views.MeshViewer()
    html = node.render(sphere, node.default_settings(), None)["html"]
    assert "meshView" in html and "verknode/packages/printing3d/three/three.module.min.js" in html
    placed = views.apply_placement(sphere, {"position": [50, 60, 10], "rotation": [0, 0, 45], "scale": [2, 2, 2]})
    lo, hi = placed.bounds()
    assert abs(lo[2]) < 1e-6 and (hi - lo)[2] == pytest.approx(40, rel=1e-3)
    assert ((lo + hi) / 2)[:2] == pytest.approx([50, 60], abs=1e-6)
    box = run(modeling.Shape, shape="Box", z=1)[0]
    moves = run(printing.Slicer, [box])[0]
    g = views.GCodeViewer()
    assert "gcodeView" in g.render(moves, g.default_settings(), None)["html"]
    assert "meshView" in (sphere.view_html() or "")


def test_package_loads_in_verknode():
    from jupyterlab_verknode.engine import packages

    import verknode_printing3d

    assert packages.api_compatible(verknode_printing3d.package.api)
    assert verknode_printing3d.package.name == "printing3d"


def test_slice_a_placed_boolean_part():
    """A part as the example makes it: a boolean, placed in the editor, sliced (larger meshes: rtree)."""
    block = run(modeling.Shape, shape="Box", x=40, y=40, z=12)[0]
    hole = run(modeling.Shape, shape="Cylinder", radius=8, z=30)[0]
    part = run(modeling.MeshBoolean, [block, hole])[0]
    placed = views.apply_placement(part, {"position": [110, 110, 6], "rotation": [0, 0, 30], "scale": [1, 1, 1]})
    moves = run(printing.Slicer, [placed])[0]
    assert moves["layer"].max() == 60
    walls = moves.filter(pl.col("feature") == "wall-outer")
    # the hole: outer walls near the centre (within the hole's radius + a line)
    r = ((walls["x"] - 110) ** 2 + (walls["y"] - 110) ** 2).sqrt()
    assert ((r > 7) & (r < 8.6)).any()
