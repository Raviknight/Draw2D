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
TOL_TYPES = ["none", "symmetric", "deviation"]


def _mm(q):
    try:
        return float(q.Value)
    except AttributeError:
        return float(q)


def read_dimension(dim):
    """Current settings of a dimension as a plain dict."""
    spec = dim.FormatSpec or "%.2w"
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


def set_dimension(dim, before="", after="", decimals=2, tol="none", upper=0.0, lower=0.0,
                  basic=False, reference=False):
    """Write the settings into the dimension. A basic dimension has no
    tolerance (it is the theoretically exact value), so ``basic`` wins."""
    if tol not in TOL_TYPES:
        raise ValueError("Tolerance type must be one of %s" % ", ".join(TOL_TYPES))
    if tol != "none" and abs(float(upper)) < 1e-12 and abs(float(lower)) < 1e-12:
        tol = "none"   # a zero tolerance would print as '± 0.00'
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
