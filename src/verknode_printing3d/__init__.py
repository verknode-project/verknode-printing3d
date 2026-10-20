"""verknode-printing3d: 3D printing for verknode - meshes (STL, OBJ, PLY, 3MF, GLB, STEP, IGES, BREP), 2D CAD
(DXF), modelling (shapes, extrusions, transforms, booleans, repair), a slicer, G-code (read, write, stats)
and 3D viewers and editors on the canvas. A verknode package: see verknode's docs/packages.md."""
from importlib.metadata import PackageNotFoundError, version as _version

from verknode.package import Package

try:
    __version__ = _version("verknode-printing3d")
except PackageNotFoundError:  # (from a source folder)
    __version__ = "0.0.0"

#: the versions of verknode's node API this package works with (also [tool.verknode] api in pyproject.toml)
API = ">=1.1,<2"  # (1.1: examples)

package = Package(
    name="printing3d",
    title="3D Printing",
    version=__version__,
    api=API,
    description="Meshes and CAD files (STL, OBJ, PLY, 3MF, GLB, STEP, IGES, DXF), modelling, a slicer, G-code and "
                "3D viewers and editors.",
    modules=["verknode_printing3d.nodes.io", "verknode_printing3d.nodes.modeling",
             "verknode_printing3d.nodes.printing", "verknode_printing3d.nodes.views"],
    static="static",
    examples="examples",
    homepage="https://github.com/verknode-project/verknode-printing3d",
)

from . import profile  # noqa: E402,F401 - the printer's node defaults
