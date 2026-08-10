# -*- coding: utf-8 -*-

import glob
import os
import shutil

from pyrevit import DB

from bb_issue_tracker.locking import ensure_directory
from bb_issue_tracker.revit.context import view_context


def capture_visible_region(uidoc, target_path, pixel_size=1800):
    doc = uidoc.Document
    target_path = os.path.abspath(target_path)
    ensure_directory(os.path.dirname(target_path))
    base_path = os.path.splitext(target_path)[0]
    before = set(glob.glob(base_path + "*"))
    options = DB.ImageExportOptions()
    try:
        options.ExportRange = DB.ExportRange.VisibleRegionOfCurrentView
        options.FilePath = base_path
        options.ZoomType = DB.ZoomFitType.FitToPage
        options.PixelSize = int(pixel_size)
        options.FitDirection = DB.FitDirectionType.Horizontal
        options.HLRandWFViewsFileType = DB.ImageFileType.PNG
        options.ShadowViewsFileType = DB.ImageFileType.PNG
        options.ImageResolution = DB.ImageResolution.DPI_150
        doc.ExportImage(options)
    finally:
        try:
            options.Dispose()
        except Exception:
            pass

    candidates = [path for path in glob.glob(base_path + "*") if path not in before]
    if os.path.isfile(target_path):
        exported = target_path
    elif candidates:
        exported = max(candidates, key=os.path.getmtime)
    else:
        existing = glob.glob(base_path + "*")
        if not existing:
            raise IOError("Revit heeft geen snapshotbestand gegenereerd.")
        exported = max(existing, key=os.path.getmtime)
    if os.path.normcase(exported) != os.path.normcase(target_path):
        shutil.copy2(exported, target_path)
    metadata = view_context(uidoc)
    metadata["kind"] = "revit_view"
    return target_path, metadata
