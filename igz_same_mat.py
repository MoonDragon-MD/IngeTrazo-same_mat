"""Same Mat 1.2.3 — Select and color the faces connected by the same material.            By MoonDragon
"""
# =========================================================================
# Extension: Same Mat — select/paint connected faces (same material)
# Author:  MoonDragon
# Date:    2026-11-15
# Version: 1.2.3
# License: MIT
#
# Menu:     Extensions > Same Mat > …
# Toolbar:  "Same Mat"
# Language: English + Italian
#
# Requires: IngeTrazo extension API 2+
# =========================================================================

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
import traceback

from PySide6.QtCore import Qt, QSize, QLocale
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QToolBar,
    QWidget,
    QWidgetAction,
)

try:
    from core.history import SnapshotImport
except Exception:
    class SnapshotImport:  # type: ignore
        def __init__(self, fn):
            self.fn = fn

try:
    from core.i18n import current_language as _host_language
except Exception:
    def _host_language():
        return None

__version__ = "1.2.3"
MIN_API_VERSION = 2

_APP = None
_TOOLBAR_CREATED = False
_BOTH_SIDES = True
PLUGIN_NAME = "Same Mat"
description = "Select and color the faces connected by the same material"

# -------------------------------------------------------------------------
# i18n — English source keys, Italian translations
# -------------------------------------------------------------------------

_TEXTS = {
    "en": {
        "ext_menu": "Same Mat",
        "act_select": "Select connected",
        "act_paint": "Paint connected…",
        "tb_select": "🔗 Connected",
        "tb_paint": "🎨 Paint",
        "tb_fb": "F+B",
        "tb_select_tip": "Select edge-adjacent faces with the same material",
        "tb_paint_tip": "Paint connected faces (scene material or solid color)",
        "tb_fb_tip": (
            "If checked, material/color is also applied to the back side "
            "(attrs['back']=True). If unchecked, front only."
        ),
        "need_face": "Select at least one face, then try again.",
        "title": "Same Mat",
        "paint_title": "Same Mat — Paint",
        "paint_what": "What should be applied to the connected faces?",
        "opt_material": "Material from scene…",
        "opt_color": "Solid color only…",
        "mat_title": "Same Mat — Material",
        "mat_pick": "Choose material:",
        "color_title": "Same Mat — color",
        "sel_status": "Same Mat: {n} faces selected",
        "paint_mat_status": "Same Mat: {n} faces → «{name}» ({side})",
        "paint_col_status": "Same Mat: {n} faces colored ({side})",
        "side_both": "F+B",
        "side_front": "front only",
        "fb_on": "Same Mat F+B: ON",
        "fb_off": "Same Mat F+B: OFF",
        "menu_select_tip": "Select edge-adjacent faces with the same material",
        "menu_paint_tip": "Paint connected faces with a scene material or color",
    },
    "it": {
        "ext_menu": "Same Mat",
        "act_select": "Seleziona connesse",
        "act_paint": "Colora connesse…",
        "tb_select": "🔗 Connesse",
        "tb_paint": "🎨 Colora",
        "tb_fb": "F+R",
        "tb_select_tip": "Seleziona facce adiacenti con lo stesso materiale",
        "tb_paint_tip": "Colora le facce connesse (materiale della scena o colore)",
        "tb_fb_tip": (
            "Se attivo, il materiale/colore viene applicato anche al retro "
            "(attrs['back']=True). Se disattivo, solo il fronte."
        ),
        "need_face": "Seleziona almeno una faccia, poi riprova.",
        "title": "Same Mat",
        "paint_title": "Same Mat — Colora",
        "paint_what": "Cosa applicare alle facce connesse?",
        "opt_material": "Materiale dalla scena…",
        "opt_color": "Solo colore…",
        "mat_title": "Same Mat — Materiale",
        "mat_pick": "Scegli il materiale:",
        "color_title": "Same Mat — colore",
        "sel_status": "Same Mat: {n} facce selezionate",
        "paint_mat_status": "Same Mat: {n} facce → «{name}» ({side})",
        "paint_col_status": "Same Mat: {n} facce colorate ({side})",
        "side_both": "F+R",
        "side_front": "solo fronte",
        "fb_on": "Same Mat F+R: ON",
        "fb_off": "Same Mat F+R: OFF",
        "menu_select_tip": "Seleziona facce adiacenti con lo stesso materiale",
        "menu_paint_tip": "Colora le facce connesse con un materiale o un colore",
    },
}


def _lang() -> str:
    """Only Italian when UI/locale is Italian; everything else > English."""
    try:
        raw = _host_language()
        if raw:
            s = str(raw).strip().replace("_", "-").lower()
            if s.startswith("it"):
                return "it"
            return "en"
    except Exception:
        pass
    try:
        loc = QLocale.system().name().replace("_", "-").lower()
        if loc.startswith("it"):
            return "it"
    except Exception:
        pass
    return "en"


def _t(key: str, **kw) -> str:
    lang = _lang()
    table = _TEXTS.get(lang) or _TEXTS["en"]
    text = table.get(key) or _TEXTS["en"].get(key) or key
    return text.format(**kw) if kw else text


def _log(msg: str) -> None:
    print(f"[Same Mat] {msg}", flush=True)


def _flash(viewport, text: str, ms: int = 3000) -> None:
    try:
        viewport.flash_status(text, ms)
    except Exception:
        _log(text)


def _viewport():
    return _APP.viewport if _APP is not None else None


def _scene():
    return _APP.scene if _APP is not None else None


# -------------------------------------------------------------------------
# Geometry / materials
# -------------------------------------------------------------------------

def _mat_key(face, eps=1e-4):
    a = getattr(face, "attrs", {}) or {}
    if a.get("mat") is not None:
        return ("mat", a["mat"])
    tex = a.get("texture")
    if isinstance(tex, dict) and tex.get("path"):
        return ("tex", tex["path"])
    col = a.get("color")
    if col is not None:
        r, g, b = col[:3]
        return (
            "col",
            round(r / eps) * eps,
            round(g / eps) * eps,
            round(b / eps) * eps,
        )
    return ("none",)


def _edge_key(va, vb):
    ia, ib = id(va), id(vb)
    return (ia, ib) if ia < ib else (ib, ia)


def _build_adjacency(faces):
    edge_to_faces = defaultdict(list)
    for f in faces:
        loop = list(getattr(f, "loop", ()) or ())
        n = len(loop)
        for i in range(n):
            e = _edge_key(loop[i], loop[(i + 1) % n])
            edge_to_faces[e].append(f)

    adj = defaultdict(set)
    for flist in edge_to_faces.values():
        for i, fa in enumerate(flist):
            for fb in flist[i + 1 :]:
                adj[fa].add(fb)
                adj[fb].add(fa)
    return adj


def _iter_faces(entities):
    for e in entities or []:
        if type(e).__name__ == "Face" or hasattr(e, "loop"):
            yield e


def _selected_faces(scene):
    sel = getattr(scene, "selection", None) or []
    return list(_iter_faces(sel))


def _candidate_meshes(scene):
    out = []
    for name in ("mesh", "loose_mesh"):
        m = getattr(scene, name, None)
        if m is not None and m not in out and getattr(m, "faces", None) is not None:
            out.append(m)
    return out


def _mesh_for_seeds(scene, seeds):
    seed_set = set(seeds)
    for m in _candidate_meshes(scene):
        faces = list(m.faces)
        if seed_set & set(faces):
            return m, faces
    for m in _candidate_meshes(scene):
        return m, list(m.faces)
    return None, []


def _flood_same_material(scene, seeds):
    if not seeds:
        return set()
    _mesh, faces = _mesh_for_seeds(scene, seeds)
    if not faces:
        return set(seeds)

    adj = _build_adjacency(faces)
    target = _mat_key(seeds[0])
    seen = set(seeds)
    q = deque(seeds)
    while q:
        f = q.popleft()
        for nb in adj.get(f, ()):
            if nb not in seen and _mat_key(nb) == target:
                seen.add(nb)
                q.append(nb)
    return seen


def _set_selection(scene, faces) -> None:
    faces = set(faces)
    # Prefer reassignment: may trigger host selection observers
    try:
        scene.selection = faces
        return
    except Exception:
        pass

    sel = getattr(scene, "selection", None)
    if sel is None:
        return
    if hasattr(sel, "clear") and hasattr(sel, "update"):
        sel.clear()
        sel.update(faces)
    elif hasattr(sel, "clear") and hasattr(sel, "add"):
        sel.clear()
        for f in faces:
            sel.add(f)


def _refresh_selection_view(viewport, scene) -> None:
    """Best-effort: make selection dots/highlight redraw."""
    # Prefer public hooks if present
    for name in (
        "selection_changed",
        "notify_selection_changed",
        "update_selection",
        "_update_selection",
        "rebuild_selection",
        "_rebuild_selection",
    ):
        fn = getattr(viewport, name, None)
        if callable(fn):
            try:
                fn()
                break
            except TypeError:
                try:
                    fn(scene.selection)
                    break
                except Exception:
                    pass
            except Exception:
                pass

    try:
        viewport.notify_scene_changed()
    except Exception:
        pass
    try:
        # Force a full repaint of the GL view
        viewport.update()
    except Exception:
        pass
    # Some builds only refresh highlight after a deferred update
    try:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, viewport.update)
    except Exception:
        pass

def _material_stamp(mat) -> dict:
    if hasattr(mat, "face_attrs"):
        return dict(mat.face_attrs())
    out = {}
    name = getattr(mat, "name", None)
    if name:
        out["mat"] = name
    color = getattr(mat, "color", None)
    if color is not None:
        out["color"] = tuple(color)
    texture = getattr(mat, "texture", None)
    if texture is not None:
        out["texture"] = dict(texture)
    opacity = getattr(mat, "opacity", None)
    if opacity is not None:
        out["opacity"] = float(opacity)
    return out


def _apply_stamp_to_face(face, stamp: dict, both_sides: bool) -> None:
    a = getattr(face, "attrs", None)
    if a is None:
        return
    for k in ("color", "texture", "mat", "opacity"):
        a.pop(k, None)
    for k, v in stamp.items():
        if v is None:
            continue
        a[k] = deepcopy(v) if isinstance(v, dict) else v
    if both_sides:
        a["back"] = True
    else:
        a.pop("back", None)


def _apply_color_to_face(face, rgb, both_sides: bool) -> None:
    a = getattr(face, "attrs", None)
    if a is None:
        return
    a.pop("texture", None)
    a.pop("mat", None)
    a["color"] = tuple(rgb[:3])
    if both_sides:
        a["back"] = True
    else:
        a.pop("back", None)


def _parent_widget(vp):
    try:
        w = QApplication.activeWindow()
        if w is not None:
            return w
    except Exception:
        pass
    cand = getattr(vp, "window", None)
    if cand is not None and hasattr(cand, "isWidgetType") and cand.isWidgetType():
        return cand
    return None


def _pick_paint_source(parent, scene):
    materials = getattr(scene, "materials", None) or {}
    names = sorted(materials.keys())

    if names:
        choice, ok = QInputDialog.getItem(
            parent,
            _t("paint_title"),
            _t("paint_what"),
            [_t("opt_material"), _t("opt_color")],
            0,
            False,
        )
        if not ok:
            return None

        if choice == _t("opt_material") or choice.startswith(
            _t("opt_material")[:10]
        ):
            name, ok2 = QInputDialog.getItem(
                parent,
                _t("mat_title"),
                _t("mat_pick"),
                names,
                0,
                False,
            )
            if not ok2 or not name:
                return None
            return ("material", materials[name])

    color = QColorDialog.getColor(
        QColor(180, 180, 180), parent, _t("color_title")
    )
    if not color.isValid():
        return None
    rgb = (color.red() / 255.0, color.green() / 255.0, color.blue() / 255.0)
    return ("color", rgb)


# -------------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------------

def select_connected() -> None:
    vp = _viewport()
    sc = _scene()
    if vp is None or sc is None:
        return

    seeds = _selected_faces(sc)
    if not seeds:
        QMessageBox.information(
            _parent_widget(vp), _t("title"), _t("need_face")
        )
        return

    connected = _flood_same_material(sc, seeds)
    _set_selection(sc, connected)
    _refresh_selection_view(vp, sc)
    _flash(vp, _t("sel_status", n=len(connected)))


def paint_connected() -> None:
    global _BOTH_SIDES
    vp = _viewport()
    sc = _scene()
    if vp is None or sc is None:
        return

    seeds = _selected_faces(sc)
    if not seeds:
        QMessageBox.information(
            _parent_widget(vp), _t("title"), _t("need_face")
        )
        return

    parent = _parent_widget(vp)
    source = _pick_paint_source(parent, sc)
    if source is None:
        return

    both = bool(_BOTH_SIDES)
    connected = list(_flood_same_material(sc, seeds))
    kind, payload = source

    def mutate(_scene):
        if kind == "material":
            stamp = _material_stamp(payload)
            for face in connected:
                _apply_stamp_to_face(face, stamp, both)
        else:
            for face in connected:
                _apply_color_to_face(face, payload, both)

    try:
        vp.history.execute(SnapshotImport(mutate))
    except Exception:
        traceback.print_exc()
        mutate(sc)

    _set_selection(sc, connected)
    _refresh_selection_view(vp, sc)

    side = _t("side_both") if both else _t("side_front")
    if kind == "material":
        label = getattr(payload, "name", "?")
        _flash(
            vp,
            _t("paint_mat_status", n=len(connected), name=label, side=side),
        )
    else:
        _flash(
            vp, _t("paint_col_status", n=len(connected), side=side)
        )


def _on_both_sides_toggled(checked: bool) -> None:
    global _BOTH_SIDES
    _BOTH_SIDES = bool(checked)
    vp = _viewport()
    if vp is not None:
        _flash(vp, _t("fb_on") if _BOTH_SIDES else _t("fb_off"), 1500)


# -------------------------------------------------------------------------
# Toolbar + menu
# -------------------------------------------------------------------------

def _find_main_window(app):
    mw = getattr(app, "window", None) or getattr(app, "main_window", None)
    if mw is not None and hasattr(mw, "addToolBar"):
        return mw
    qapp = QApplication.instance()
    if qapp is not None:
        for w in qapp.topLevelWidgets():
            if hasattr(w, "addToolBar"):
                return w
    return None


def _make_action(parent, text, tip, callback):
    act = QAction(text, parent)
    act.setToolTip(tip)
    act.setStatusTip(tip)
    act.triggered.connect(lambda checked=False: callback())
    return act


def create_toolbar(main_window) -> QToolBar:
    tb = QToolBar(PLUGIN_NAME, main_window)
    tb.setObjectName("igz_same_mat_toolbar")
    tb.setWindowTitle(PLUGIN_NAME)
    tb.setIconSize(QSize(24, 24))

    tb.addAction(
        _make_action(
            main_window,
            _t("tb_select"),
            _t("tb_select_tip"),
            select_connected,
        )
    )
    tb.addAction(
        _make_action(
            main_window,
            _t("tb_paint"),
            _t("tb_paint_tip"),
            paint_connected,
        )
    )

    cb = QCheckBox(_t("tb_fb"))
    cb.setChecked(_BOTH_SIDES)
    cb.setToolTip(_t("tb_fb_tip"))
    cb.toggled.connect(_on_both_sides_toggled)
    wa = QWidgetAction(tb)
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(6, 0, 6, 0)
    lay.addWidget(cb)
    wa.setDefaultWidget(wrap)
    tb.addAction(wa)
    tb._same_mat_fb = cb  # type: ignore[attr-defined]

    return tb


def setup(app) -> None:
    global _APP, _TOOLBAR_CREATED

    api = getattr(app, "api_version", None)
    if not isinstance(api, int) or api < MIN_API_VERSION:
        _log(f"API {api} < {MIN_API_VERSION}; abort")
        return

    _APP = app
    _log(f"language={_lang()}")

    try:
        sub = app.add_menu(_t("ext_menu"))
        if sub is not None:
            sub.addAction(_t("act_select"), select_connected)
            sub.addAction(_t("act_paint"), paint_connected)
        else:
            app.add_menu_action(
                f"Same Mat: {_t('act_select')}",
                select_connected,
                tip=_t("menu_select_tip"),
            )
            app.add_menu_action(
                f"Same Mat: {_t('act_paint')}",
                paint_connected,
                tip=_t("menu_paint_tip"),
            )
    except Exception:
        traceback.print_exc()

    if not _TOOLBAR_CREATED:
        try:
            mw = _find_main_window(app)
            if mw is not None:
                tb = create_toolbar(mw)
                mw.addToolBar(Qt.TopToolBarArea, tb)
                _TOOLBAR_CREATED = True
                try:
                    app.restore_toolbar_layout()
                except Exception:
                    pass
            else:
                _log("main window not found; toolbar skipped")
        except Exception:
            traceback.print_exc()

    _log(f"setup ok v{__version__}")