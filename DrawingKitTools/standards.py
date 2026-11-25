# SPDX-License-Identifier: LGPL-2.1-or-later
"""One drawing standard for the whole kit: ISO or ASME.

By default DrawingKit follows TechDraw's own dimension standard preference
(ISO Oriented/Referencing -> ISO, ASME Inlined/Referencing -> ASME), so
it never mixes conventions with TechDraw. The Drawing standard button sets
it explicitly. What follows it:

* section and detail captions: ASME "SECTION A-A", "DETAIL B SCALE 2 : 1";
  ISO "A-A", "B (2 : 1)"
* new sheets: ISO first-angle, ASME third-angle projection (when set
  explicitly; on Auto, TechDraw's own projection preference stays)
* dimension style of dimensions DrawingKit makes (when set explicitly)
* tolerance frames: only that standard's symbols and modifiers
  (ISO 1101: CZ, UZ; ASME Y14.5: Ⓤ, Ⓢ, ST, CF, dynamic profile, translation;
  ASME Y14.5-2018 has no symmetry or concentricity)
* weld symbols: ISO 2553's dashed identification line, none for AWS A2.4
"""

import FreeCAD as App

PREFS = "User parameter:BaseApp/Preferences/Mod/DrawingKit"
TD_DIMS = "User parameter:BaseApp/Preferences/Mod/TechDraw/Dimensions"
CHOICES = [("auto", "Auto (as TechDraw)"), ("ISO", "ISO"), ("ASME", "ASME")]


def setting():
    try:
        v = App.ParamGet(PREFS).GetString("Standard", "auto") or "auto"
    except Exception:
        v = "auto"
    return v if v in ("auto", "ISO", "ASME") else "auto"


def set_setting(value):
    if value not in ("auto", "ISO", "ASME"):
        raise ValueError("Standard must be auto, ISO or ASME")
    App.ParamGet(PREFS).SetString("Standard", value)


def techdraw_standard():
    """ISO or ASME from TechDraw's dimension standard preference."""
    try:
        i = App.ParamGet(TD_DIMS).GetInt("StandardAndStyle", 0)
    except Exception:
        i = 0
    return "ASME" if i >= 2 else "ISO"


def current():
    s = setting()
    return techdraw_standard() if s == "auto" else s


def is_asme():
    return current() == "ASME"


def explicit():
    """True if the user chose a standard (not Auto)."""
    return setting() != "auto"


# ---------------------------------------------------------------------------
# What follows the standard
# ---------------------------------------------------------------------------
def section_caption(letter, std=None, scale_txt=None):
    """'SECTION A-A' (ASME) or 'A-A' (ISO); with ``scale_txt`` (a section
    drawn at its own scale) 'SECTION A-A SCALE 2 : 1' or 'A-A (2 : 1)'."""
    std = std or current()
    cap = ("SECTION %s-%s" if std == "ASME" else "%s-%s") % (letter, letter)
    if scale_txt:
        scale_txt = scale_txt.replace(":", " : ") if " : " not in scale_txt else scale_txt
        cap += (" SCALE %s" if std == "ASME" else " (%s)") % scale_txt
    return cap


def detail_caption(letter, scale_txt, std=None):
    std = std or current()
    scale_txt = scale_txt.replace(":", " : ") if " : " not in scale_txt else scale_txt
    if std == "ASME":
        return "DETAIL %s SCALE %s" % (letter, scale_txt)
    return "%s (%s)" % (letter, scale_txt)


def default_first_angle(std=None):
    return (std or current()) == "ISO"


def weld_dashed_line(std=None):
    """ISO 2553 system A draws the dashed identification line; AWS A2.4
    (used with ASME drawings) does not."""
    return (std or current()) == "ISO"


# frame symbols per standard
ASME_ONLY_ZONES = ("DP", "ST")
ISO_ONLY_ZONES = ("CZ",)
NOT_IN_ASME_2018 = ("symmetry", "concentricity")


def frame_zones(std=None):
    """Zone modifiers the frame pop-up offers."""
    std = std or current()
    base = ["U", "P", "F", "T"]
    return base + (list(ASME_ONLY_ZONES) if std == "ASME" else list(ISO_ONLY_ZONES))


def tolerance_modifiers(std=None):
    """Material-condition modifiers for the tolerance (ISO has no RFS)."""
    return ["", "MMC", "LMC"] + (["RFS"] if (std or current()) == "ASME" else [])


def datum_modifiers(std=None):
    return ["", "MMC", "LMC"] + (["RFS", "TR"] if (std or current()) == "ASME" else [])


def has_cf(std=None):
    return (std or current()) == "ASME"


def characteristic_note(name, std=None):
    """'' or a short note shown next to a characteristic in the pop-up."""
    if (std or current()) == "ASME" and name in NOT_IN_ASME_2018:
        return "not in ASME Y14.5-2018"
    return ""


def dimension_style(referencing=False, std=None):
    std = std or current()
    if std == "ASME":
        return "ASME Referencing" if referencing else "ASME Inlined"
    return "ISO Referencing" if referencing else "ISO Oriented"


def style_dimension(dim, referencing=False):
    """Give a DrawingKit-made dimension the chosen standard's style (only
    when the standard was set explicitly; on Auto TechDraw's default stays).
    GUI only (a display property)."""
    if not explicit():
        return False
    vo = getattr(dim, "ViewObject", None)
    if vo is None or "StandardAndStyle" not in vo.PropertiesList:
        return False
    vo.StandardAndStyle = dimension_style(referencing)
    return True


def apply_to_page(page):
    """Bring an existing sheet's DrawingKit-made items into line with the
    current standard: section/detail captions, weld lines and the dimension
    style. Returns how many items changed."""
    from . import annot, core
    n = 0
    std = current()
    for v in page.Views:
        try:
            if v.isDerivedFrom("TechDraw::DrawViewDetail"):
                cap = detail_caption(v.Reference, core.scale_text(core.view_scale(v)), std)
                if v.Caption != cap:
                    v.Caption = cap
                    n += 1
            elif v.isDerivedFrom("TechDraw::DrawViewSection"):
                cap = section_caption(v.SectionSymbol, std)
                if v.Caption != cap:
                    v.Caption = cap
                    n += 1
            elif getattr(v, annot.KIND, "") == "weld":
                p = annot._params(v)
                want = weld_dashed_line(std)
                if bool(p.get("iso_line", True)) != want:
                    import json
                    p["iso_line"] = want
                    setattr(v, annot.PARAMS, json.dumps(p))
                    annot.render(v)
                    n += 1
            elif v.isDerivedFrom("TechDraw::DrawViewDimension") and explicit():
                vo = getattr(v, "ViewObject", None)
                if vo is not None and "StandardAndStyle" in vo.PropertiesList:
                    ref = "Referencing" in str(vo.StandardAndStyle)
                    want = dimension_style(ref, std)
                    if str(vo.StandardAndStyle) != want:
                        vo.StandardAndStyle = want
                        n += 1
        except Exception as e:
            App.Console.PrintWarning("DrawingKit standard: %s: %s\n" % (v.Label, e))
    return n
