# -*- coding: utf-8 -*-

import os

from pyrevit import DB

from bb_issue_tracker.textutils import slugify, stable_hash, to_text


def element_id_value(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def xyz_to_list(point):
    if point is None:
        return []
    return [float(point.X), float(point.Y), float(point.Z)]


def xyz_list_to_object(values):
    if not values or len(values) < 3:
        return None
    return DB.XYZ(float(values[0]), float(values[1]), float(values[2]))


def get_ui_view(uidoc, view_id=None):
    target_id = element_id_value(view_id or uidoc.ActiveView.Id)
    for ui_view in uidoc.GetOpenUIViews():
        if element_id_value(ui_view.ViewId) == target_id:
            return ui_view
    return None


def get_zoom_corners(uidoc, view_id=None):
    ui_view = get_ui_view(uidoc, view_id)
    if not ui_view:
        return []
    try:
        return [xyz_to_list(point) for point in ui_view.GetZoomCorners()]
    except Exception:
        return []


def get_sheet_context(doc, view):
    result = {"sheet_id": "", "sheet_number": "", "sheet_name": ""}
    try:
        if isinstance(view, DB.ViewSheet):
            result["sheet_id"] = str(element_id_value(view.Id))
            result["sheet_number"] = view.SheetNumber
            result["sheet_name"] = view.Name
            return result
        sheets = DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
        target = element_id_value(view.Id)
        for sheet in sheets:
            for placed_id in sheet.GetAllPlacedViews():
                if element_id_value(placed_id) == target:
                    result["sheet_id"] = str(element_id_value(sheet.Id))
                    result["sheet_number"] = sheet.SheetNumber
                    result["sheet_name"] = sheet.Name
                    return result
    except Exception:
        pass
    return result


def view_context(uidoc, view=None):
    doc = uidoc.Document
    view = view or uidoc.ActiveView
    result = {
        "view_id": str(element_id_value(view.Id)),
        "view_unique_id": to_text(view.UniqueId),
        "view_name": to_text(view.Name),
        "view_type": to_text(view.ViewType),
        "zoom_corners": get_zoom_corners(uidoc, view.Id)
    }
    if isinstance(view, DB.View3D):
        orientation = None
        try:
            orientation = view.GetOrientation()
            result["view_3d"] = {
                "is_perspective": bool(view.IsPerspective),
                "eye_position": xyz_to_list(orientation.EyePosition),
                "up_direction": xyz_to_list(orientation.UpDirection),
                "forward_direction": xyz_to_list(orientation.ForwardDirection)
            }
        except Exception:
            result["view_3d"] = {"is_perspective": bool(view.IsPerspective)}
        finally:
            if orientation is not None:
                try:
                    orientation.Dispose()
                except Exception:
                    pass
    result.update(get_sheet_context(doc, view))
    return result


def central_model_path(doc):
    try:
        if doc.IsWorkshared:
            model_path = doc.GetWorksharingCentralModelPath()
            if model_path:
                return to_text(DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path))
    except Exception:
        pass
    try:
        return to_text(doc.PathName)
    except Exception:
        return ""


def current_model_key(doc):
    """Return the same stable model key used in the stored issue context."""
    model_identity = central_model_path(doc) or to_text(doc.Title)
    return stable_hash(model_identity, 20)


def document_context(uiapp):
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        return {
            "has_document": False,
            "project": {},
            "model": {},
            "view": {},
            "windows_username": os.environ.get("USERNAME", ""),
            "revit_username": to_text(uiapp.Application.Username)
        }
    doc = uidoc.Document
    project_info = doc.ProjectInformation
    project_number = to_text(project_info.Number).strip()
    try:
        project_name = to_text(project_info.Name).strip()
    except Exception:
        project_name = ""
    if not project_name or project_name.lower() == "project information":
        project_name = to_text(doc.Title).strip()
    model_path = central_model_path(doc)
    model_title = to_text(doc.Title)
    model_identity = model_path or model_title
    project_key = slugify(project_number, "")
    if not project_key:
        project_key = "model-{0}".format(stable_hash(model_identity, 12))
    return {
        "has_document": True,
        "project": {
            "project_key": project_key,
            "project_number": project_number,
            "project_name": project_name
        },
        "model": {
            "model_key": current_model_key(doc),
            "model_title": model_title,
            "central_model_path": model_path,
            "source_revit_version": to_text(uiapp.Application.VersionNumber)
        },
        "view": view_context(uidoc),
        "windows_username": os.environ.get("USERNAME", ""),
        "revit_username": to_text(uiapp.Application.Username)
    }


def is_graphical_view(view):
    blocked_names = (
        "ProjectBrowser", "SystemBrowser", "Schedule", "PanelSchedule",
        "ColumnSchedule", "Report", "CostReport", "LoadsReport",
        "PressureLossReport", "Internal", "DrawingSheet", "Legend",
        "Undefined"
    )
    blocked = [getattr(DB.ViewType, name) for name in blocked_names if hasattr(DB.ViewType, name)]
    try:
        if view is None or view.IsTemplate or view.ViewType in blocked:
            return False
        if isinstance(view, DB.ViewSheet):
            return False
        if hasattr(DB, "ViewSchedule") and isinstance(view, DB.ViewSchedule):
            return False
        return True
    except Exception:
        return False
