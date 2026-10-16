"""User-facing parameters for MoldForge, stored on the Scene.

All size fields are in MILLIMETRES, whatever the scene's unit system (Metric, Imperial,
or none). The build converts mm to Blender units with the scene's unit scale
(``core.units.mm_per_unit``), so a 3 mm wall is 3 mm whether the scene is set to
millimetres, inches, or the bare "1 unit = 1 mm" convention. Sensible defaults suit
print-scale models (~20-200 mm); the sprue/vents are auto-capped so they can't blow out
a small mold.
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
from .core import units


def _dist(name, default, desc, mn=0.0, soft=None, update=None):
    # A size field, in MILLIMETRES. A plain float (not a Blender 'DISTANCE'/LENGTH unit)
    # so the number the user types is always millimetres, independent of the scene's unit
    # system; the build converts mm -> Blender units via core.units.mm_per_unit.
    kw = dict(name=name, description=desc + " (mm)", default=default, min=mn)
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
    # Sizes are in millimetres; the model's footprint is in Blender units. Work the caps
    # out in millimetres (convert the footprint with the scene's unit scale) so they match
    # the mm size fields in any unit system.
    mpu = units.mm_per_unit(context.scene)
    offset = p.wall_thickness + (p.shell_wall if p.box_style == 'POUR_BOX' else 0.0)
    footprint_mm = min(max(xs) - min(xs), max(ys) - min(ys)) * mpu
    half_min = footprint_mm * 0.5 + offset
    if half_min <= 0.0:
        return None
    return {"sprue_r": C.THROAT_CAP * half_min, "mouth_r": C.MOUTH_CAP * half_min,
            "vent_r": C.VENT_CAP * half_min, "half_min": half_min}


def _clamp_sprue_radius(self, context):
    if getattr(self, "big_throat", False):
        return                                   # Oversized Throat = fully manual, no clamp
    caps = mold_caps(context)
    if not caps:
        return
    cap = caps["half_min"] * C.THROAT_CAP
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
    'SILICONE': 1.10,      # casting silicone (platinum ~1.07, tin RTV ~1.2)
    'URETHANE': 1.05,      # Smooth-On Smooth-Cast urethane resin
    'EPOXY': 1.15,         # generic epoxy casting resin
    'POLYESTER': 1.10,     # polyester casting resin
    'PLASTER': 1.80,       # plaster of Paris / gypsum
    'WAX': 0.90,           # casting / candle wax
    'CONCRETE': 2.40,      # cement / GFRC
}


_PRINTER_HEIGHTS = {
    # Z build height in mm, from the manufacturers' published build volumes.
    # Anycubic
    'PHOTON_MONO4K': 165.0, 'PHOTON_MONOX': 245.0, 'PHOTON_M3': 180.0,
    'PHOTON_M3PLUS': 245.0, 'PHOTON_M3MAX': 300.0, 'PHOTON_M5S': 200.0,
    'PHOTON_M7': 230.0, 'PHOTON_M7PRO': 230.0, 'PHOTON_M7MAX': 300.0,
    # Elegoo
    'MARS3': 175.0, 'MARS_ULTRA': 165.0, 'SATURN2': 250.0, 'SATURN3ULTRA': 260.0,
    'SATURN4': 220.0, 'JUPITER': 300.0,
    # Phrozen
    'PHROZEN_MINI8KS': 180.0, 'PHROZEN_MIGHTY': 235.0, 'PHROZEN_MEGA8KS': 300.0,
    'PHROZEN_MEGA8K': 400.0,
    # Creality, UniFormation, Formlabs, Peopoly
    'HALOT_ONE': 160.0, 'HALOT_MAGE': 230.0, 'GKTWO': 245.0, 'GK3ULTRA': 300.0,
    'FORM4': 210.0, 'PHENOM_FORGE': 350.0,
    # FDM
    'ENDER3': 250.0, 'PRUSA_MK3S': 210.0, 'BAMBU_A1MINI': 180.0, 'BAMBU_X1P1': 256.0,
}


def _apply_printer_preset(self, context):
    h = _PRINTER_HEIGHTS.get(self.printer_preset)
    if h is not None:
        self.max_print_height = h


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
            ('STAMP', "Stamp from SVG / Text",
             "Make a real silicone INK STAMP from artwork: the design (an SVG "
             "file, or the selected Text/Curve object) is engraved into the pan "
             "floor. Pour silicone, cure, peel: the slab carries the design "
             "raised and mirrored, so stamped imprints read correctly - glue it "
             "to an acrylic block"),
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
                        "that fits — unless Oversized Throat is on, which uses "
                        "exactly what you type. The panel shows the funnel being built",
                        mn=0.3, soft=60.0, update=_clamp_sprue_radius)
    big_throat: BoolProperty(
        name="Oversized Throat",
        description="Fully manual throat: use EXACTLY the typed Throat Radius, with "
                    "no auto-fit cap at all (normally it's capped at ≈30% of the mold "
                    "half-width). A very wide throat leaves little shell around the "
                    "hole — the panel warns, and you own the result",
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
            ('LOCK', "Locking Base",
             "(Pour Box) Unite a sawtooth base plinth into the high-poly positive; the "
             "printed shells get a matching socket that hugs the plinth with a tolerance "
             "fit, so they lock onto the base and can't slip. Bottom prints open"),
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
    plate_screw: BoolProperty(
        name="Screw Master to Plate",
        description="Hold the printed master on the key plate with one screw instead "
                    "of glue: a hole through the plate (countersunk from below, so "
                    "the plate still sits flat) and a hole in the master's underside "
                    "for a heat-set threaded insert. It goes in the middle of the "
                    "base, or at the nearest spot with enough master around the "
                    "insert. The plate is thickened to fit the countersink",
        default=False,
    )
    screw_diameter: _dist("Screw Hole", 3.4,
                          "Hole through the key plate for the screw (3.4 for M3, "
                          "4.5 for M4)", mn=1.0, soft=10.0)
    screw_countersink: BoolProperty(
        name="Countersink",
        description="Countersink the plate's underside for a flat-head screw, so the "
                    "head sits flush and the plate stays flat on the table. Untick "
                    "for a plain hole",
        default=True,
    )
    screw_head_diameter: _dist("Head Diameter", 6.5,
                               "Countersink diameter at the plate's underside, for "
                               "the screw head (about 6.5 for M3, 8.5 for M4); the "
                               "cone is 90 degrees", mn=1.0, soft=16.0)
    insert_diameter: _dist("Insert Hole", 4.0,
                           "Hole in the master's underside for the heat-set threaded "
                           "insert (about 4.0 for M3 inserts, 5.6 for M4 - check "
                           "your insert's datasheet)", mn=1.0, soft=12.0)
    insert_depth: _dist("Insert Depth", 6.5,
                        "How deep the insert hole goes into the master: the "
                        "insert's length plus about 1 mm", mn=1.0, soft=25.0)
    suction_cup: BoolProperty(
        name="Suction Cup Former",
        description="Also print a suction-cup former (MF_Mold_Cup): a smooth high-poly "
                    "dome on a plate with four legs that seat over the open bottom "
                    "(Open Bottom, with or without the key plate, or Locking Base - "
                    "the former follows the base's actual height and width), tabs "
                    "hugging the outer wall. "
                    "Cast with the mold inverted, fill, press the former in - the "
                    "material cures around the dome, leaving a suction-cup bell in "
                    "the cast's base. Pop the former out after cure",
        default=False,
    )
    cup_diameter: _dist("Cup Diameter", 0.0,
                        "Dome diameter of the suction-cup former. 0 = automatic (about "
                        "70% of the model's base opening). An explicit size is used as "
                        "typed - it may be wider than the cast opening (the bell then "
                        "truncates at the opening) and is capped only by what fits in "
                        "through the shells' bottom. On a Locking Base that limit is "
                        "the sawtooth socket: raise Base Margin for a wider bell",
                        mn=0.0, soft=120.0)
    cup_depth: _dist("Cup Depth", 8.0,
                     "How deep the former's dome presses into the pour - the bell "
                     "depth of the finished suction cup", mn=1.0, soft=30.0)
    cup_lock: EnumProperty(
        name="Fastening",
        description="How a former locks down onto the shells - the pour FLOATS it "
                    "(buoyancy), so it must be held. Used by the suction-cup former, "
                    "and on an Open Bottom by the Vac-U-Lock plug too",
        items=[
            ('PIN', "Pin-Lock (resin-safe)",
             "No flex needed at all: the former slides on freely, then a ~2 mm pin "
             "(bamboo skewer, 1.75 mm filament, nail) slides through each hook's "
             "channel into the shell groove - pure shear, safe for brittle resin"),
            ('SNAP', "Snap-Lock (flexible filament)",
             "Printed beads click into grooves in the shells - quick and tool-free, "
             "for filaments that flex (PLA/PETG/ABS). NOT for brittle resin"),
            ('BAND', "Band Cleats",
             "Lips on the hooks catch rubber bands stretched over the former - "
             "nothing is cut into the shells at all"),
        ],
        default='PIN',
    )
    fit_clearance: _dist("Fit Clearance", 0.2,
                         "Gap PER FACE between mating printed parts (the key "
                         "plate's groove vs the shell's tongue, and the model "
                         "pocket). Increase if your prints come out too tight to "
                         "assemble", mn=0.0, soft=1.0)
    flange_width: _dist("Flange Width", 6.0,
                        "How far the base flange extends past the mold")

    # --- Locking base (sawtooth plinth) --------------------------------- #
    lock_height: _dist("Base Height", 10.0,
                       "How tall the sawtooth base plinth is, below the model's base",
                       mn=1.0)
    lock_margin: _dist("Base Margin", 4.0,
                       "How far the plinth extends past the model's footprint (a lip)",
                       mn=0.0)
    lock_teeth: IntProperty(
        name="Sawtooth Teeth",
        description="Number of sawtooth ridges up the plinth's side (the anti-slip "
                    "zigzag the shells socket onto)",
        default=3, min=1, max=12,
    )
    lock_tooth_depth: _dist("Tooth Depth", 2.0,
                            "How far each sawtooth ridge sticks out", mn=0.2)
    lock_tolerance: _dist("Lock Tolerance", 0.2,
                          "Clearance between the printed shell socket and the base, so "
                          "the shells slide on and lock without binding (per face)",
                          mn=0.0, soft=1.0)
    lock_unite: BoolProperty(
        name="Unite Base with Model",
        description="Join the sawtooth base into the positive so master + base are one "
                    "piece. Untick to keep the base as a separate MF_Mold_Base part "
                    "(exported with the shells) and leave your model untouched - e.g. to "
                    "print the base on its own and attach the master to it",
        default=True,
    )

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
    wing_keys: EnumProperty(
        name="Wing Alignment",
        description="Alignment keys on the wing mating faces: a raised key on one "
                    "half seats into a matching socket in the other (grown by Fit "
                    "Clearance), so the bolted halves can't shear. Placed between "
                    "the bolt holes and sized to the wing lip",
        items=[
            ('NONE', "None", "No alignment keys on the wings (the bolts alone align)"),
            ('CONE', "Cone", "Pointed cone pins - self-centering, easiest to seat"),
            ('DOME', "Half Sphere", "Dome bumps - smooth engage and release"),
            ('FRUSTUM', "Half Cone", "Truncated cone pads - sturdy, shear-resistant"),
        ],
        default='NONE',
    )
    wing_key_size: _dist("Key Size", 6.0,
                         "Diameter of the wing alignment keys (their footprint on the "
                         "wing face)",
                         mn=1.0, soft=20.0)
    wing_key_height: _dist("Key Height", 0.0,
                           "How far the keys stand out past the mating face. 0 = "
                           "automatic: proportional to Key Size but capped by the wing "
                           "lip so the socket never pierces the wing. Set it higher for "
                           "taller keys - past the lip the socket punches through the "
                           "mating wing as a hole (still aligns and prints fine). Half "
                           "Sphere caps at a full hemisphere so it can always assemble",
                           mn=0.0, soft=12.0)
    wing_key_spacing: _dist("Key Spacing", 40.0,
                            "Pitch between wing alignment keys along the seam - keys "
                            "are spread evenly at this distance (a tall mold gets a "
                            "whole row), dodging the bolt holes",
                            mn=5.0, soft=120.0)
    wing_band_notches: BoolProperty(
        name="Band Notches",
        description="Notches across the clamp wings' outer edges every 30 mm, for "
                    "rubber bands: a band stretched round the mold sits in a notch on "
                    "each wing and cannot slide off. For clamping with bands instead "
                    "of bolts",
        default=False,
    )
    bolt_diameter: _dist("Bolt Diameter", 3.0,
                         "Diameter of the clamp/flange bolt holes", mn=0.5)
    bolt_auto: BoolProperty(
        name="Auto Bolts",
        description="Place the clamp bolt holes automatically by flange height; "
                    "off by default - the wings clamp with clips or bands unless "
                    "you ask for holes here or set a count per side",
        default=False,
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
    stamp_svg: StringProperty(
        name="SVG File", subtype='FILE_PATH', default="",
        description="Artwork for the stamp. Used when no Text/Curve object is "
                    "selected; shapes must be FILLED paths (convert strokes to "
                    "paths in Inkscape first)")
    stamp_width: FloatProperty(
        name="Stamp Width", default=60.0, min=5.0, soft_max=200.0,
        description="The design is scaled (uniformly) to this width (mm)")
    stamp_relief: FloatProperty(
        name="Relief Depth", default=2.0, min=0.5, soft_max=5.0,
        description="How far the design stands proud of the stamp face (mm). "
                    "1.5-2.5 is the sweet spot: shallower smears ink from the "
                    "background, deeper makes fine lines floppy")
    stamp_mirror: BoolProperty(
        name="Mirror Design", default=False,
        description="Flip the design left-right. Leave OFF for a normal ink "
                    "stamp (the pour mirrors it once and stamping mirrors it "
                    "back, so imprints read correctly). Turn ON only if you "
                    "want the STAMP FACE itself to read correctly")
    split_offset: FloatProperty(
        name="Parting Offset", default=0.0,
        description="Slide the parting plane off-centre along the split axis, in mm "
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
        name="Seam Height", default=0.0,
        description="Slide the horizontal seam up/down from mid-height, in mm "
                    "(auto-clamped so neither stack vanishes)")
    dual_density: BoolProperty(
        name="Dual Density (2-pour)", default=False,
        description="Firm-core / soft-shell casting kit (Locking Base or Open "
                    "Bottom). Adds Core_Master: your model shrunk INWARD by Soft "
                    "Wall. On a Locking Base it rides a bare + SOCKET CROSS carved "
                    "from the same sawtooth plinth as the positive - the lips ARE "
                    "the teeth; on an Open Bottom it hangs from four legs that rest "
                    "on the shells' bottom rim, hooks centring it. Mold it in a "
                    "SECOND run (that run keeps this mold), cast it FIRM, then pour "
                    "INVERTED: fill the open base with SOFT and seat the core - "
                    "excess burps out between the arms; the layers bond as they "
                    "cure")
    core_wall: FloatProperty(
        name="Soft Wall", default=5.0, min=0.5, soft_max=20.0,
        description="Thickness of the SOFT outer layer (mm). The firm core is "
                    "the model shrunk INWARD by exactly this much everywhere - "
                    "a true offset, so the soft layer is even on the inside of "
                    "a bend, the outside, the top and the flanks alike")
    anchor_plug: BoolProperty(
        name="Vac-U-Lock Plug", default=False,
        description="Also print MF_Mold_Plug: the bundled Vac-U-Lock + "
                    "suction-bell former, ALWAYS at its original size. On a "
                    "Locking Base it stands on its own socket cross (sawtooth "
                    "lips clamp into the shells' grooves); on an Open Bottom on "
                    "four legs resting on the shells' rim, fastened like the "
                    "suction-cup former. Cast inverted like the dual core - fill "
                    "the base, seat it - and the cured toy's base carries the "
                    "gripping channel inside a suction bell; if the mold is too "
                    "small for the real plug it is skipped with a note")
    printer_fit: BoolProperty(
        name="Printer Fit",
        description="Split anything taller than your printer's build height into "
                    "pieces that fit the plate: shells into bolted stacked levels, "
                    "the positive into glue-up sections that self-align with "
                    "printed pegs. Pick your printer or type its height below",
        default=False,
    )
    printer_preset: EnumProperty(
        name="Printer",
        description="Pick your printer to fill in its build height, or Custom to "
                    "type it yourself. Double-check the number against your "
                    "machine - editions vary",
        items=[
            ('CUSTOM', "Custom", "Type the maximum print height yourself "
                                 "(0 = Printer Fit off)"),
            None,
            ('PHOTON_M7', "Anycubic Photon Mono M7 (230 mm)", "Anycubic Photon Mono M7"),
            ('PHOTON_M7PRO', "Anycubic Photon Mono M7 Pro (230 mm)",
             "Anycubic Photon Mono M7 Pro"),
            ('PHOTON_M7MAX', "Anycubic Photon Mono M7 Max (300 mm)",
             "Anycubic Photon Mono M7 Max"),
            ('PHOTON_M5S', "Anycubic Photon Mono M5s / M5s Pro (200 mm)",
             "Anycubic Photon Mono M5s / M5s Pro"),
            ('PHOTON_M3', "Anycubic Photon M3 (180 mm)", "Anycubic Photon M3"),
            ('PHOTON_M3PLUS', "Anycubic Photon M3 Plus (245 mm)", "Anycubic Photon M3 Plus"),
            ('PHOTON_M3MAX', "Anycubic Photon M3 Max (300 mm)", "Anycubic Photon M3 Max"),
            ('PHOTON_MONOX', "Anycubic Photon Mono X / X 6K (245 mm)",
             "Anycubic Photon Mono X / X 6K"),
            ('PHOTON_MONO4K', "Anycubic Photon Mono 4K (165 mm)", "Anycubic Photon Mono 4K"),
            None,
            ('MARS3', "Elegoo Mars 3 / 4 (175 mm)", "Elegoo Mars 3 / Mars 4"),
            ('MARS_ULTRA', "Elegoo Mars 4 Ultra / 5 Ultra (165 mm)",
             "Elegoo Mars 4 Ultra / Mars 5 Ultra"),
            ('SATURN2', "Elegoo Saturn 2 / 3 (250 mm)", "Elegoo Saturn 2 / Saturn 3"),
            ('SATURN3ULTRA', "Elegoo Saturn 3 Ultra (260 mm)", "Elegoo Saturn 3 Ultra"),
            ('SATURN4', "Elegoo Saturn 4 / 4 Ultra (220 mm)", "Elegoo Saturn 4 / Saturn 4 Ultra"),
            ('JUPITER', "Elegoo Jupiter / Jupiter SE (300 mm)", "Elegoo Jupiter / Jupiter SE"),
            None,
            ('PHROZEN_MINI8KS', "Phrozen Sonic Mini 8K S (180 mm)", "Phrozen Sonic Mini 8K S"),
            ('PHROZEN_MIGHTY', "Phrozen Sonic Mighty 8K / 12K (235 mm)",
             "Phrozen Sonic Mighty 8K / Mighty 12K"),
            ('PHROZEN_MEGA8KS', "Phrozen Sonic Mega 8K S (300 mm)", "Phrozen Sonic Mega 8K S"),
            ('PHROZEN_MEGA8K', "Phrozen Sonic Mega 8K (400 mm)", "Phrozen Sonic Mega 8K"),
            None,
            ('HALOT_ONE', "Creality Halot-One (160 mm)", "Creality Halot-One"),
            ('HALOT_MAGE', "Creality Halot-Mage / Mage Pro (230 mm)",
             "Creality Halot-Mage / Halot-Mage Pro / Mage S"),
            ('GKTWO', "UniFormation GKtwo (245 mm)", "UniFormation GKtwo"),
            ('GK3ULTRA', "UniFormation GK3 Ultra (300 mm)", "UniFormation GK3 Ultra"),
            ('FORM4', "Formlabs Form 4 (210 mm)", "Formlabs Form 4"),
            ('PHENOM_FORGE', "Peopoly Phenom Forge (350 mm)", "Peopoly Phenom Forge"),
            None,
            ('ENDER3', "Ender-3 family (250 mm)", "Creality Ender-3 / V2 / S1"),
            ('PRUSA_MK3S', "Prusa MK3S/MK4 (210 mm)", "Original Prusa i3 MK3S / MK4"),
            ('BAMBU_A1MINI', "Bambu A1 mini (180 mm)", "Bambu Lab A1 mini"),
            ('BAMBU_X1P1', "Bambu X1/P1 (256 mm)", "Bambu Lab X1 / P1 series"),
        ],
        default='CUSTOM',
        update=_apply_printer_preset,
    )
    max_print_height: FloatProperty(
        name="Max Print Height", default=0.0, min=0.0, soft_max=400.0,
        description="Printer Fit: your printer's maximum print height in mm "
                    "(0 = off). Any shell taller than this is cut into stacked "
                    "levels - each with a bolted seam ring all around - and a "
                    "too-tall positive into glue-up sections that self-align with "
                    "printed pegs (nothing on its cast surface), so every piece "
                    "fits the build plate")
    fit_positive: BoolProperty(
        name="Cut Positive Too", default=True,
        description="Printer Fit also cuts a too-tall positive into glue-up "
                    "sections that self-align: pegs printed on each face seat "
                    "into sockets in the next (nothing is ever added to its cast "
                    "surface). Untick to always keep the positive whole - print "
                    "it tilted or split it yourself")
    support_clearance: FloatProperty(
        name="Support Height", default=5.0, min=0.0, soft_max=20.0,
        description="Printer Fit: height the slicer's supports/raft lift the "
                    "print off the plate (resin prints rarely sit flat). This is "
                    "reserved out of Max Print Height, so every level still fits "
                    "WITH its supports")
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
    pour_channel: BoolProperty(
        name="Bottom-Fill Channel",
        description="Pour BESIDE the model instead of over it: a straight pipe on the "
                    "parting seam, outside the case, with a gate into the cavity just "
                    "above its floor - the silicone enters at the base and rises evenly "
                    "around the model, so nothing drapes over a complex master and air "
                    "is pushed out ahead of it. The top funnel then works as the riser "
                    "and air escape. Each half carries half the channel, so the cured "
                    "sprue lifts out when the halves come apart",
        default=False,
    )
    channel_radius: _dist("Channel Radius", 4.0,
                          "Radius of the bottom-fill channel's bore - its own size, not "
                          "tied to the silicone thickness", mn=1.0, soft=12.0)
    channel_flip: BoolProperty(
        name="Other Side",
        description="Put the bottom-fill channel on the other end of the parting seam "
                    "(by default it goes where the model reaches out least)",
        default=False,
    )
    sprue_flare: FloatProperty(
        name="Funnel Flare", default=2.4, min=1.0, max=4.0,
        description="Mouth width as a multiple of the sprue radius — 1.0 is a "
                    "straight tube (best when a wide cone won't fit the shape), "
                    "bigger is a wider catch funnel (auto-capped to the mold)",
    )
    big_mouth: BoolProperty(
        name="Oversized Mouth",
        description="Fully manual mouth: exactly throat x Mouth Flare, with no "
                    "auto-fit cap (normally the mouth is capped at ≈45% of the mold "
                    "half-width and kept on the mold edge). It may then overhang the "
                    "mold — the panel warns. Use when a wide catch funnel won't "
                    "otherwise fit the shape",
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
        name="Sprue X", default=0.0,
        description="Manual funnel X offset from the model's footprint centre, in mm "
                    "(0 = centre); used when Sprue Placement is Manual. Clamped to "
                    "the footprint so the funnel stays on the model")
    sprue_y: FloatProperty(
        name="Sprue Y", default=0.0,
        description="Manual funnel Y offset from the model's footprint centre, in mm "
                    "(0 = centre); used when Sprue Placement is Manual. Clamped to "
                    "the footprint so the funnel stays on the model")
    vent_place: EnumProperty(
        name="Vent Placement",
        description="Where the air vents go",
        items=[
            ('AUTO', "Auto (high points)",
             "Drill vents at the model's highest points, spaced apart - where "
             "air actually traps"),
            ('MARKERS', "Manual (markers)",
             "Drill ONE vent at every Vent Marker you placed. Snap the 3D "
             "cursor onto the surface (Shift+Right-Click), press Add Vent "
             "Marker, then move/duplicate/delete the markers freely"),
        ],
        default='AUTO',
    )
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
            ('SILICONE', "Silicone", "Casting silicone ≈ 1.10 (platinum ~1.07, tin ~1.2)"),
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

    # --- Stand legs (casting in the pour box) ---------------------------- #
    stand_legs: BoolProperty(
        name="Stand Legs",
        description="Cast in the pour box itself: after the silicone cures, turn the "
                    "whole mold over onto the pour funnel's mouth, which carries the "
                    "weight, and pour the cast into the silicone's open base. Short "
                    "thin legs from the top of the shell end in feet level with the "
                    "mouth and keep it from tipping (Pour Box with an Open Bottom or "
                    "Locking Base)",
        default=False,
    )
    stand_tilt: FloatProperty(
        name="Stand Tilt", default=0.0, min=0.0, max=30.0,
        description="Cut the legs' feet at this angle (degrees), so the turned-over "
                    "mold stands tilted and air rises out of one side of the cast as "
                    "you pour. It leans to the side that needs the least leg, and the "
                    "feet on the low side move out to keep it from tipping. 0 = level")

    # --- Label ---------------------------------------------------------- #
    label_on: BoolProperty(
        name="Label",
        description="A label on one shell: the silicone and cast weights, the "
                    "model's name, the date - whatever you type. It sits on a small "
                    "framed plaque with rounded corners on the flattest spot of the "
                    "shell's outside",
        default=False,
    )
    label_text: StringProperty(
        name="Label Text",
        description="What the label says. Fields: {silicone_g} {silicone_ml} "
                    "{cast_g} {cast_ml} {name} {date}. Use | for a new line",
        default="{silicone_g} g silicone|{cast_g} g cast",
    )
    label_size: _dist("Letter Height", 5.0,
                      "Height of the label's capital letters; made smaller (down to "
                      "3 mm) when the shell has no flat spot big enough",
                      mn=2.0, soft=15.0)
    label_depth: _dist("Label Depth", 0.6,
                       "How deep the letters are cut into the plaque, or how far "
                       "they stand out of it when raised", mn=0.2, soft=2.0)
    label_style: EnumProperty(
        name="Label Style",
        description="How the letters are made",
        items=[
            ('CUT', "Cut in", "Letters (and the frame line) are cut into the "
                              "plaque: crisp, and nothing to snap off"),
            ('RAISED', "Raised", "Letters (and the frame line) stand out of the "
                                 "plaque"),
        ],
        default='CUT',
    )

    # --- Export --------------------------------------------------------- #
    export_after: BoolProperty(
        name="Export after generate", default=False,
        description="After generating, write every print as STL into the Export "
                    "Folder: the mold shells, base/former parts and the positive")
    export_dir: StringProperty(name="Export Folder", subtype='DIR_PATH', default="//")

    # --- The model the last Generate built from (Regenerate uses it) ------- #
    last_master: StringProperty(
        name="Last Model", default="", options={'HIDDEN'},
        description="The model the last Generate built from - Regenerate rebuilds "
                    "from it even while it is hidden")

    # --- Results (read-only display) ------------------------------------ #
    last_cavity_volume: FloatProperty(name="Cavity Volume", default=0.0)
    last_silicone_volume: FloatProperty(name="Silicone Volume", default=0.0)
    last_plastic_volume: FloatProperty(name="Box Plastic Volume", default=0.0)
