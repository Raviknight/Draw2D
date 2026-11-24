# SPDX-License-Identifier: LGPL-2.1-or-later
"""Edit a TechDraw dimension the Onshape way: prefix/suffix text (e.g. '3X'),
decimals, tolerance (none, symmetric ±, deviation +/-), basic (boxed) and
reference (in parentheses).

TechDraw keeps all of this in the dimension's own properties (FormatSpec,
TheoreticalExact, EqualTolerance, OverTolerance, UnderTolerance...); this
module only reads and writes them, so the result is a normal dimension.
"""

import re

NUM = re.compile(r"%[-+ #0]*\d*(?:\.(\d+))?[fgw]")
TOL_TYPES = ["none", "symmetric", "deviation", "limits"]


def _is_angle(dim):
    return "Angle" in (dim.Type or "")


def _limits(dim, nominal, upper, lower, dp):
    """('60.05', '59.95'): the largest and smallest size allowed."""
    unit = "°" if _is_angle(dim) else ""
    return ("%.*f%s" % (dp, nominal + float(upper), unit),
            "%.*f%s" % (dp, nominal - abs(float(lower)), unit))


def _mm(q):
    try:
        return float(q.Value)
    except AttributeError:
        return float(q)


def read_dimension(dim):
    """Current settings of a dimension as a plain dict."""
    spec = dim.FormatSpec or "%.2w"
    if is_limits(dim):
        m_ = re.search(r"\.(\d+)", dim.FormatSpecOverTolerance or "")
        return {"before": spec.strip(), "after": "", "decimals": len(m_.group(1)) if m_ else 0,
                "tol": "limits", "upper": _mm(dim.OverTolerance),
                "lower": abs(_mm(dim.UnderTolerance)), "basic": False, "reference": False}
    m = NUM.search(spec)
    if m is None:
        before, after, decimals = spec, "", 2
    else:
        before, after = spec[:m.start()], spec[m.end():]
        decimals = int(m.group(1)) if m.group(1) is not None else 2
    reference = before.endswith("(") and after.startswith(")")
    if reference:
        before, after = before[:-1], after[1:]
    over, under = _mm(dim.OverTolerance), _mm(dim.UnderTolerance)
    if abs(over) < 1e-12 and abs(under) < 1e-12:
        tol = "none"
    elif dim.EqualTolerance:
        tol = "symmetric"
    else:
        tol = "deviation"
    return {"before": before, "after": after, "decimals": decimals,
            "tol": tol, "upper": over, "lower": abs(under),
            "basic": bool(dim.TheoreticalExact), "reference": reference}


def is_limits(dim):
    """A limit dimension: no value of its own (only text before it, like
    '⌀' or '3X'), the largest and smallest size written out stacked."""
    spec = dim.FormatSpec or ""
    return bool(dim.Arbitrary) and "%" not in spec and bool(dim.ArbitraryTolerances) and \
        not dim.EqualTolerance


def set_dimension(dim, before="", after="", decimals=2, tol="none", upper=0.0, lower=0.0,
                  basic=False, reference=False, nominal=None):
    """Write the settings into the dimension. A basic dimension has no
    tolerance (it is the theoretically exact value), so ``basic`` wins.
    ``tol='limits'`` writes the largest and smallest size (from the
    measured value, or ``nominal``) stacked, as a limit dimension."""
    if tol not in TOL_TYPES:
        raise ValueError("Tolerance type must be one of %s" % ", ".join(TOL_TYPES))
    if tol != "none" and abs(float(upper)) < 1e-12 and abs(float(lower)) < 1e-12:
        tol = "none"   # a zero tolerance would print as '± 0.00'
    if tol == "limits" and not basic:
        if nominal is None:
            nominal = float(dim.getRawValue())
        dp = int(decimals)
        hi, lo = _limits(dim, nominal, upper, lower, dp)
        prefix = before.strip()
        if dim.Type == "Diameter" and "⌀" not in prefix:
            prefix = (prefix + " ⌀").strip()
        elif dim.Type == "Radius" and not prefix.endswith("R"):
            prefix = (prefix + " R").strip()
        dim.TheoreticalExact = False
        dim.Arbitrary = True
        dim.FormatSpec = (prefix + " ") if prefix else " "
        dim.EqualTolerance = False
        dim.OverTolerance = float(upper)
        dim.UnderTolerance = -abs(float(lower))
        dim.ArbitraryTolerances = True
        dim.FormatSpecOverTolerance = hi
        dim.FormatSpecUnderTolerance = lo
        return dim
    num = "%%.%df" % int(decimals)
    if reference:
        num = "(" + num + ")"
    dim.Arbitrary = False
    dim.FormatSpec = "%s%s%s" % (before, num, after)
    dim.TheoreticalExact = bool(basic)
    dp = int(decimals)
    # The tolerance text is written out literally (ArbitraryTolerances):
    # FreeCAD 1.1 prints small tolerances in the unit schema's sub-unit
    # without the unit (0.05 mm came out as "± 50").
    if basic or tol == "none":
        dim.ArbitraryTolerances = False
        dim.FormatSpecOverTolerance = "%%+.%dw" % dp
        dim.FormatSpecUnderTolerance = "%%+.%dw" % dp
        dim.EqualTolerance = True
        dim.OverTolerance = 0.0
        dim.UnderTolerance = 0.0
    elif tol == "symmetric":
        u = abs(float(upper))
        dim.EqualTolerance = True
        dim.OverTolerance = u
        dim.UnderTolerance = -u
        dim.ArbitraryTolerances = True
        dim.FormatSpecOverTolerance = "%.*f" % (dp, u)
        dim.FormatSpecUnderTolerance = "%.*f" % (dp, -u)
    else:
        u, l_ = float(upper), -abs(float(lower))
        dim.EqualTolerance = False
        dim.OverTolerance = u
        dim.UnderTolerance = l_
        dim.ArbitraryTolerances = True
        dim.FormatSpecOverTolerance = "%+.*f" % (dp, u)
        dim.FormatSpecUnderTolerance = "%+.*f" % (dp, l_)
    return dim


def refresh_limits(page):
    """Write the limits of the page's limit dimensions again from what they
    measure now (the model may have changed). Returns how many changed."""
    n = 0
    for d in page.Views:
        try:
            if not d.isDerivedFrom("TechDraw::DrawViewDimension") or not is_limits(d):
                continue
            s = read_dimension(d)
            hi, lo = _limits(d, float(d.getRawValue()), s["upper"], s["lower"], s["decimals"])
        except Exception:
            continue
        if d.FormatSpecOverTolerance != hi or d.FormatSpecUnderTolerance != lo:
            d.FormatSpecOverTolerance, d.FormatSpecUnderTolerance = hi, lo
            n += 1
    return n
