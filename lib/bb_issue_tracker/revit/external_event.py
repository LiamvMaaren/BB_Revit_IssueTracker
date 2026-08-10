# -*- coding: utf-8 -*-

from collections import deque

from pyrevit import DB, UI
from Autodesk.Revit.Exceptions import InvalidOperationException, OperationCanceledException
from Autodesk.Revit.UI.Selection import ObjectSnapTypes, ObjectType

from bb_issue_tracker.constants import (
    REQUEST_CAPTURE_SNAPSHOT, REQUEST_CLEAR_MARKERS, REQUEST_GET_CONTEXT,
    REQUEST_NAVIGATE_LOCATION, REQUEST_NAVIGATE_VIEW, REQUEST_PICK_LOCATION,
    REQUEST_SYNC_MARKERS
)
from bb_issue_tracker.revit.anchors import (
    make_face_anchor, make_level_anchor, make_xyz_anchor
)
from bb_issue_tracker.revit.context import (
    document_context, is_graphical_view, view_context, xyz_to_list
)
from bb_issue_tracker.revit.navigation import NavigationController
from bb_issue_tracker.revit.screenshots import capture_visible_region


class RevitRequestHandler(UI.IExternalEventHandler):
    def __init__(self, panel):
        self.panel = panel
        self.queue = deque()
        self.external_event = None
        self.navigation = NavigationController(self._navigation_callback)
        self.markers = None

    def GetName(self):
        return "BB Issue Tracker request queue"

    def Execute(self, uiapp):
        if not self.queue:
            return
        request = self.queue.popleft()
        try:
            self._execute_request(uiapp, request)
        except Exception as error:
            self._call_panel("on_revit_request_failed", request.get("kind", ""), str(error))
        finally:
            if self.queue and self.external_event:
                try:
                    self.external_event.Raise()
                except Exception:
                    pass

    def _marker_controller(self):
        if self.markers is None:
            from bb_issue_tracker.revit.markers import MarkerController
            self.markers = MarkerController(self.panel)
        else:
            self.markers.set_panel(self.panel)
        return self.markers

    def _execute_request(self, uiapp, request):
        kind = request.get("kind")
        if kind == REQUEST_GET_CONTEXT:
            self._call_panel("on_context_received", document_context(uiapp))
            return
        if kind == REQUEST_CLEAR_MARKERS:
            controller = self._marker_controller()
            controller.clear_all()
            self._call_panel("on_marker_sync_completed", True, 0, [], [])
            return
        if kind == REQUEST_SYNC_MARKERS:
            controller = self._marker_controller()
            count, warnings, location_updates = controller.sync(
                uiapp,
                request.get("issues", []),
                request.get("icon_dir", ""),
                request.get("fallback_dir", "")
            )
            self._call_panel(
                "on_marker_sync_completed", True, count, warnings, location_updates
            )
            return

        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            raise RuntimeError("Er is geen actief Revit-document.")
        expected_model = request.get("expected_model_key")
        if expected_model:
            current_model = document_context(uiapp).get("model", {}).get("model_key", "")
            if current_model != expected_model:
                raise RuntimeError("Het actieve Revit-model is gewijzigd. Open het oorspronkelijke model opnieuw.")
        if kind == REQUEST_PICK_LOCATION:
            self._pick_location(uidoc, request)
        elif kind == REQUEST_CAPTURE_SNAPSHOT:
            self._marker_controller().clear_document(uidoc.Document)
            path, source = capture_visible_region(
                uidoc, request.get("target_path"), request.get("pixel_size", 1800)
            )
            self._call_panel("on_snapshot_captured", path, source)
        elif kind in (REQUEST_NAVIGATE_LOCATION, REQUEST_NAVIGATE_VIEW):
            current = document_context(uiapp)
            expected_model = request.get("model_key")
            if expected_model and current.get("model", {}).get("model_key") != expected_model:
                raise RuntimeError(
                    "Dit issue hoort bij model '{0}'. Open eerst het juiste Revit-model.".format(
                        request.get("model_title", "")
                    )
                )
            self.navigation.navigate(
                uiapp, request.get("target", {}),
                request.get("section_box_size_m", 4.0)
            )
        else:
            raise RuntimeError("Onbekend Revit-verzoek: {0}".format(kind))

    def _pick_location(self, uidoc, request):
        view = uidoc.ActiveView
        if not is_graphical_view(view):
            raise RuntimeError("Kies een grafische Revit-view voor een issue-locatie.")
        point = None
        anchor = None

        if isinstance(view, DB.View3D):
            reference = None
            try:
                reference = uidoc.Selection.PickObject(
                    ObjectType.Face,
                    "Klik een vlak als host voor het BB Issue"
                )
                point = reference.GlobalPoint
                if point is None:
                    raise RuntimeError("Revit kon het gekozen punt op het vlak niet bepalen.")
                try:
                    anchor = make_face_anchor(uidoc.Document, reference, point)
                except Exception:
                    # A linked or otherwise non-resolvable face still yields a
                    # durable XYZ issue location rather than cancelling the issue.
                    anchor = make_xyz_anchor(point)
            except OperationCanceledException:
                self._call_panel("on_location_pick_cancelled")
                return
            finally:
                if reference is not None:
                    try:
                        reference.Dispose()
                    except Exception:
                        pass
        else:
            try:
                snaps = (
                    ObjectSnapTypes.Endpoints | ObjectSnapTypes.Midpoints |
                    ObjectSnapTypes.Intersections | ObjectSnapTypes.Nearest
                )
                point = uidoc.Selection.PickPoint(snaps, "Klik de locatie van het BB Issue")
            except InvalidOperationException:
                reference = None
                try:
                    reference = uidoc.Selection.PickObject(
                        ObjectType.PointOnElement,
                        "Geen actief werkvlak: klik de locatie op een Revit-element"
                    )
                    point = reference.GlobalPoint
                    if point is not None:
                        try:
                            anchor = make_face_anchor(uidoc.Document, reference, point)
                        except Exception:
                            anchor = None
                except OperationCanceledException:
                    self._call_panel("on_location_pick_cancelled")
                    return
                finally:
                    if reference is not None:
                        try:
                            reference.Dispose()
                        except Exception:
                            pass
            except OperationCanceledException:
                self._call_panel("on_location_pick_cancelled")
                return
            if point is not None and anchor is None:
                anchor = make_level_anchor(uidoc.Document, view, point) or make_xyz_anchor(point)

        if point is None:
            raise RuntimeError("Revit kon geen geldig 3D-punt bepalen.")
        location = view_context(uidoc)
        point_values = xyz_to_list(point)
        location["point_xyz_internal"] = point_values
        location["last_known_xyz_internal"] = point_values
        location["anchor"] = anchor or make_xyz_anchor(point)
        snapshot_path = ""
        snapshot_source = {}
        if request.get("auto_snapshot") and request.get("target_path"):
            try:
                self._marker_controller().clear_document(uidoc.Document)
                snapshot_path, snapshot_source = capture_visible_region(
                    uidoc, request.get("target_path"), request.get("pixel_size", 1800)
                )
            except Exception as error:
                snapshot_source = {"kind": "capture_error", "message": str(error)}
        self._call_panel("on_location_picked", location, snapshot_path, snapshot_source)

    def _navigation_callback(self, success, message):
        self._call_panel("on_navigation_completed", success, message)

    def _call_panel(self, method_name, *args):
        method = getattr(self.panel, method_name, None)
        if method:
            method(*args)


class ExternalEventBridge(object):
    def __init__(self, panel):
        self.handler = RevitRequestHandler(panel)
        self.event = UI.ExternalEvent.Create(self.handler)
        self.handler.external_event = self.event

    def enqueue(self, kind, **payload):
        request = dict(payload)
        request["kind"] = kind
        self.handler.queue.append(request)
        self.event.Raise()

    def enqueue_latest(self, kind, **payload):
        retained = [item for item in self.handler.queue if item.get("kind") != kind]
        while self.handler.queue:
            self.handler.queue.popleft()
        self.handler.queue.extend(retained)
        self.enqueue(kind, **payload)
