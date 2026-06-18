"""Binary STL export, written directly so it works in any context/headless."""

import os
import struct

import bpy


def export_stl(obj, filepath):
    """Write ``obj`` (with its world transform applied) as a binary STL."""
    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(deps)
    me = eval_obj.to_mesh()
    try:
        me.calc_loop_triangles()
        mw = eval_obj.matrix_world   # consistent with the evaluated mesh
        verts = me.vertices
        tris = me.loop_triangles

        with open(filepath, 'wb') as f:
            f.write(b'\x00' * 80)                       # 80-byte header
            f.write(struct.pack('<I', len(tris)))       # triangle count
            for tri in tris:
                p = [mw @ verts[i].co for i in tri.vertices]
                normal = (p[1] - p[0]).cross(p[2] - p[0])
                if normal.length > 0.0:
                    normal.normalize()
                f.write(struct.pack('<3f', normal.x, normal.y, normal.z))
                for v in p:
                    f.write(struct.pack('<3f', v.x, v.y, v.z))
                f.write(struct.pack('<H', 0))           # attribute byte count
    finally:
        eval_obj.to_mesh_clear()
    return filepath


def _safe_name(name):
    """Filesystem-safe filename stem — object names can hold path-significant
    characters (``/``, ``:`` …) that would break os.path.join."""
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "_" for c in name)
    return cleaned.strip("._") or "part"


def export_objects(objects, directory):
    os.makedirs(directory, exist_ok=True)
    written = []
    for obj in objects:
        path = os.path.join(directory, f"{_safe_name(obj.name)}.stl")
        export_stl(obj, path)
        written.append(path)
    return written
