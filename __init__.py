"""MoldForge - generate printable silicone mold systems from a mesh.

This is an original implementation built on Blender's public Python API
(bmesh, modifiers, depsgraph). It does not contain or derive from any
third-party mold tool's code.
"""

import bpy
from bpy.props import PointerProperty

from .properties import MoldForgeProperties
from .operators import MOLDFORGE_OT_generate, MOLDFORGE_OT_export
from .panel import MOLDFORGE_PT_main

_classes = (
    MoldForgeProperties,
    MOLDFORGE_OT_generate,
    MOLDFORGE_OT_export,
    MOLDFORGE_PT_main,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.moldforge = PointerProperty(type=MoldForgeProperties)


def unregister():
    if hasattr(bpy.types.Scene, "moldforge"):
        del bpy.types.Scene.moldforge
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
