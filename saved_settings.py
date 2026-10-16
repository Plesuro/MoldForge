"""Saved settings: your own MoldForge presets, kept between .blend files.

Nothing ships with the add-on. A saved setting is exactly what YOU stored with
"Save Current Settings": every value on the MoldForge panels, written as a small
JSON file in the add-on's user folder (outside the add-on itself, so updating
MoldForge never wipes it). Loading one sets them all in one click, so a new
.blend no longer means rebuilding your mold setup by hand.

JSON on purpose, not a Python preset script: loading only ever SETS values, one
at a time. A setting a newer version renamed, or an option that no longer
exists, is skipped and counted - the rest of the preset still loads.
"""

import json
import os
import re

import bpy
from bpy.props import StringProperty

FORMAT = 1

# Not settings: the SVG artwork belongs to one stamp, the last_* figures are
# the result of the last build.
_SKIP = frozenset({"rna_type", "name", "stamp_svg"})
_SKIP_PREFIX = ("last_",)

# Written first when loading: each has an update callback that writes another
# setting (a printer fills in its height, a material its density, the
# oversized toggles decide whether the radius is clamped), so the value saved
# for that other setting must land AFTER it.
_DRIVERS = ("box_style", "base_style", "printer_preset", "silicone_preset",
            "cast_preset", "big_throat", "big_mouth")

_ENV_DIR = "MOLDFORGE_PRESET_DIR"      # tests point this at a scratch folder


def preset_dir(create=False):
    """The folder holding the saved settings. The extension's own user folder
    when installed as an extension (Blender keeps it across updates), else a
    folder in Blender's user config."""
    override = os.environ.get(_ENV_DIR)
    if override:
        path = override
    else:
        try:
            path = bpy.utils.extension_path_user(__package__, path="saved_settings")
        except Exception:                       # not running as an extension
            path = os.path.join(bpy.utils.user_resource('CONFIG'),
                                "moldforge", "saved_settings")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _file_stem(name):
    """A safe file name for a preset name (the display name lives in the file)."""
    stem = re.sub(r"[^A-Za-z0-9 _.\-]+", "_", name).strip(" .")
    return (stem or "settings")[:64]


def _eligible(prop):
    return (prop.identifier not in _SKIP
            and not prop.identifier.startswith(_SKIP_PREFIX)
            and not prop.is_readonly
            and prop.type not in ('POINTER', 'COLLECTION'))


def settings_dict(props):
    """Every saved-able setting on the property group, as plain JSON values."""
    out = {}
    for prop in props.bl_rna.properties:
        if not _eligible(prop):
            continue
        val = getattr(props, prop.identifier)
        if prop.type == 'ENUM' and prop.is_enum_flag:
            val = sorted(val)
        elif getattr(prop, "is_array", False):
            val = list(val)
        out[prop.identifier] = val
    return out


def apply_settings(props, values):
    """Set every known setting in ``values`` on ``props``. Returns
    ``(applied, skipped)``: a count, and the names that could not be set (gone
    from this version, or an option value that no longer exists)."""
    rna = props.bl_rna.properties
    order = ([n for n in _DRIVERS if n in values]
             + [n for n in values if n not in _DRIVERS])
    applied, skipped = 0, []
    for name in order:
        prop = rna.get(name)
        if prop is None or not _eligible(prop):
            skipped.append(name)
            continue
        val = values[name]
        if prop.type == 'ENUM' and prop.is_enum_flag:
            val = set(val)
        try:
            setattr(props, name, val)
            applied += 1
        except (TypeError, ValueError, AttributeError):
            skipped.append(name)
    return applied, skipped


def reset_settings(props):
    """Every setting back to its default (the last build's figures stay)."""
    n = 0
    for prop in props.bl_rna.properties:
        if _eligible(prop):
            props.property_unset(prop.identifier)
            n += 1
    return n


def list_presets():
    """``[(display name, path)]``, sorted by name. Unreadable files are skipped."""
    folder = preset_dir()
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in os.listdir(folder):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(folder, fn)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            name = str(data.get("name") or os.path.splitext(fn)[0])
        except (OSError, ValueError, AttributeError):
            continue
        out.append((name, path))
    out.sort(key=lambda t: t[0].lower())
    return out


def _path_for(name):
    for n, path in list_presets():
        if n == name:
            return path
    return None


def save_preset(props, name):
    """Write the current settings under ``name`` (replacing a preset of that
    name). Returns ``(path, count, replaced)``."""
    name = name.strip()
    if not name:
        raise ValueError("give the settings a name")
    path = _path_for(name)
    replaced = path is not None
    if path is None:
        folder = preset_dir(create=True)
        stem = _file_stem(name)
        path = os.path.join(folder, stem + ".json")
        k = 2
        while os.path.exists(path):                 # a different name, same stem
            path = os.path.join(folder, f"{stem}_{k}.json")
            k += 1
    try:
        from . import panel
        version = panel._version()
    except Exception:
        version = "?"
    values = settings_dict(props)
    data = {"moldforge_settings": FORMAT, "name": name, "version": version,
            "settings": values}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    return path, len(values), replaced


def load_preset(props, name):
    """Apply the preset ``name``. Returns ``(applied, skipped)``."""
    path = _path_for(name)
    if path is None:
        raise FileNotFoundError(f"no saved settings called '{name}'")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    values = data.get("settings")
    if not isinstance(values, dict):
        raise ValueError(f"'{name}' is not a MoldForge settings file")
    return apply_settings(props, values)


def delete_preset(name):
    path = _path_for(name)
    if path is None:
        raise FileNotFoundError(f"no saved settings called '{name}'")
    os.remove(path)


# --- operators ----------------------------------------------------------------- #

class MOLDFORGE_OT_settings_save(bpy.types.Operator):
    """Save every MoldForge setting under a name, to load into any .blend later"""
    bl_idname = "moldforge.settings_save"
    bl_label = "Save Current Settings"
    bl_options = {'REGISTER'}

    name: StringProperty(name="Name", default="My settings",
                         description="Name of the saved settings (the same name "
                                     "replaces the earlier save)")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, title="Save Current Settings", confirm_text="Save")

    def execute(self, context):
        try:
            _path, n, replaced = save_preset(context.scene.moldforge, self.name)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, f"Could not save the settings: {exc}")
            return {'CANCELLED'}
        verb = "Updated" if replaced else "Saved"
        self.report({'INFO'}, f"{verb} '{self.name.strip()}' ({n} settings)")
        return {'FINISHED'}


class MOLDFORGE_OT_settings_load(bpy.types.Operator):
    """Load these saved settings into this file"""
    bl_idname = "moldforge.settings_load"
    bl_label = "Load Saved Settings"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            applied, skipped = load_preset(context.scene.moldforge, self.name)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, f"Could not load the settings: {exc}")
            return {'CANCELLED'}
        if skipped:
            self.report({'WARNING'},
                        f"Loaded '{self.name}' ({applied} settings); "
                        f"{len(skipped)} not in this version were skipped: "
                        f"{', '.join(skipped[:6])}")
        else:
            self.report({'INFO'}, f"Loaded '{self.name}' ({applied} settings)")
        return {'FINISHED'}


class MOLDFORGE_OT_settings_delete(bpy.types.Operator):
    """Delete these saved settings"""
    bl_idname = "moldforge.settings_delete"
    bl_label = "Delete Saved Settings"
    bl_options = {'REGISTER'}

    name: StringProperty(name="Name", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Delete Saved Settings",
            message=f"Delete '{self.name}'?", confirm_text="Delete")

    def execute(self, context):
        try:
            delete_preset(self.name)
        except OSError as exc:
            self.report({'ERROR'}, f"Could not delete: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Deleted '{self.name}'")
        return {'FINISHED'}


class MOLDFORGE_OT_settings_reset(bpy.types.Operator):
    """Put every MoldForge setting back to its default"""
    bl_idname = "moldforge.settings_reset"
    bl_label = "Reset to Defaults"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Reset to Defaults",
            message="Put every MoldForge setting back to its default?",
            confirm_text="Reset")

    def execute(self, context):
        n = reset_settings(context.scene.moldforge)
        self.report({'INFO'}, f"{n} settings back to their defaults")
        return {'FINISHED'}


class MOLDFORGE_OT_settings_folder(bpy.types.Operator):
    """Open the folder with your saved settings (copy the files to share or back them up)"""
    bl_idname = "moldforge.settings_folder"
    bl_label = "Open Settings Folder"
    bl_options = {'REGISTER'}

    def execute(self, context):
        try:
            folder = preset_dir(create=True)
        except OSError as exc:
            self.report({'ERROR'}, f"Could not create the folder: {exc}")
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


class MOLDFORGE_MT_settings_delete(bpy.types.Menu):
    bl_label = "Delete Saved Settings"
    bl_idname = "MOLDFORGE_MT_settings_delete"

    def draw(self, context):
        layout = self.layout
        for name, _path in list_presets():
            layout.operator("moldforge.settings_delete", text=name,
                            icon='X').name = name


class MOLDFORGE_MT_settings(bpy.types.Menu):
    bl_label = "Saved Settings"
    bl_idname = "MOLDFORGE_MT_settings"

    def draw(self, context):
        layout = self.layout
        names = [n for n, _p in list_presets()]
        if names:
            for name in names:
                layout.operator("moldforge.settings_load", text=name,
                                icon='PRESET').name = name
        else:
            row = layout.row()
            row.active = False
            row.label(text="Nothing saved yet", icon='INFO')
        layout.separator()
        layout.operator("moldforge.settings_save", text="Save Current Settings...",
                        icon='ADD')
        if names:
            layout.menu("MOLDFORGE_MT_settings_delete", text="Delete", icon='REMOVE')
        layout.operator("moldforge.settings_reset", icon='LOOP_BACK')
        layout.separator()
        layout.operator("moldforge.settings_folder", icon='FILE_FOLDER')


SETTINGS_CLASSES = (
    MOLDFORGE_OT_settings_save,
    MOLDFORGE_OT_settings_load,
    MOLDFORGE_OT_settings_delete,
    MOLDFORGE_OT_settings_reset,
    MOLDFORGE_OT_settings_folder,
    MOLDFORGE_MT_settings_delete,
    MOLDFORGE_MT_settings,
)
