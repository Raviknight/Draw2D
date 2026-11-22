# SPDX-License-Identifier: LGPL-2.1-or-later
# FreeCAD runs this file with separate globals and locals, so names imported
# at the top are NOT visible inside the class body or its methods. Import
# everything the class needs inside the methods themselves.
import FreeCADGui as Gui


class Draw2DWorkbench(Gui.Workbench):
    MenuText = "Draw2D"
    ToolTip = "Quicker TechDraw drawings: sized sheets, fitted views, auto-lettered sections"

    def __init__(self):
        import os
        import Draw2DTools
        self.__class__.Icon = os.path.join(os.path.dirname(Draw2DTools.__file__),
                                           "Resources", "icons", "Draw2D.svg")

    def Initialize(self):
        import TechDrawGui  # noqa: F401  (page and view display)
        from Draw2DTools import commands
        self.appendToolbar("Draw2D", commands.COMMAND_NAMES)
        self.appendMenu("Draw2D", commands.COMMAND_NAMES)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(Draw2DWorkbench())
