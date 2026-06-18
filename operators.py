"""MoldForge operators: generate the mold, and export the parts."""

import os

import bpy
from bpy.props import StringProperty

from .core import pipeline
from .core import export as mf_export
from .core import util as mf_util


class MOLDFORGE_OT_generate(bpy.types.Operator):
    bl_idname = "moldforge.generate"
    bl_label = "Generate Mold"
    bl_description = "Build a silicone mold from the active mesh"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _gen = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    # Scripts / tests / EXEC context build synchronously.
    def execute(self, context):
        props = context.scene.moldforge
        try:
            result = pipeline.build_mold_system(context.active_object, props)
        except Exception as exc:  # surface a clean message instead of a traceback
            self.report({'ERROR'}, f"Mold generation failed: {exc}")
            return {'CANCELLED'}
        return self._finalize(context, props, result)

    # From the UI: drive the build phase-by-phase so progress shows and a heavy build
    # doesn't look frozen — wait cursor + per-phase status + a progress bar.
    def invoke(self, context, event):
        props = context.scene.moldforge
        for warning in pipeline.prebuild_warnings(context.active_object, props):
            self.report({'WARNING'}, warning)
        self._gen = pipeline.staged_build(context.active_object, props)
        wm = context.window_manager
        context.window.cursor_set('WAIT')
        wm.progress_begin(0.0, 1.0)
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'RUNNING_MODAL'}
        try:
            frac, label = next(self._gen)
        except StopIteration as done:
            self._cleanup(context)
            return self._finalize(context, context.scene.moldforge, done.value)
        except Exception as exc:   # all recovery exhausted / bad input
            self._cleanup(context)
            self.report({'ERROR'}, f"Mold generation failed: {exc}")
            return {'CANCELLED'}
        context.window_manager.progress_update(frac)
        if context.workspace:
            context.workspace.status_text_set(f"MoldForge: {label}… ({frac * 100:.0f}%)")
        return {'RUNNING_MODAL'}

    def _cleanup(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        self._gen = None
        wm.progress_end()
        context.window.cursor_set('DEFAULT')
        if context.workspace:
            context.workspace.status_text_set(None)

    def _finalize(self, context, props, result):
        props.last_cavity_volume = result["cavity_volume"]
        props.last_silicone_volume = result["silicone_volume"]
        props.last_plastic_volume = result.get("plastic_volume", 0.0)

        if props.box_style == 'TRAY':
            mode = getattr(props, "tray_mode", 'EMBED')
            if mode == 'FRAME':
                summary = (f"Frame ready. Silicone to pour ≈ "
                           f"{result['silicone_volume']:,.0f} u³ around your object.")
            else:
                summary = (f"Tray ready. Pour ≈ {result['silicone_volume']:,.0f} u³ of "
                           f"silicone over the embedded master.")
        elif props.box_style == 'POUR_BOX':
            what = "Silicone skin" if getattr(props, "skin_keys", False) else "Silicone to pour"
            summary = (f"Pour box ready. {what} ≈ {result['silicone_volume']:,.0f} u³ "
                       f"(MF_Skin shows it).")
        else:
            summary = f"Mold ready. Material ≈ {result['silicone_volume']:,.0f} u³."

        if props.box_style != 'TRAY' and getattr(props, "parts_count", 2) >= 3:
            summary += f" Split into {props.parts_count} radial wedges."

        notes = []
        if result.get("remeshed"):
            notes.append("model was auto-remeshed (non-manifold or very heavy, so "
                         "fine surface detail is smoothed)")
        if result.get("trimmed"):
            notes.append("a small severed fragment was trimmed (e.g. an open-bottom "
                         "rim) to keep each half one solid")
        undercut = result.get("undercut", 0.0)
        if undercut > 0.04:
            notes.append(f"~{undercut * 100:.0f}% of the model is undercut on the "
                         f"{result.get('axis', '?')} axis and may not release cleanly "
                         f"from a two-part mold (try another Split Axis)")

        if props.export_after and props.export_dir:
            directory = bpy.path.abspath(props.export_dir)
            try:
                if not os.path.isdir(directory):
                    raise OSError(f"{directory!r} is not a folder")
                written = mf_export.export_objects(result["parts"], directory)
                summary += f" Exported {len(written)} part(s)."
            except Exception as exc:  # the mold built fine; don't fail on export
                notes.append(f"export failed: {getattr(exc, 'strerror', None) or exc}")

        if notes:
            self.report({'WARNING'}, summary + " (" + "; ".join(notes) + ".)")
        else:
            self.report({'INFO'}, summary)
        return {'FINISHED'}


class MOLDFORGE_OT_export(bpy.types.Operator):
    bl_idname = "moldforge.export"
    bl_label = "Export Mold Parts"
    bl_description = "Export the generated mold parts (MF_Mold_*) as STL files"
    bl_options = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        directory = bpy.path.abspath(self.directory or context.scene.moldforge.export_dir)
        if not directory or not os.path.isdir(directory):
            self.report({'ERROR'}, "Choose a valid export folder first.")
            return {'CANCELLED'}

        coll = bpy.data.collections.get(mf_util.COLLECTION_NAME)
        parts = [o for o in coll.objects if o.name.startswith("MF_Mold_")] if coll else []
        if not parts:
            self.report({'ERROR'}, "No mold parts found. Generate a mold first.")
            return {'CANCELLED'}

        try:
            written = mf_export.export_objects(parts, directory)
        except Exception as exc:
            self.report({'ERROR'}, f"Export failed: {getattr(exc, 'strerror', None) or exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Exported {len(written)} part(s) to {directory}")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.directory = bpy.path.abspath(context.scene.moldforge.export_dir or "//")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
