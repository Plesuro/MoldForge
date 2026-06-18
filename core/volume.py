"""Mesh volume in cubic scene units."""

import bpy
import bmesh


def mesh_volume(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    bm.from_object(obj, deps)
    try:
        return abs(bm.calc_volume(signed=False))
    finally:
        bm.free()
