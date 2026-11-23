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

    # TechDraw's own tools, so you can dimension and annotate without
    # leaving Draw2D (names from FreeCAD 1.1's TechDraw workbench).
    TECHDRAW_TOOLBARS = [
        ("TechDraw views", ["TechDraw_View", "TechDraw_ActiveView", "TechDraw_DetailView",
                            "TechDraw_BrokenView", "TechDraw_ClipGroup"]),
        ("TechDraw dimensions", ["TechDraw_Dimension", "TechDraw_CompDimensionTools",
                                 "TechDraw_ExtentGroup", "TechDraw_Balloon",
                                 "TechDraw_AxoLengthDimension", "TechDraw_DimensionRepair"]),
        ("TechDraw dimension helpers", ["TechDraw_ExtensionCreateChainDimensionGroup",
                                        "TechDraw_ExtensionCreateCoordDimensionGroup",
                                        "TechDraw_ExtensionChamferDimensionGroup",
                                        "TechDraw_ExtensionInsertPrefixGroup",
                                        "TechDraw_ExtensionIncreaseDecreaseGroup"]),
        ("TechDraw centre lines", ["TechDraw_ExtensionCircleCenterLinesGroup",
                                   "TechDraw_CenterLineGroup", "TechDraw_ExtensionThreadsGroup",
                                   "TechDraw_CosmeticVertexGroup", "TechDraw_2PointCosmeticLine"]),
        ("TechDraw annotation", ["TechDraw_Annotation", "TechDraw_RichTextAnnotation",
                                 "TechDraw_LeaderLine", "TechDraw_WeldSymbol",
                                 "TechDraw_SurfaceFinishSymbols", "TechDraw_HoleShaftFit",
                                 "TechDraw_Hatch", "TechDraw_GeometricHatch",
                                 "TechDraw_ToggleFrame"]),
        ("TechDraw page", ["TechDraw_FillTemplateFields", "TechDraw_RedrawPage",
                           "TechDraw_ExportPageSVG", "TechDraw_ExportPageDXF",
                           "TechDraw_PrintAll"]),
    ]

    def Initialize(self):
        import TechDrawGui  # noqa: F401  (page display, and registers TechDraw's commands)
        from Draw2DTools import commands
        self.appendToolbar("Draw2D", commands.COMMAND_NAMES)
        self.appendMenu("Draw2D", commands.COMMAND_NAMES)
        known = set(Gui.listCommands())
        for name, cmds in self.TECHDRAW_TOOLBARS:
            cmds = [c for c in cmds if c in known]
            if cmds:
                self.appendToolbar(name, cmds)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(Draw2DWorkbench())


class Draw2DInTechDraw:
    """Also show the Draw2D buttons in the TechDraw workbench.

    FreeCAD switches to TechDraw when you click on a page, which would hide
    the Draw2D toolbar; this puts the same buttons at the end of TechDraw's
    'TechDraw Pages' toolbar.
    """

    def modifyToolBars(self):
        try:
            from Draw2DTools import commands
        except Exception:
            return []
        items = [{"append": "Separator", "toolBar": "TechDraw Pages"}]
        items += [{"append": name, "toolBar": "TechDraw Pages"}
                  for name in commands.COMMAND_NAMES]
        return items


if hasattr(Gui, "addWorkbenchManipulator"):
    Gui.addWorkbenchManipulator(Draw2DInTechDraw())
