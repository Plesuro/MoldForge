"""User-facing parameters for MoldForge, stored on the Scene.

Sizes are absolute (scene units / mm). Set your scene unit to millimetres and the
fields read as mm. Sensible defaults suit print-scale models (~20-200 mm); the
sprue/vents are auto-capped so they can't blow out a small mold.
"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from mathutils import Vector

from .core import constants as C


def _dist(name, default, desc, mn=0.0, soft=None, update=None):
    kw = dict(name=name, description=desc, default=default,
              min=mn, subtype='DISTANCE', unit='LENGTH')
    if soft is not None:
        kw["soft_max"] = soft
    if update is not None:
        kw["update"] = update
    return FloatProperty(**kw)


def mold_caps(context):
    """The funnel/vent size caps the active model implies (same formulas the
    builder uses), or None when no model is active to measure. Lets the UI clamp
    input live and show the true effective sizes instead of silently capping at
    build time.

    Uses the object's CACHED bounding box (8 corners) rather than iterating every
    vertex — this runs on every panel redraw, so a per-vertex scan here makes
    Blender crawl on heavy meshes."""
    obj = getattr(context, "active_object", None)
    if obj is None or obj.type != 'MESH' or obj.name.startswith("MF_"):
        return None
    mw = obj.matrix_world
    xs = []
    ys = []
    for c in obj.bound_box:                 # 8 local-space corners, cached
        w = mw @ Vector((c[0], c[1], c[2]))
        xs.append(w.x)
        ys.append(w.y)
    if not xs:
        return None
    p = context.scene.moldforge
    offset = p.wall_thickness + (p.shell_wall if p.box_style == 'POUR_BOX' else 0.0)
    half_min = min(max(xs) - min(xs), max(ys) - min(ys)) * 0.5 + offset
    if half_min <= 0.0:
        return None
    return {"sprue_r": C.THROAT_CAP * half_min, "mouth_r": C.MOUTH_CAP * half_min,
            "vent_r": C.VENT_CAP * half_min, "half_min": half_min}


def _clamp_sprue_radius(self, context):
    caps = mold_caps(context)
    if not caps:
        return
    frac = C.THROAT_CAP_BIG if getattr(self, "big_throat", False) else C.THROAT_CAP
    cap = caps["half_min"] * frac
    if self.sprue_radius > cap:
        self["sprue_radius"] = cap               # dict-set: no update recursion


def _clamp_vent_radius(self, context):
    caps = mold_caps(context)
    if caps and self.vent_radius > caps["vent_r"]:
        self["vent_radius"] = caps["vent_r"]


# Typical densities (g/ml) for the weight estimate. Picking a preset just fills the
# density field below; you can still type a custom number afterwards (that flips the
# dropdown back to Custom on its own next redraw — the value is what matters).
_SILICONE_PRESETS = {
    'DRAGONSKIN': 1.07,    # Smooth-On Dragon Skin platinum series
    'MOLDSTAR': 1.18,      # Smooth-On Mold Star
    'OOMOO': 1.42,         # Smooth-On Oomoo tin-cure
    'ECOFLEX': 1.07,       # Smooth-On Ecoflex
    'MOLDMAX': 1.42,       # Smooth-On Mold Max tin-cure
    'PLATSIL': 1.12,       # Polytek / generic platinum RTV
}
_CAST_PRESETS = {
    'URETHANE': 1.05,      # Smooth-On Smooth-Cast urethane resin
    'EPOXY': 1.15,         # generic epoxy casting resin
    'POLYESTER': 1.10,     # polyester casting resin
    'PLASTER': 1.80,       # plaster of Paris / gypsum
    'WAX': 0.90,           # casting / candle wax
    'CONCRETE': 2.40,      # cement / GFRC
}


def _apply_silicone_preset(self, context):
    d = _SILICONE_PRESETS.get(self.silicone_preset)
    if d is not None:
        self.silicone_density = d


def _apply_cast_preset(self, context):
    d = _CAST_PRESETS.get(self.cast_preset)
    if d is not None:
        self.cast_density = d


class MoldForgeProperties(bpy.types.PropertyGroup):
    # --- Mold type ------------------------------------------------------ #
    box_style: EnumProperty(
        name="Mold Type",
        description="What MoldForge outputs — the two genuinely different functions",
        items=[
            ('POUR_BOX', "Silicone Pour Box",
             "Printed jacket you pour liquid silicone into — the silicone is the "
             "mold. For the glove/mother-mold workflow, set a thin gap and turn "
             "on Glove Skin Keys"),
            ('SOLID', "Direct Printed Mold",
             "The printed pieces ARE the mold — cast resin/wax/plaster straight "
             "in. Shape: hugging (least material) or block (easiest to clamp)"),
            ('TRAY', "Tray / Open Pour",
             "One-part open tray (pan) for FLAT or relief objects — text, logos, "
             "coins, medallions. The object sits at the bottom and the top is open: "
             "embed it and pour silicone over it for a stamp, carve it for a direct "
             "cast pan, or print just the frame for a real object. No split, wings "
             "or funnel"),
        ],
        default='POUR_BOX',
    )
    solid_shape: EnumProperty(
        name="Shape",
        description="Outer shape of a direct printed mold",
        items=[
            ('HUG', "Hugging", "Pieces follow the model's shape — least material"),
            ('BLOCK', "Block", "Rectangular block — easiest to clamp and stand"),
        ],
        default='HUG',
    )
    skin_keys: BoolProperty(
        name="Glove Skin Keys",
        description="Glove / mother-mold workflow: raise registration bumps on the "
                    "silicone skin that seat into pockets in the rigid shell, so a "
                    "thin skin can't shift or slump (set the silicone gap to the "
                    "skin thickness, e.g. 3 mm)",
        default=False,
    )

    # --- Tray / open pour (flat & relief objects) ----------------------- #
    tray_mode: EnumProperty(
        name="Tray Mode",
        description="What the printed tray does with your object",
        items=[
            ('EMBED', "Embed → silicone stamp",
             "Fuse the object into the tray floor and pour SILICONE over it. The "
             "cured silicone is a flexible negative stamp/mold you cast into"),
            ('FRAME', "Frame only (real object)",
             "Print just the open box at the object's footprint — drop your REAL "
             "object in and pour silicone around it"),
        ],
        default='EMBED',
    )
    tray_up: EnumProperty(
        name="Capture Face",
        description="Which way the object's detailed face points — the open pour side",
        items=[
            ('AUTO', "Auto", "Lay the object on its flattest side, detail facing up"),
            ('Z', "+Z up", "The object's +Z face is the detail / pour side"),
            ('X', "+X up", "The object's +X face is the detail / pour side"),
            ('Y', "+Y up", "The object's +Y face is the detail / pour side"),
        ],
        default='AUTO',
    )
    tray_outline: EnumProperty(
        name="Outline",
        description="Shape of the tray around the object",
        items=[
            ('RECT', "Rectangular", "A rectangular pan around the object's footprint "
             "— simplest and strongest"),
            ('HUG', "Hug (rounded)", "Walls follow the object's outline with rounded "
             "corners — uses less silicone and plastic, especially for round or "
             "irregular shapes"),
        ],
        default='RECT',
    )
    tray_wall: _dist("Pan Wall", 2.5,
                     "Thickness of the printed tray walls", mn=0.4, soft=8.0)
    tray_floor: _dist("Pan Floor", 3.0,
                      "Thickness of the printed tray floor", mn=0.4, soft=15.0)
    tray_margin: _dist("Border", 6.0,
                       "Gap between the object and the tray wall — the silicone "
                       "border around your object", mn=0.0, soft=30.0)
    tray_depth: _dist("Pour Depth", 5.0,
                      "How much silicone stands above the object's high point "
                      "(the slab thickness)", mn=0.0, soft=40.0)

    # --- Sizes (absolute, scene units / mm) ----------------------------- #
    wall_thickness: _dist("Silicone / Wall Thickness", 3.0,
                          "Silicone thickness (pour gap / glove skin, or the "
                          "direct mold's wall)", mn=0.1)
    shell_wall: _dist("Printed Shell Wall", 2.0,
                      "Thickness of the printed pour-jacket wall", mn=0.4)
    sprue_radius: _dist("Throat Radius", 4.0,
                        "Radius of the funnel's narrow BOTTOM — the hole where it "
                        "enters the mold. The mouth (top) is this x Mouth Flare. "
                        "Typing more than the mold can take snaps to the maximum "
                        "that fits; the panel shows the exact funnel being built",
                        mn=0.3, soft=15.0, update=_clamp_sprue_radius)
    big_throat: BoolProperty(
        name="Oversized Throat",
        description="Let the throat radius grow past the auto-fit cap (≈30% of the "
                    "mold half-width, up to ≈45%). Leaves less shell around the hole — "
                    "the panel warns when the throat is oversized",
        default=False,
        update=_clamp_sprue_radius,
    )
    funnel_height: _dist("Funnel Height", 12.0,
                         "How far the pour funnel stands proud of the mold top",
                         mn=1.0, soft=60.0)

    # --- Base ----------------------------------------------------------- #
    base_style: EnumProperty(
        name="Bottom",
        description="How the bottom of the mold is finished — the three genuinely "
                    "different functions",
        items=[
            ('FLAT', "Flat (closed)",
             "Flat closed floor the mold stands on (add a Mounting Flange to "
             "bolt it to a board)"),
            ('OPEN', "Open Bottom",
             "Open at the master's base — the master sits on the build plate and "
             "you pour from the top (add a Detachable Key Plate for a separate "
             "keyed bottom)"),
            ('FOLLOW', "Follow Model",
             "The bottom follows the model's shape (no flat cut)"),
        ],
        default='FLAT',
    )
    base_flange: BoolProperty(
        name="Mounting Flange",
        description="Add an outward bolted skirt around the flat base — clamps "
                    "the mold down to a board",
        default=True,
    )
    base_plate: BoolProperty(
        name="Detachable Key Plate",
        description="Close the open bottom with a separate printed plate: the "
                    "model registers into a pocket, and a ring tongue on the "
                    "shell's rim drops into a groove around the plate's chin "
                    "collar — self-aligning all round and a seal for the pour",
        default=False,
    )
    fit_clearance: _dist("Fit Clearance", 0.3,
                         "Gap PER FACE between mating printed parts (the key "
                         "plate's groove vs the shell's tongue, and the model "
                         "pocket). Increase if your prints come out too tight to "
                         "assemble", mn=0.0, soft=1.0)
    flange_width: _dist("Flange Width", 6.0,
                        "How far the base flange extends past the mold")

    # --- Clamp wings ---------------------------------------------------- #
    wings: BoolProperty(
        name="Clamp Wings",
        description="Add full-height clamp flanges along the parting seam(s), with "
                    "bolt holes, to clamp the pieces together. They hug the model's "
                    "profile from top to bottom; with 3+ radial pieces every seam "
                    "gets a bolted flange pair",
        default=True,
    )
    wing_width: _dist("Wing Width", 8.0,
                      "How far the clamp flanges spread out past the sides")
    bolt_diameter: _dist("Bolt Diameter", 3.0,
                         "Diameter of the clamp/flange bolt holes", mn=0.5)
    bolt_auto: BoolProperty(
        name="Auto Bolts",
        description="Place the clamp bolt holes automatically by flange height; "
                    "untick to set an exact count per side/seam instead",
        default=True,
    )
    bolt_count: IntProperty(
        name="Bolts / Side",
        description="Exact bolt holes per clamp wing / seam when Auto Bolts is "
                    "off — 0 means no bolt holes at all",
        default=0, min=0, max=10,
    )

    # --- Split / keys --------------------------------------------------- #
    split_axis: EnumProperty(
        name="Split Axis",
        description="Direction the two halves separate",
        items=[
            ('AUTO', "Auto",
             "Pick the axis the model releases best along (fewest undercuts), "
             "falling back to the wider footprint when they're equal"),
            ('X', "X", "Split left/right"),
            ('Y', "Y", "Split front/back"),
        ],
        default='AUTO',
    )
    split_offset: FloatProperty(
        name="Parting Offset", default=0.0, subtype='DISTANCE', unit='LENGTH',
        description="Slide the parting plane off-centre along the split axis "
                    "(auto-clamped so neither half vanishes)")
    split_horizontal: BoolProperty(
        name="Horizontal Split",
        description="Also split the shell horizontally — for XL molds: each piece "
                    "prints shorter, and the horizontal seam gets a bolted flange "
                    "ring all around. Size the holes for your threaded inserts "
                    "with Bolt Diameter (inserts in the lower lip, screws from "
                    "the top)",
        default=False,
    )
    split_z_offset: FloatProperty(
        name="Seam Height", default=0.0, subtype='DISTANCE', unit='LENGTH',
        description="Slide the horizontal seam up/down from mid-height "
                    "(auto-clamped so neither stack vanishes)")
    contoured: BoolProperty(
        name="Contoured Parting",
        description="Parting surface follows the model's mid-profile (self-"
                    "registering) instead of a flat plane; falls back to flat if "
                    "it can't produce clean halves",
        default=True,
    )
    key_count: IntProperty(
        name="Alignment Keys",
        description="Registration features on the parting face (used with a flat "
                    "parting + no wings; a contoured parting self-registers)",
        default=2, min=0, max=4,
    )
    parts_count: IntProperty(
        name="Mold Pieces",
        description="How many pieces the mold splits into. 2 is a normal two-part "
                    "split; 3-4 splits it into radial wedges around the vertical axis, "
                    "so a model with undercuts on every side can still release (each "
                    "wedge pulls straight out)",
        default=2, min=2, max=4,
    )
    registration: EnumProperty(
        name="Registration",
        description="What the alignment features look like (flat parting)",
        items=[
            ('KEYS', "Cone Keys", "Conical pins seating into sockets"),
            ('TEETH', "Interlocking Teeth", "A castellated row along the seam"),
        ],
        default='KEYS',
    )

    # --- Sprue / vents -------------------------------------------------- #
    sprue: BoolProperty(
        name="Sprue (pour funnel)",
        description="Cut a funnel from the top into the cavity for pouring",
        default=True,
    )
    sprue_flare: FloatProperty(
        name="Funnel Flare", default=2.4, min=1.0, max=4.0,
        description="Mouth width as a multiple of the sprue radius — 1.0 is a "
                    "straight tube (best when a wide cone won't fit the shape), "
                    "bigger is a wider catch funnel (auto-capped to the mold)",
    )
    big_mouth: BoolProperty(
        name="Oversized Mouth",
        description="Let the mouth flare grow past the auto-fit cap (≈45% of the "
                    "mold half-width). It may then overhang the mold edge — the "
                    "panel warns when the mouth is oversized. Use when a wide catch "
                    "funnel won't otherwise fit the shape",
        default=False,
    )
    sprue_count: IntProperty(
        name="Pour Points",
        description="Number of pour funnels (more helps fill tall figures)",
        default=1, min=1, max=4,
    )
    sprue_place: EnumProperty(
        name="Sprue Placement",
        description="Where the pour funnel sits on the model",
        items=[
            ('XY', "Center XY", "Center the funnel on the model's footprint "
             "(both X and Y) — straight down the middle"),
            ('X', "Center X", "Center on X; follow the model's highest point along Y"),
            ('Y', "Center Y", "Center on Y; follow the model's highest point along X"),
            ('TOP', "Highest Point", "Put the funnel on the model's highest point — "
             "best venting for tall figures, but off-center on a leaning model"),
            ('MANUAL', "Manual X/Y", "Type the funnel position yourself as an X/Y "
             "offset from the model's footprint centre"),
        ],
        default='TOP',
    )
    sprue_x: FloatProperty(
        name="Sprue X", default=0.0, subtype='DISTANCE', unit='LENGTH',
        description="Manual funnel X offset from the model's footprint centre "
                    "(0 = centre); used when Sprue Placement is Manual. Clamped to "
                    "the footprint so the funnel stays on the model")
    sprue_y: FloatProperty(
        name="Sprue Y", default=0.0, subtype='DISTANCE', unit='LENGTH',
        description="Manual funnel Y offset from the model's footprint centre "
                    "(0 = centre); used when Sprue Placement is Manual. Clamped to "
                    "the footprint so the funnel stays on the model")
    vent_count: IntProperty(
        name="Air Vents",
        description="Thin channels from the cavity's high points to the outside",
        default=0, min=0, max=8,
    )
    vent_radius: _dist("Vent Radius", 1.0,
                       "Radius of each air vent channel; typing more than the "
                       "mold can take snaps back to the maximum that fits",
                       mn=0.2, soft=4.0, update=_clamp_vent_radius)

    # --- Mesh prep ------------------------------------------------------ #
    heal: BoolProperty(
        name="Heal Mesh",
        description="Merge doubles, drop loose geometry and recalculate normals first",
        default=True,
    )
    decimate: BoolProperty(name="Decimate", default=False)
    decimate_ratio: FloatProperty(name="Ratio", default=0.5, min=0.1, max=1.0, subtype='FACTOR')
    voxel_safe: BoolProperty(
        name="Safe Remesh",
        description="Voxel-remesh the whole model first — for messy or non-manifold meshes",
        default=False,
    )
    voxel_size: _dist("Remesh Voxel", 1.0,
                      "Voxel size for Safe Remesh (smaller = finer, slower)", mn=0.05)

    # --- Materials (for the weight estimate) ---------------------------- #
    silicone_preset: EnumProperty(
        name="Mold Material",
        description="Pick a common mold material to fill in its density, or Custom "
                    "to type your own",
        items=[
            ('CUSTOM', "Custom", "Type the density yourself"),
            ('DRAGONSKIN', "Dragon Skin", "Smooth-On Dragon Skin (platinum) ≈ 1.07"),
            ('MOLDSTAR', "Mold Star", "Smooth-On Mold Star (platinum) ≈ 1.18"),
            ('OOMOO', "Oomoo", "Smooth-On Oomoo (tin-cure) ≈ 1.42"),
            ('ECOFLEX', "Ecoflex", "Smooth-On Ecoflex (soft platinum) ≈ 1.07"),
            ('MOLDMAX', "Mold Max", "Smooth-On Mold Max (tin-cure) ≈ 1.42"),
            ('PLATSIL', "Platinum RTV", "Generic platinum-cure RTV ≈ 1.12"),
        ],
        default='CUSTOM',
        update=_apply_silicone_preset,
    )
    cast_preset: EnumProperty(
        name="Cast Material",
        description="Pick a common casting material to fill in its density, or "
                    "Custom to type your own",
        items=[
            ('CUSTOM', "Custom", "Type the density yourself"),
            ('URETHANE', "Urethane Resin", "Smooth-Cast urethane resin ≈ 1.05"),
            ('EPOXY', "Epoxy Resin", "Generic epoxy casting resin ≈ 1.15"),
            ('POLYESTER', "Polyester Resin", "Polyester casting resin ≈ 1.10"),
            ('PLASTER', "Plaster", "Plaster of Paris / gypsum ≈ 1.80"),
            ('WAX', "Wax", "Casting / candle wax ≈ 0.90"),
            ('CONCRETE', "Concrete", "Cement / GFRC ≈ 2.40"),
        ],
        default='CUSTOM',
        update=_apply_cast_preset,
    )
    silicone_density: FloatProperty(
        name="Silicone g/ml", default=1.15, min=0.1, max=5.0,
        description="Density of the pour silicone / solid-mold material (RTV "
                    "silicone ≈ 1.1–1.2)")
    cast_density: FloatProperty(
        name="Cast g/ml", default=1.10, min=0.1, max=5.0,
        description="Density of what you cast (resin ≈ 1.1, plaster ≈ 1.8, wax ≈ 0.9)")
    plastic_density: FloatProperty(
        name="Print g/ml", default=1.24, min=0.1, max=5.0,
        description="Density of the printed plastic (PLA ≈ 1.24, PETG ≈ 1.27)")

    # --- Export --------------------------------------------------------- #
    export_after: BoolProperty(name="Export after generate", default=False)
    export_dir: StringProperty(name="Export Folder", subtype='DIR_PATH', default="//")

    # --- Results (read-only display) ------------------------------------ #
    last_cavity_volume: FloatProperty(name="Cavity Volume", default=0.0)
    last_silicone_volume: FloatProperty(name="Silicone Volume", default=0.0)
    last_plastic_volume: FloatProperty(name="Box Plastic Volume", default=0.0)
