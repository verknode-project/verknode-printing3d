# verknode-printing3d

3D printing for [verknode](https://github.com/verknode-project/verknode): read meshes and CAD files, model and
fix parts, slice them, and work with G-code, with 3D viewers and editors on the canvas.

![The example workflow: a block with a hole made of shapes and a boolean, placed by hand in the Mesh Editor; sliced for the printer, its toolpaths layer by layer, print time and filament; a DXF drawing extruded and an STL knob combined on the build plate in the Mesh Viewer](docs/hero.png)

It is also verknode's example of a **package**: a set of nodes, a port type, canvas widgets, views and
node defaults, distributed by itself and installed into verknode from its package manager (see verknode's
[docs/packages.md](https://github.com/verknode-project/verknode/blob/main/docs/packages.md)).

## Install

In verknode: **≡ Settings › Packages › 3D Printing › Install**. The nodes are there at once, without a
restart. Or:

    python -m jupyterlab_verknode.packages install printing3d

## Nodes

| | Nodes |
|---|---|
| **IO** | Mesh Reader (STL, OBJ, PLY, OFF, 3MF, GLB / glTF, STEP, IGES, BREP), Mesh Writer, DXF Reader (2D outlines), G-code Reader, G-code Writer |
| **Modeling** | Shape (box, cylinder, sphere, cone, torus, capsule, tube), Extrude Outline (DXF to solid), OpenSCAD (code CAD, with the `openscad` program), Mesh Transform, Mesh Boolean, Mesh Repair, Mesh Combine, Mesh Info |
| **Printing** | Slicer (walls, solid top / bottom, infill, skirt, retraction), G-code Stats (time, filament, layers) |
| **Views** | Mesh Viewer, Mesh Editor (place, rotate and scale by hand on the build plate), G-code Viewer (layer by layer) |

- **The `mesh` port** (orange squares) carries triangle meshes in millimetres. Its port view is a 3D
  viewer.
- **G-code is a table of moves**, one row per move: position, extrusion, feed rate, layer, feature. You
  can filter it, chart it and join it like any table.
- **The printer** is set up once, in ≡ Settings › Node defaults › 3D printer: build volume, nozzle,
  filament and temperatures. The slicer and the viewers use it.
- **The built-in slicer** is for simple parts and for showing the workflow. For production prints, slice
  with PrusaSlicer, Cura or OrcaSlicer, and read their G-code with G-code Reader.

The example workflow puts it together: a part made of shapes, a boolean, the editor, the slicer, the
statistics and the viewers. To open it: **≡ Settings › Packages › 3D Printing › Examples**. It is copied,
with its data, into `verknode-examples/printing3d/`, so you can change it.

## Versions

- **Node API:** the package works with verknode's node API `>=1.1,<2` (`[tool.verknode] api`). Version
  0.1.0 needed only 1.0, so a verknode with node API 1.0 is offered 0.1.0 from the index.
- **Compatibility, every week:** the Compatibility workflow checks the package against the newest
  verknode. When verknode's API gets a new major version and the checks pass, it opens a pull request
  that widens the range.
- **Releases:** a tag `vX.Y.Z` builds the wheel onto a GitHub release, and verknode's package index adds
  that version by itself. Installed copies are then offered the update, or update themselves if the user
  chose automatic updates.
- **Saved workflows:** a node whose settings change gets a higher `version` and a `migrate()`, so saved
  workflows update themselves when they are opened.

## Develop

    pip install -e ".[test]"        # next to a verknode (its Python on the path)
    pytest
    python -m jupyterlab_verknode.packages validate .

## Changes

See [CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0. three.js (MIT) is in `src/verknode_printing3d/static/three`.
