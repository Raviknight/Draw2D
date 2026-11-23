# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run DrawingKit's headless tests inside FreeCAD.

    "C:\\Program Files\\FreeCAD 1.1\\bin\\freecadcmd.exe" tests\\run_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_drawingkit  # noqa: E402

suite = unittest.defaultTestLoader.loadTestsFromModule(test_drawingkit)
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
print("DRAWINGKIT TESTS %s" % ("PASSED" if result.wasSuccessful() else "FAILED"))
sys.stdout.flush()
os._exit(0 if result.wasSuccessful() else 1)
