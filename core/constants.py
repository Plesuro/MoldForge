"""Tunable geometry constants for MoldForge, gathered in one place.

These were scattered across the builder, the live UI caps and the tests; keeping
them here keeps the funnel/wing/voxel heuristics consistent and easy to tune.

Cap fractions are of the mold's HALF-WIDTH (half the smaller X/Y footprint plus the
wall offset) unless noted.
"""

# --- Funnel size caps (fraction of the mold half-width) -------------------- #
THROAT_CAP = 0.30          # max throat radius
THROAT_CAP_BIG = 0.45      # ... with Oversized Throat on (UI warns)
MOUTH_CAP = 0.45           # max mouth radius
MOUTH_CAP_BIG = 1.5        # ... with Oversized Mouth on (may overhang; UI warns)
VENT_CAP = 0.16            # max vent radius

# --- Funnel neck shaping --------------------------------------------------- #
NECK_TAPER = 0.25          # neck cone bottom radius as a fraction of its top
FUNNEL_LOCAL_DROP = 2.5    # ignore model hits deeper than this * footprint below the
                           # local peak (a curved model's distant lower body), so the
                           # spout drops only to the surface it actually sits on

# --- Cleanup voxel remesh -------------------------------------------------- #
HEAVY_FACES = 250_000      # above this a model is voxel-remeshed (heavy = slow booleans)
DIRECT_VOXEL_DIV = 200.0   # direct-mold cavity voxel = model characteristic size / this
DIRECT_VOXEL_MIN = 0.3
DIRECT_VOXEL_MAX = 1.2
JACKET_VOXEL_FACTOR = 0.4  # pour-box cleanup voxel = this * wall offset (coarse is fine)

# --- Clamp flange (wings) -------------------------------------------------- #
WING_INNER = 0.35              # rind inner surface reaches this * offset into the body
FLANGE_COARSEN_FACES = 40_000  # above this, the wing rind is built from a coarse copy

# --- Tray / open-pour mold (flat & relief objects) ------------------------- #
TRAY_WELD_OVERLAP = 0.5    # min depth the embedded master sinks into the floor (a clean
                           # weld; also stops silicone seeping under it)
TRAY_FLAT_RATIO = 0.6      # warn when the thinnest extent exceeds this * the largest
                           # (the object isn't really flat — a wrap-around type fits better)
