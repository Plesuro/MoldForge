"""Sidebar (N-panel) UI for MoldForge."""

import bpy

from .core import constants as C
from .properties import mold_caps

_VERSION = None


def _version():
    """The add-on version string, read once from the bundled manifest, so the panel
    can show exactly which build is loaded (Blender caches extensions, so a stale
    install otherwise looks identical to a fresh one)."""
    global _VERSION
    if _VERSION is None:
        try:
            import os
            import tomllib
            path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
            with open(path, "rb") as f:
                _VERSION = tomllib.load(f).get("version", "?")
        except Exception:
            _VERSION = "?"
    return _VERSION


def _mm_per_unit(context):
    """How many millimetres one Blender unit represents, for the volume estimates.
    Mold makers model with 1 unit = 1 mm at the default scale_length=1.0, so honour
    that; a user who has configured real units (scale_length != 1) gets them used."""
    s = context.scene.unit_settings.scale_length
    return 1.0 if abs(s - 1.0) < 1e-9 else s * 1000.0


def _vol_row(box, name, units_cubed, density=None, mpu=1.0):
    """One 'Name ........ 123.4 ml · 142 g' line. ``mpu`` = mm per Blender unit."""
    ml = units_cubed * (mpu ** 3) / 1000.0
    row = box.row()
    row.label(text=name)
    sub = row.row()
    sub.alignment = 'RIGHT'
    if density:
        sub.label(text=f"{ml:,.1f} ml · {ml * density:,.0f} g")
    else:
        sub.label(text=f"{ml:,.1f} ml")


class MOLDFORGE_PT_main(bpy.types.Panel):
    bl_label = "MoldForge"
    bl_idname = "MOLDFORGE_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MoldForge"

    def draw(self, context):
        layout = self.layout
        props = context.scene.moldforge
        tray = props.box_style == 'TRAY'

        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("moldforge.generate", icon='MOD_FLUIDSIM')
        vrow = layout.row()
        vrow.alignment = 'RIGHT'
        vrow.label(text=f"v{_version()}")

        box = layout.box()
        box.label(text="Mold", icon='MOD_SOLIDIFY')
        box.prop(props, "box_style")

        if tray:
            box.prop(props, "tray_mode")
            row = box.row(align=True)
            row.prop(props, "tray_up")
            row.prop(props, "tray_outline")
            row = box.row(align=True)
            row.prop(props, "tray_wall")
            row.prop(props, "tray_floor")
            row = box.row(align=True)
            row.prop(props, "tray_margin")
            row.prop(props, "tray_depth")
            if props.tray_mode == 'FRAME':
                box.label(text="Drop your real object in, then pour silicone",
                          icon='INFO')
            else:
                box.label(text="Pour silicone over the embedded object", icon='INFO')
        else:
            is_block = props.box_style == 'SOLID' and props.solid_shape == 'BLOCK'
            if props.box_style == 'SOLID':
                box.prop(props, "solid_shape", expand=True)
            box.prop(props, "wall_thickness")
            if props.box_style == 'POUR_BOX':
                box.prop(props, "shell_wall")
                box.prop(props, "skin_keys")
            box.prop(props, "base_style")
            if props.base_style == 'FLAT':
                row = box.row(align=True)
                row.prop(props, "base_flange")
                if props.base_flange:
                    row.prop(props, "flange_width", text="Width")
            elif props.base_style == 'OPEN':
                box.prop(props, "base_plate")
                if props.base_plate:
                    box.prop(props, "fit_clearance")

            box = layout.box()
            box.label(text="Split & Clamp", icon='MOD_BOOLEAN')
            box.prop(props, "parts_count")
            row = box.row(align=True)
            row.prop(props, "split_horizontal")
            if props.split_horizontal:
                row.prop(props, "split_z_offset", text="Height")
            if props.parts_count >= 3:
                box.label(text="Radial wedges — each pulls straight out", icon='MOD_ARRAY')
                if not is_block:
                    box.prop(props, "wings")
                if props.wings and not is_block:
                    box.prop(props, "wing_width")
                    box.prop(props, "bolt_diameter")
                    row = box.row(align=True)
                    row.prop(props, "bolt_auto", toggle=True)
                    sub = row.row(align=True)
                    sub.active = not props.bolt_auto
                    sub.prop(props, "bolt_count")
                else:
                    box.prop(props, "key_count", text="Seam Pins")
            else:
                box.prop(props, "split_axis", expand=True)
                box.prop(props, "split_offset")
                box.prop(props, "contoured")
                if not props.contoured:
                    box.prop(props, "key_count")
                    if props.key_count > 0 and not props.wings:
                        box.prop(props, "registration")
                box.prop(props, "wings")
                if props.wings:
                    box.prop(props, "wing_width")
                    box.prop(props, "bolt_diameter")
                    row = box.row(align=True)
                    row.prop(props, "bolt_auto", toggle=True)
                    sub = row.row(align=True)
                    sub.active = not props.bolt_auto
                    sub.prop(props, "bolt_count")

            box = layout.box()
            box.label(text="Sprue & Vents", icon='OUTLINER_OB_FORCE_FIELD')
            box.prop(props, "sprue")
            caps = mold_caps(context)
            if props.sprue:
                row = box.row(align=True)
                row.prop(props, "sprue_radius")
                row.prop(props, "big_throat", text="", icon='FULLSCREEN_ENTER', toggle=True)
                box.prop(props, "funnel_height")
                row = box.row(align=True)
                row.prop(props, "sprue_flare", text="Mouth Flare")
                row.prop(props, "big_mouth", text="", icon='FULLSCREEN_ENTER', toggle=True)
                box.prop(props, "sprue_count")
                box.prop(props, "sprue_place", text="Placement")
                if props.sprue_place == 'MANUAL':
                    row = box.row(align=True)
                    row.prop(props, "sprue_x", text="X")
                    row.prop(props, "sprue_y", text="Y")
                # The truth row: the funnel that will actually be built, flagged when the
                # mold size auto-fits the typed values or an Oversized toggle exceeds them.
                if caps:
                    hm = caps["half_min"]
                    throat_cap = hm * (C.THROAT_CAP_BIG if props.big_throat else C.THROAT_CAP)
                    mouth_cap = hm * (C.MOUTH_CAP_BIG if props.big_mouth else C.MOUTH_CAP)
                    neck = min(props.sprue_radius, throat_cap)
                    mouth = max(min(neck * props.sprue_flare, mouth_cap), neck)
                    throat_over = props.big_throat and neck > hm * C.THROAT_CAP + 1e-6
                    mouth_over = props.big_mouth and mouth > hm * C.MOUTH_CAP + 1e-6
                    throat_fit = (not props.big_throat
                                  and props.sprue_radius > hm * C.THROAT_CAP + 1e-6)
                    mouth_fit = (not props.big_mouth
                                 and neck * props.sprue_flare > hm * C.MOUTH_CAP + 1e-6)
                    row = box.row()
                    row.label(text=f"Built: throat Ø{2 * neck:.1f} · mouth Ø{2 * mouth:.1f}",
                              icon='ERROR' if (throat_over or mouth_over or throat_fit or mouth_fit)
                              else 'INFO')
                    if throat_over or mouth_over:
                        box.label(text="oversized funnel — may overhang / thin the shell",
                                  icon='ERROR')
                    elif throat_fit or mouth_fit:
                        box.label(text="auto-fitted to this mold's size")
            row = box.row(align=True)
            row.prop(props, "vent_count")
            if props.vent_count > 0:
                row.prop(props, "vent_radius")
                if caps and props.vent_radius > caps["vent_r"] + 1e-6:
                    box.label(text=f"vents auto-fitted to Ø{2 * caps['vent_r']:.1f}",
                              icon='ERROR')

        box = layout.box()
        box.label(text="Mesh Prep", icon='MODIFIER')
        box.prop(props, "heal")
        row = box.row(align=True)
        row.prop(props, "decimate")
        if props.decimate:
            row.prop(props, "decimate_ratio")
        row = box.row(align=True)
        row.prop(props, "voxel_safe")
        if props.voxel_safe:
            row.prop(props, "voxel_size")

        box = layout.box()
        box.label(text="Export", icon='EXPORT')
        box.prop(props, "export_after")
        if props.export_after:
            box.prop(props, "export_dir")
        box.operator("moldforge.export", icon='FILE_TICK')

        box = layout.box()
        box.label(text="Material Density (g/ml)", icon='PHYSICS')
        row = box.row(align=True)
        row.prop(props, "silicone_preset", text="Mold")
        row.prop(props, "cast_preset", text="Cast")
        row = box.row(align=True)
        row.prop(props, "silicone_density", text="Mold")
        row.prop(props, "cast_density", text="Cast")
        row.prop(props, "plastic_density", text="Print")

        mpu = _mm_per_unit(context)
        box = layout.box()
        box.label(text="Estimated Volume / Weight", icon='MESH_CUBE')
        box.label(text=f"(1 unit = {mpu:g} mm)")
        if tray:
            _vol_row(box, "Silicone to pour", props.last_silicone_volume,
                     props.silicone_density, mpu)
            _vol_row(box, "Pan plastic", props.last_plastic_volume, props.plastic_density, mpu)
            if props.tray_mode != 'FRAME':            # cast volume unknown for a real object
                _vol_row(box, "Cast material", props.last_cavity_volume, props.cast_density, mpu)
        elif props.box_style == 'POUR_BOX':
            silicone_label = "Silicone skin" if props.skin_keys else "Silicone to pour"
            _vol_row(box, silicone_label, props.last_silicone_volume, props.silicone_density, mpu)
            _vol_row(box, "Box plastic", props.last_plastic_volume, props.plastic_density, mpu)
            _vol_row(box, "Cast material", props.last_cavity_volume, props.cast_density, mpu)
        else:
            _vol_row(box, "Mold material", props.last_silicone_volume, props.silicone_density, mpu)
            _vol_row(box, "Cast material", props.last_cavity_volume, props.cast_density, mpu)
