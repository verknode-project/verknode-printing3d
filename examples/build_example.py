"""Makes examples/printing3d.vkf (and its data: a DXF drawing and an STL part) with verknode's Python API.

    python examples/build_example.py        # verknode and this package importable
"""
import os

import ezdxf
import trimesh

from jupyterlab_verknode.standalone import Workflow

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
os.makedirs(DATA, exist_ok=True)

# a mounting plate drawn in 2D: an outline with four screw holes and a slot
doc = ezdxf.new()
msp = doc.modelspace()
msp.add_lwpolyline([(0, 0), (60, 0), (60, 40), (0, 40)], close=True)
for x, y in [(6, 6), (54, 6), (6, 34), (54, 34)]:
    msp.add_circle((x, y), 2.2)
msp.add_lwpolyline([(20, 17), (40, 17), (40, 23), (20, 23)], close=True)
doc.saveas(os.path.join(DATA, "plate.dxf"))
# an STL part: a knob (a cylinder with a sphere on top)

knob = trimesh.boolean.union([trimesh.creation.cylinder(radius=6, height=12, sections=48),
                              trimesh.creation.icosphere(subdivisions=3, radius=9).apply_translation([0, 0, 12])],
                             engine="manifold")
knob.export(os.path.join(DATA, "knob.stl"))

wf = Workflow(base_dir=HERE)
g = wf.engine
# 1. a part made of shapes: a block with a hole, placed by hand in the editor, measured
block = wf.add_node("Shape", "Block 40 x 40 x 12", at=(80, 120), shape="Box", x=40, y=40, z=12)
hole = wf.add_node("Shape", "Hole r 8", at=(80, 260), shape="Cylinder", radius=8, z=30)
cut = wf.add_node("Mesh Boolean", "Block minus hole", at=(260, 180), operation="Difference (A - B)")
wf.connect(block, cut, 1, 1)
wf.connect(hole, cut, 1, 2)
editor = wf.add_node("Mesh Editor", "Place it", at=(430, 180), title="Mesh Editor: drag, rotate (R), scale (S)",
                     selection={"position": [110, 110, 6], "rotation": [0, 0, 30], "scale": [1, 1, 1], "drop": True})
wf.connect(cut, editor)
info = wf.add_node("Mesh Info", "Measure", at=(620, 120))
wf.connect(editor, info, 2, 1)
info_view = wf.add_node("Table Display", "Mesh info", at=(780, 60), title="The placed part")
wf.connect(info, info_view)
stl = wf.add_node("Mesh Writer", "Save STL", at=(620, 260), path="output/block.stl")
wf.connect(editor, stl, 2, 1)
# 2. sliced: G-code, its statistics and toolpaths
slicer = wf.add_node("Slicer", "Slice (0.2 mm, 20 %)", at=(620, 420), layer_height=0.2, infill=20, center=False)
wf.connect(editor, slicer, 2, 1)
stats = wf.add_node("G-code Stats", "Print time, filament", at=(800, 420))
wf.connect(slicer, stats)
stats_view = wf.add_node("Table Display", "Print", at=(980, 360), title="Print time and filament")
wf.connect(stats, stats_view)
gview = wf.add_node("G-code Viewer", "Toolpaths", at=(800, 560), title="G-code: layer by layer")
wf.connect(slicer, gview)
gcode = wf.add_node("G-code Writer", "Save G-code", at=(980, 560), path="output/block.gcode")
wf.connect(slicer, gcode)
# 3. from files: a 2D drawing extruded, an STL part, combined on the plate
dxf = wf.add_node("DXF Reader", "Plate drawing", at=(80, 720), path="data/plate.dxf")
plate = wf.add_node("Extrude Outline", "Extrude 4 mm", at=(260, 720), height=4, name="plate")
wf.connect(dxf, plate)
knob_r = wf.add_node("Mesh Reader", "Knob (STL)", at=(80, 860), path="data/knob.stl")
both = wf.add_node("Mesh Combine", "On the plate", at=(430, 790), gap=8)
wf.connect(plate, both, 1, 1)
wf.connect(knob_r, both, 1, 2)
viewer = wf.add_node("Mesh Viewer", "3D view", at=(620, 790), title="Plate and knob, on the build plate")
wf.connect(both, viewer)
g.add_group([block.id, hole.id, cut.id, editor.id, info.id, info_view.id, stl.id],
            "A part from shapes: a boolean, placed by hand in the Mesh Editor, measured, saved as STL", "#e07b00")
g.add_group([slicer.id, stats.id, stats_view.id, gview.id, gcode.id],
            "Sliced for the printer of Settings › Node defaults › 3D printer: G-code, time, filament, toolpaths", "#3d9be9")
g.add_group([dxf.id, plate.id, knob_r.id, both.id, viewer.id],
            "From CAD files: a DXF drawing extruded, an STL part, combined on the plate", "#4caf50")
results = wf.run(check=False)
bad = [r for r in results if r.get("state") not in (None, "EXECUTED")]
print("ran", len(results), "nodes;", "problems:", bad[:3])
wf.save(os.path.join(HERE, "printing3d.vkf"))
print("saved", os.path.join(HERE, "printing3d.vkf"))
