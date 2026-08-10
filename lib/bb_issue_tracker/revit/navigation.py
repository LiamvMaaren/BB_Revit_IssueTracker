# -*- coding: utf-8 -*-

import copy

from pyrevit import DB

from bb_issue_tracker.revit.anchors import resolve_location_point
from bb_issue_tracker.revit.context import (
    element_id_value, get_ui_view, xyz_list_to_object
)


def find_view(doc, target):
    unique_id = target.get("view_unique_id")
    if unique_id:
        try:
            view = doc.GetElement(unique_id)
            if view and isinstance(view, DB.View):
                return view
        except Exception:
            pass
    view_id = target.get("view_id")
    if view_id:
        try:
            view = doc.GetElement(DB.ElementId(int(view_id)))
            if view and isinstance(view, DB.View):
                return view
        except Exception:
            pass
    view_name = target.get("view_name")
    if view_name:
        for view in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
            if not view.IsTemplate and view.Name == view_name:
                return view
    return None


def restore_3d_orientation(view, target):
    data = (target or {}).get("view_3d") or {}
    if not data or not isinstance(view, DB.View3D):
        return
    eye = xyz_list_to_object(data.get("eye_position"))
    up = xyz_list_to_object(data.get("up_direction"))
    forward = xyz_list_to_object(data.get("forward_direction"))
    if eye is None or up is None or forward is None:
        return
    orientation = None
    try:
        orientation = DB.ViewOrientation3D(eye, up, forward)
        view.SetOrientation(orientation)
    except Exception:
        # A locked or otherwise constrained 3D view may reject an orientation
        # change. The section box and XYZ navigation can still continue.
        pass
    finally:
        if orientation is not None:
            try:
                orientation.Dispose()
            except Exception:
                pass


def meters_to_internal(value):
    try:
        return float(DB.UnitUtils.ConvertToInternalUnits(
            float(value), DB.UnitTypeId.Meters
        ))
    except Exception:
        try:
            return float(DB.UnitUtils.ConvertToInternalUnits(
                float(value), DB.DisplayUnitType.DUT_METERS
            ))
        except Exception:
            return float(value) / 0.3048


def apply_3d_section_box(document, view, point, size_meters=4.0):
    """Apply a model-axis-aligned cube around the resolved issue point."""
    if point is None or not isinstance(view, DB.View3D):
        return False
    size_internal = max(meters_to_internal(size_meters), meters_to_internal(0.5))
    half = size_internal / 2.0
    box = DB.BoundingBoxXYZ()
    box.Transform = DB.Transform.Identity
    box.Min = DB.XYZ(point.X - half, point.Y - half, point.Z - half)
    box.Max = DB.XYZ(point.X + half, point.Y + half, point.Z + half)

    transaction = None
    started = False
    try:
        if not document.IsModifiable:
            transaction = DB.Transaction(
                document, "BB Issue Tracker - 3D section box"
            )
            transaction.Start()
            started = True
        view.IsSectionBoxActive = True
        view.SetSectionBox(box)
        if started:
            transaction.Commit()
        return True
    except Exception:
        if started and transaction is not None:
            try:
                transaction.RollBack()
            except Exception:
                pass
        return False
    finally:
        try:
            box.Dispose()
        except Exception:
            pass
        if transaction is not None:
            try:
                transaction.Dispose()
            except Exception:
                pass


def resolved_target(document, target):
    result = copy.deepcopy(target or {})
    point, resolved_location, warning = resolve_location_point(document, result)
    if resolved_location:
        result.update(resolved_location)
    return result, point, warning


def zoom_to_target(uidoc, target, point=None, size_meters=4.0):
    ui_view = get_ui_view(uidoc)
    if not ui_view:
        raise RuntimeError("De actieve Revit-view heeft geen open UIView.")

    if isinstance(uidoc.ActiveView, DB.View3D) and point is not None:
        half = meters_to_internal(size_meters) / 2.0
        first = DB.XYZ(point.X - half, point.Y - half, point.Z - half)
        second = DB.XYZ(point.X + half, point.Y + half, point.Z + half)
    else:
        corners = target.get("zoom_corners") or []
        if len(corners) >= 2:
            first = xyz_list_to_object(corners[0])
            second = xyz_list_to_object(corners[1])
        else:
            point = point or xyz_list_to_object(
                target.get("last_known_xyz_internal") or
                target.get("point_xyz_internal")
            )
            if point is None:
                return
            radius = 10.0
            first = DB.XYZ(point.X - radius, point.Y - radius, point.Z - radius)
            second = DB.XYZ(point.X + radius, point.Y + radius, point.Z + radius)
    ui_view.ZoomAndCenterRectangle(first, second)


class NavigationController(object):
    def __init__(self, callback):
        self.callback = callback
        self._uiapp = None
        self._target = None
        self._target_view_id = None
        self._attempts = 0
        self._section_box_size_m = 4.0

    def navigate(self, uiapp, target, section_box_size_m=4.0):
        if self._uiapp is not None:
            raise RuntimeError("Er wordt al naar een andere issue-locatie genavigeerd.")
        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            raise RuntimeError("Er is geen actief Revit-document.")
        view = find_view(uidoc.Document, target)
        if view is None:
            raise RuntimeError("De opgeslagen Revit-view is niet gevonden.")
        self._section_box_size_m = float(section_box_size_m or 4.0)
        if element_id_value(uidoc.ActiveView.Id) == element_id_value(view.Id):
            message = self._focus(uidoc, target)
            self.callback(True, message)
            return
        self._uiapp = uiapp
        self._target = target
        self._target_view_id = element_id_value(view.Id)
        self._attempts = 0
        uiapp.Idling += self._on_idling
        try:
            uidoc.RequestViewChange(view)
        except Exception:
            try:
                uiapp.Idling -= self._on_idling
            except Exception:
                pass
            self._uiapp = None
            self._target = None
            self._target_view_id = None
            raise

    def _focus(self, uidoc, target):
        resolved, point, warning = resolved_target(uidoc.Document, target)
        restore_3d_orientation(uidoc.ActiveView, resolved)
        section_box_applied = apply_3d_section_box(
            uidoc.Document,
            uidoc.ActiveView,
            point,
            self._section_box_size_m
        )
        zoom_to_target(
            uidoc,
            resolved,
            point=point,
            size_meters=self._section_box_size_m
        )
        try:
            uidoc.RefreshActiveView()
        except Exception:
            pass

        message = "Locatie geopend"
        if isinstance(uidoc.ActiveView, DB.View3D) and section_box_applied:
            message += " met section box van circa {0:g} m".format(
                self._section_box_size_m
            )
        if warning:
            message += " · " + warning
        return message

    def _on_idling(self, sender, args):
        self._attempts += 1
        try:
            uidoc = self._uiapp.ActiveUIDocument
            if uidoc and element_id_value(uidoc.ActiveView.Id) == self._target_view_id:
                message = self._focus(uidoc, self._target)
                self._finish(True, message)
            elif self._attempts > 30:
                self._finish(False, "De viewwissel duurde te lang.")
        except Exception as error:
            self._finish(False, str(error))

    def _finish(self, success, message):
        try:
            if self._uiapp:
                self._uiapp.Idling -= self._on_idling
        except Exception:
            pass
        self._uiapp = None
        self._target = None
        self._target_view_id = None
        self.callback(success, message)
