"""Mesh preparation: heal, decimate, voxel remesh, and centering."""

import math

import bpy
import bmesh
from mathutils import Matrix

from . import constants as C
from . import util


def heal(obj):
    """Merge doubles, drop loose geometry, and make normals consistent/outward."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def ensure_outward_normals(obj):
    """Make face normals consistent and outward — Solidify grows along normals,
    so this guarantees the shell goes outward even when Heal is disabled."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def decimate(obj, ratio):
    mod = obj.modifiers.new("mf_decimate", 'DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = ratio
    util.apply_all_modifiers(obj)


def voxel_remesh(obj, voxel_size):
    # Safety net: never voxel a mesh so finely relative to its bounding box that the
    # grid explodes and exhausts memory. A degenerate input (e.g. a Solidify spike
    # flung millions of units out) would otherwise OOM the whole process here. Legit
    # fine molds (a few hundred voxels across) stay far under the cap.
    mn, mx = util.world_bbox(obj)
    longest = max(mx.x - mn.x, mx.y - mn.y, mx.z - mn.z, 1e-4)
    if not math.isfinite(longest):
        longest = 1e9
    voxel_size = max(voxel_size, longest / C.REMESH_MAX_SPAN_VOXELS)
    mod = obj.modifiers.new("mf_remesh", 'REMESH')
    mod.mode = 'VOXEL'
    mod.voxel_size = voxel_size
    util.apply_all_modifiers(obj)


def center_object(obj):
    """Move the object so its bounding-box center sits at the world origin,
    baking the transform into the mesh data."""
    mn, mx = util.world_bbox(obj)
    center = (mn + mx) * 0.5
    obj.matrix_world = Matrix.Translation(-center) @ obj.matrix_world
    obj.data.transform(obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    obj.data.update()
