"""Headless registration test.

The Advanced panel is a parent with seven sub-panels. A child registered before
its parent kills the whole add-on at load time, and Blender's UI is not drawn
headlessly - so this asserts the class ORDER, drives every panel's draw logic
with a stand-in layout, and checks register()/unregister() round-trip cleanly.

Run:  blender --background --python-exit-code 1 --python tests/test_register.py
"""

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

import moldforge                                    # noqa: E402
from moldforge import panel                         # noqa: E402

_fails = []


def check(cond, label):
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        _fails.append(label)


def main():
    print("\n=== Registration ===")

    moldforge.register()
    check(hasattr(bpy.types.Scene, "moldforge"),
          "register() attaches Scene.moldforge")
    check(bpy.context.scene.moldforge is not None,
          "the property group is reachable on the scene")

    names = [c.__name__ for c in moldforge._classes]
    for cls in moldforge._classes:
        parent = getattr(cls, "bl_parent_id", None)
        if not parent:
            continue
        pname = next((c.__name__ for c in moldforge._classes
                      if getattr(c, "bl_idname", None) == parent), None)
        check(pname is not None and names.index(pname) < names.index(cls.__name__),
              f"{cls.__name__} registers after its parent {parent}")

    for cls in panel.PANEL_CLASSES:
        check(hasattr(bpy.types, cls.__name__),
              f"{cls.__name__} is registered with Blender")
    props = bpy.context.scene.moldforge

    # Every property name the panel draws must exist: a typo in layout.prop()
    # is a runtime error the moment the sidebar is opened, which no headless
    # test would otherwise reach.
    import re
    src = open(os.path.join(os.path.dirname(moldforge.__file__), "panel.py")).read()
    drawn = sorted(set(re.findall(r'prop\(props,\s*"([a-z_0-9]+)"', src)))
    bad = [n for n in drawn if not hasattr(props, n)]
    check(not bad, f"every property drawn by the panel exists "
                   f"({len(drawn)} drawn, unknown: {bad})")

    # The header summaries must survive every mold type / bottom combination
    # and stay short enough not to push the panel wider.
    long_ones, crashed = [], []
    for bs in ('POUR_BOX', 'SOLID', 'TRAY'):
        for base in ('FLAT', 'OPEN', 'FOLLOW', 'LOCK'):
            for extra in ({}, {"dual_density": True, "skin_keys": True,
                               "base_flange": True, "base_plate": True,
                               "suction_cup": True, "split_horizontal": True,
                               "printer_fit": True, "big_throat": True,
                               "vent_place": 'MARKERS', "parts_count": 3}):
                props.box_style, props.base_style = bs, base
                for k, v in extra.items():
                    setattr(props, k, v)
                try:
                    out = [panel.summary_shell(props), panel.summary_parting(props),
                           panel.summary_wings(props), panel.summary_pour(props),
                           panel.summary_printer(props), panel.summary_export(props, 1.0)]
                except Exception as exc:                     # noqa: BLE001
                    crashed.append(f"{bs}/{base}: {exc}")
                    continue
                long_ones += [t for t in out if len(t) > 28]
    check(not crashed, f"summaries never crash across mold types ({crashed[:2]})")
    check(not long_ones, f"summaries stay short (over-long: {long_ones[:3]})")

    # The panels' draw logic (every branch of it) must run without a UI: drive
    # each panel with a stand-in layout that accepts any call, across every mold
    # type, bottom and selection state. This catches a misspelled layout call, a
    # property read that raises, or a format string that fails on a value, which
    # headless tests otherwise never reach because panels only draw on screen.
    class FakeLayout:
        def __getattr__(self, name):
            return lambda *a, **k: FakeLayout()

        def __setattr__(self, name, value):
            pass

    class FakePanel:
        """Stands in for the Blender-made panel instance: a fake layout plus the
        class's own static helpers (``_summary``, ``_generate`` ...)."""
        def __init__(self, cls):
            self.layout = FakeLayout()
            self._cls = cls

        def __getattr__(self, name):
            import inspect
            import types as _types
            raw = inspect.getattr_static(self._cls, name)
            attr = getattr(self._cls, name)
            if isinstance(raw, (staticmethod, classmethod)):
                return attr
            if inspect.isfunction(attr):          # plain method: bind to the fake
                return _types.MethodType(attr, self)
            return attr

    draw_errors = []
    bpy.ops.wm.read_factory_settings(use_empty=True)   # add-on stays registered
    props = bpy.context.scene.moldforge
    mesh = bpy.data.meshes.new("m")
    model = bpy.data.objects.new("Toy", mesh)
    bpy.context.scene.collection.objects.link(model)
    part = bpy.data.objects.new("MF_Mold_A", bpy.data.meshes.new("p"))
    bpy.context.scene.collection.objects.link(part)
    for active in (None, model, part):
        bpy.context.view_layer.objects.active = active
        for bs in ('POUR_BOX', 'SOLID', 'TRAY'):
            for base in ('FLAT', 'OPEN', 'FOLLOW', 'LOCK'):
                for extra in ({}, {"dual_density": True, "anchor_plug": True,
                                   "printer_fit": True, "sprue": False,
                                   "parts_count": 3, "tray_mode": 'STAMP',
                                   "vent_place": 'MARKERS', "wing_keys": 'CONE',
                                   "split_horizontal": True, "sprue_place": 'MANUAL',
                                   "pour_channel": True, "label_on": True,
                                   "stand_legs": True, "wing_band_notches": True,
                                   "base_plate": True, "plate_screw": True,
                                   "suction_cup": True}):
                    props.box_style, props.base_style = bs, base
                    for k, v in extra.items():
                        setattr(props, k, v)
                    for cls in panel.PANEL_CLASSES:
                        for meth in ("draw", "draw_header", "draw_header_preset"):
                            fn = getattr(cls, meth, None)
                            if fn is None or not callable(fn):
                                continue
                            try:
                                fn(FakePanel(cls), bpy.context)
                            except Exception as exc:              # noqa: BLE001
                                draw_errors.append(f"{cls.__name__}.{meth} "
                                                   f"[{bs}/{base}/{active}]: {exc}")
                        poll = getattr(cls, "poll", None)
                        if poll is not None:
                            try:
                                poll(bpy.context)
                            except Exception as exc:              # noqa: BLE001
                                draw_errors.append(f"{cls.__name__}.poll: {exc}")
    check(not draw_errors,
          f"every panel draws in every state without error ({draw_errors[:3]})")
    ok, text, _icon = panel._model_status(bpy.context, props)
    bpy.context.view_layer.objects.active = model
    props.box_style = 'POUR_BOX'
    ok2, text2, _icon2 = panel._model_status(bpy.context, props)
    check(ok2 and "Toy" in text2 and "mm" in text2,
          f"the model line names the selected model with its size ({text2!r})")
    bpy.context.view_layer.objects.active = part
    ok3, text3, _icon3 = panel._model_status(bpy.context, props)
    check(not ok3 and "mold part" in text3,
          f"selecting a generated part is called out ({text3!r})")

    # --- Saved settings: save, reset, load round-trip into a scratch folder.
    print("\n=== Saved settings ===")
    import json
    import tempfile
    from moldforge import saved_settings as ss
    os.environ["MOLDFORGE_PRESET_DIR"] = tempfile.mkdtemp(prefix="mf_presets_")
    props = bpy.context.scene.moldforge
    props.box_style, props.base_style = 'POUR_BOX', 'OPEN'
    props.wall_thickness, props.core_wall = 5.5, 7.0
    props.wings, props.cup_lock = False, 'BAND'
    props.printer_fit, props.printer_preset = True, 'CUSTOM'
    props.max_print_height = 187.0        # typed by hand: must survive the load
    props.silicone_preset = 'DRAGONSKIN'
    props.silicone_density = 1.09         # typed after picking the material
    props.export_dir = "/tmp/mf_out/"
    props.base_plate, props.plate_screw = True, True
    props.screw_diameter, props.insert_depth = 3.2, 7.5
    before = ss.settings_dict(props)
    check(len(before) > 80 and "last_silicone_volume" not in before
          and "stamp_svg" not in before,
          f"a save holds every setting and no build figures ({len(before)} settings)")
    res = bpy.ops.moldforge.settings_save(name="Bench setup")
    check(res == {'FINISHED'} and [n for n, _p in ss.list_presets()] == ["Bench setup"],
          f"Save Current Settings writes one named file ({ss.list_presets()})")
    res = bpy.ops.moldforge.settings_reset()
    check(res == {'FINISHED'} and props.wall_thickness == 3.0 and props.wings
          and props.base_style == 'FLAT' and props.max_print_height == 0.0,
          "Reset to Defaults puts every setting back")
    res = bpy.ops.moldforge.settings_load(name="Bench setup")
    after = ss.settings_dict(props)
    diff = sorted(k for k in before if before[k] != after.get(k))
    check(res == {'FINISHED'} and not diff,
          f"loading the saved settings restores every value exactly (differ: {diff})")
    check(props.max_print_height == 187.0 and abs(props.silicone_density - 1.09) < 1e-6,
          "hand-typed values beat the presets that fill them (printer height, density)")
    ss.save_preset(props, "Bench setup")
    check(len(ss.list_presets()) == 1,
          "saving under the same name replaces it instead of adding a copy")
    ss.save_preset(props, "a/b: odd name?")
    check([n for n, _p in ss.list_presets()] == ["a/b: odd name?", "Bench setup"],
          f"any name is kept as typed, sorted ({[n for n, _p in ss.list_presets()]})")
    # A preset from another version: an option value and a setting that do not
    # exist here are skipped, everything else still loads.
    with open(os.path.join(ss.preset_dir(), "old.json"), "w") as f:
        json.dump({"moldforge_settings": 1, "name": "Old version",
                   "settings": {"wall_thickness": 4.25, "printer_preset": "PHOTON_M9",
                                "gone_setting": 3, "wings": False}}, f)
    props.wall_thickness, props.wings = 3.0, True
    applied, skipped = ss.load_preset(props, "Old version")
    check(applied == 2 and sorted(skipped) == ["gone_setting", "printer_preset"]
          and props.wall_thickness == 4.25 and not props.wings,
          f"an outdated preset loads what it can and names the rest ({skipped})")
    with open(os.path.join(ss.preset_dir(), "broken.json"), "w") as f:
        f.write("{ not json")
    check("broken" not in " ".join(n for n, _p in ss.list_presets()),
          "an unreadable file is left out of the menu instead of breaking it")
    res = bpy.ops.moldforge.settings_delete(name="Old version")
    check(res == {'FINISHED'}
          and "Old version" not in [n for n, _p in ss.list_presets()],
          "Delete removes the saved settings")
    menu_errors = []
    for mcls in (ss.MOLDFORGE_MT_settings, ss.MOLDFORGE_MT_settings_delete):
        try:
            mcls.draw(FakePanel(mcls), bpy.context)
        except Exception as exc:                          # noqa: BLE001
            menu_errors.append(f"{mcls.__name__}: {exc}")
    check(not menu_errors, f"the saved-settings menus draw ({menu_errors})")

    # --- Regenerate: rebuild from the remembered model without unhiding or
    # reselecting it (user review: "tweak settings and regenerate right there").
    print("\n=== Regenerate ===")
    import bmesh
    bpy.ops.wm.read_factory_settings(use_empty=True)
    props = bpy.context.scene.moldforge
    bmr = bmesh.new()
    bmesh.ops.create_cube(bmr, size=30.0)
    toy = bpy.data.objects.new("RegenToy", bpy.data.meshes.new("RegenToy"))
    bmr.to_mesh(toy.data); bmr.free()
    bpy.context.scene.collection.objects.link(toy)
    bpy.context.view_layer.objects.active = toy
    props.box_style, props.base_style = 'POUR_BOX', 'FLAT'
    props.wings, props.vent_count = False, 0
    res = bpy.ops.moldforge.generate()
    check(res == {'FINISHED'} and props.last_master == "RegenToy" and toy.hide_get(),
          f"Generate remembers its model and hides it ({props.last_master!r})")
    bpy.context.view_layer.objects.active = None
    _ok0, text0, _ic0 = panel._model_status(bpy.context, props)
    check("Regenerate uses RegenToy" in text0,
          f"with nothing active (the hidden original) the panel offers Regenerate "
          f"({text0!r})")
    pos = bpy.data.objects.get("MF_Positive")
    bpy.context.view_layer.objects.active = pos
    ok_r, text_r, _ic = panel._model_status(bpy.context, props)
    check("Regenerate uses RegenToy" in text_r,
          f"with a mold part selected the panel offers Regenerate ({text_r!r})")
    props.wall_thickness = 5.0
    # As in the viewport after Generate: the original is hidden, and Blender's
    # 3D view then reports no active object at all. Regenerate must not care.
    bpy.context.view_layer.objects.active = None
    res = bpy.ops.moldforge.regenerate()
    shells = [o for o in bpy.data.objects if o.name.startswith("MF_Mold_")]
    check(res == {'FINISHED'} and len(shells) == 2
          and all(o.name in ("MF_Mold_A", "MF_Mold_B") for o in shells),
          f"Regenerate rebuilds from the hidden model and replaces the old parts "
          f"({sorted(o.name for o in shells)})")
    xs = [(o.matrix_world @ v.co).x for o in shells for v in o.data.vertices]
    w30 = max(xs) - min(xs)
    check(w30 > 30.0 + 2 * (5.0 + 2.0) - 1.0,
          f"... with the changed settings (mold {w30:.1f} wide for a 5 mm gap)")
    res = bpy.ops.moldforge.toggle_master()
    check(res == {'FINISHED'} and not toy.hide_get(), "Show Original unhides the model")
    regen_err = []
    for cls in (panel.MOLDFORGE_PT_main, panel.MOLDFORGE_PT_result):
        try:
            cls.draw(FakePanel(cls), bpy.context)
        except Exception as exc:                          # noqa: BLE001
            regen_err.append(f"{cls.__name__}: {exc}")
    check(not regen_err, f"panels draw with a remembered model ({regen_err})")
    bpy.data.objects.remove(toy, do_unlink=True)
    check(bpy.ops.moldforge.regenerate.poll() is False,
          "Regenerate is unavailable once the model is gone")

    moldforge.unregister()
    check(not hasattr(bpy.types.Scene, "moldforge"),
          "unregister() removes Scene.moldforge")
    check(not hasattr(bpy.types, "MOLDFORGE_PT_main"),
          "unregister() removes the panel classes")
    moldforge.register()          # a second round-trip must also work
    moldforge.unregister()
    check(True, "register/unregister survives a second round-trip")

    print("\n" + "=" * 40)
    if _fails:
        print(f"RESULT: {len(_fails)} FAILURE(S)")
        for f in _fails:
            print("  -", f)
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED")


main()
