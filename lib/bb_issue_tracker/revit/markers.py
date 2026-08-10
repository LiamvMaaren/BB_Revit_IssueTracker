# -*- coding: utf-8 -*-
"""Temporary, clickable and plot-safe Revit issue markers."""

import os

from System import Guid
from System.Collections.Generic import List

from pyrevit import DB, UI
from Autodesk.Revit.DB.ExternalService import (
    ExternalServiceRegistry, ExternalServices
)

from bb_issue_tracker.models import normalize_issue
from bb_issue_tracker.revit.anchors import resolve_location_point
from bb_issue_tracker.revit.context import (
    central_model_path, current_model_key, is_graphical_view
)
from bb_issue_tracker.revit.marker_icons import MarkerIconCache
from bb_issue_tracker.status import marker_status
from bb_issue_tracker.textutils import to_text


MARKER_SERVER_ID = Guid("f5b9c8c2-893e-4e96-944b-0383e9686b74")


def _document_token(document):
    try:
        identity = to_text(document.PathName)
    except Exception:
        identity = ""
    try:
        title = to_text(document.Title)
    except Exception:
        title = ""
    try:
        runtime = to_text(document.GetHashCode())
    except Exception:
        runtime = title
    return "{0}|{1}|{2}".format(runtime, title, identity)


def _normal_path(value):
    text = to_text(value).strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(text))
    except Exception:
        return text.lower()


def _issue_matches_document(issue, document, model_key):
    """Match robustly while preserving model isolation.

    The stored hash can legitimately change when a previously unsaved model is
    saved or when a detached/local file changes path. Exact keys remain the
    primary match; path and title are controlled fallbacks for those cases.
    """
    model = issue.get("model", {}) or {}
    issue_key = to_text(model.get("model_key", "")).strip()
    if issue_key and issue_key == model_key:
        return True

    issue_path = _normal_path(model.get("central_model_path", ""))
    document_path = _normal_path(central_model_path(document))
    if issue_path and document_path and issue_path == document_path:
        return True

    issue_title = to_text(model.get("model_title", "")).strip().lower()
    document_title = to_text(getattr(document, "Title", "")).strip().lower()
    if issue_title and document_title and issue_title == document_title:
        # A title fallback is only accepted when one side has no stable path.
        return not issue_path or not document_path
    return False


class MarkerClickHandler(UI.ITemporaryGraphicsHandler):
    def __init__(self):
        self.panel = None
        self._issue_by_control = {}

    def set_panel(self, panel):
        self.panel = panel

    def bind(self, document, index, issue_id):
        self._issue_by_control[(_document_token(document), int(index))] = issue_id

    def unbind_document(self, document):
        token = _document_token(document)
        for key in list(self._issue_by_control.keys()):
            if key[0] == token:
                del self._issue_by_control[key]

    def OnClick(self, data):
        issue_id = self._issue_by_control.get(
            (_document_token(data.Document), int(data.Index)), ""
        )
        if not issue_id or self.panel is None:
            return
        callback = getattr(self.panel, "on_marker_clicked", None)
        if callback:
            try:
                callback(issue_id)
            except Exception:
                # A temporary graphics callback must never destabilize Revit.
                pass

    def GetName(self):
        return "BB Issue Tracker marker clicks"

    def GetDescription(self):
        return "Opent het issue dat bij een tijdelijke BB-marker hoort."

    def GetVendorId(self):
        return "BBBV"

    def GetServiceId(self):
        return ExternalServices.BuiltInExternalServices.TemporaryGraphicsHandlerService

    def GetServerId(self):
        return MARKER_SERVER_ID


_CLICK_HANDLER = MarkerClickHandler()


class MarkerController(object):
    def __init__(self, panel):
        self.panel = panel
        self._states = {}
        self._service_ready = False
        self._service_warning = ""
        self._events_ready = False
        self._application = None
        _CLICK_HANDLER.set_panel(panel)

    def set_panel(self, panel):
        self.panel = panel
        _CLICK_HANDLER.set_panel(panel)

    def sync(self, uiapp, raw_issues, icon_dir, fallback_dir=""):
        uidoc = uiapp.ActiveUIDocument
        if uidoc is None:
            self.clear_all()
            return 0, [], []
        document = uidoc.Document
        view = uidoc.ActiveView
        warnings = []

        # Click registration must not block drawing. This also makes the marker
        # visible on hosts where a Python engine cannot expose the callback
        # interface correctly; the warning remains visible in the panel.
        try:
            self._ensure_click_service()
        except Exception as error:
            self._service_warning = "Klikhandler: {0}".format(to_text(error))
        if self._service_warning:
            warnings.append(self._service_warning)

        try:
            self._ensure_plot_events(uiapp)
        except Exception as error:
            warnings.append("Print-/exportbeveiliging: {0}".format(to_text(error)))
        self._clear_other_documents(document)

        if not is_graphical_view(view):
            self.clear_document(document)
            return 0, warnings, []

        model_key = current_model_key(document)
        view_unique_id = to_text(view.UniqueId)
        view_id = to_text(getattr(view.Id, "Value", getattr(view.Id, "IntegerValue", "")))
        view_name = to_text(view.Name)
        candidates = []
        location_updates = []
        for raw_issue in raw_issues or []:
            issue = normalize_issue(raw_issue)
            if not _issue_matches_document(issue, document, model_key):
                continue
            location = issue.get("location", {}) or {}
            issue_view_uid = to_text(location.get("view_unique_id", ""))
            issue_view_id = to_text(location.get("view_id", ""))
            issue_view_name = to_text(location.get("view_name", ""))
            if issue_view_uid:
                if issue_view_uid != view_unique_id:
                    continue
            elif issue_view_id:
                if issue_view_id != view_id:
                    continue
            elif issue_view_name != view_name:
                continue
            visible_status = marker_status(issue)
            if not visible_status:
                continue
            point, resolved_location, host_warning = resolve_location_point(
                document, location
            )
            if point is None:
                continue
            point_values = resolved_location.get("point_xyz_internal") or []
            if resolved_location != location:
                location_updates.append({
                    "issue_id": issue.get("issue_id", ""),
                    "revision": issue.get("revision", 0),
                    "project": issue.get("project", {}) or {},
                    "location": resolved_location
                })
            candidates.append(
                (issue, visible_status, point, point_values, host_warning)
            )

        candidates.sort(key=lambda item: to_text(item[0].get("issue_id")))
        signature = (
            model_key,
            view_unique_id,
            tuple(
                (
                    issue.get("issue_id", ""),
                    issue.get("title", ""),
                    status,
                    tuple(round(float(value), 7) for value in values[:3])
                )
                for issue, status, point, values, host_warning in candidates
            )
        )
        token = _document_token(document)
        existing = self._states.get(token)
        if existing and existing.get("signature") == signature:
            return len(existing.get("indices", [])), warnings, location_updates

        self.clear_document(document)
        if not candidates:
            self._states[token] = {
                "document": document,
                "signature": signature,
                "indices": [],
                "data": []
            }
            return 0, warnings, location_updates

        manager = DB.TemporaryGraphicsManager.GetTemporaryGraphicsManager(document)
        icon_cache = MarkerIconCache(icon_dir, fallback_dir=fallback_dir)
        indices = []
        data_objects = []
        add_warnings = []
        for issue, status, point, values, host_warning in candidates:
            data = None
            try:
                image_path = icon_cache.icon_path(issue.get("issue_id", ""), status)
                image_path = os.path.abspath(image_path)
                if not os.path.isfile(image_path):
                    raise IOError("Markerafbeelding ontbreekt: {0}".format(image_path))
                data = DB.InCanvasControlData(image_path, point)
                index = manager.AddControl(data, view.Id)
                manager.SetVisibility(int(index), True)
                indices.append(int(index))
                # Keep the data wrapper alive for the complete control lifetime.
                # This avoids premature disposal/GC differences between Revit
                # and pyRevit Python engines.
                data_objects.append(data)
                data = None
                if self._service_ready:
                    _CLICK_HANDLER.bind(document, index, issue.get("issue_id", ""))
                try:
                    tooltip = "{0} - {1}".format(
                        issue.get("issue_id", ""),
                        issue.get("title", "") or status
                    )
                    if host_warning:
                        tooltip += "\n" + host_warning
                    manager.SetTooltip(index, tooltip)
                except Exception:
                    pass
            except Exception as error:
                add_warnings.append(
                    "{0}: {1}".format(issue.get("issue_id", "Issue"), to_text(error))
                )
            finally:
                if data is not None:
                    try:
                        data.Dispose()
                    except Exception:
                        pass

        warnings.extend(add_warnings)
        # Do not cache a failed signature. The one-second timer must retry after
        # a transient graphics or image-loading failure.
        stored_signature = signature if len(indices) == len(candidates) else None
        self._states[token] = {
            "document": document,
            "signature": stored_signature,
            "indices": indices,
            "data": data_objects
        }
        try:
            uidoc.RefreshActiveView()
        except Exception:
            pass
        return len(indices), warnings, location_updates

    def clear_document(self, document):
        token = _document_token(document)
        state = self._states.pop(token, None)
        _CLICK_HANDLER.unbind_document(document)
        if not state:
            return
        try:
            manager = DB.TemporaryGraphicsManager.GetTemporaryGraphicsManager(document)
        except Exception:
            manager = None
        if manager is not None:
            for index in state.get("indices", []):
                try:
                    manager.RemoveControl(int(index))
                except Exception:
                    pass
        for data in state.get("data", []):
            try:
                data.Dispose()
            except Exception:
                pass

    def clear_all(self):
        for state in list(self._states.values()):
            document = state.get("document")
            if document is not None:
                self.clear_document(document)
        self._states = {}

    def _clear_other_documents(self, active_document):
        active_token = _document_token(active_document)
        for token, state in list(self._states.items()):
            if token != active_token:
                document = state.get("document")
                if document is not None:
                    self.clear_document(document)

    def _ensure_click_service(self):
        _CLICK_HANDLER.set_panel(self.panel)
        if self._service_ready:
            return
        service = ExternalServiceRegistry.GetService(
            ExternalServices.BuiltInExternalServices.TemporaryGraphicsHandlerService
        )
        if service is None:
            raise RuntimeError("De Revit-service voor tijdelijke graphics is niet beschikbaar.")

        registered = list(service.GetRegisteredServerIds())
        if MARKER_SERVER_ID not in registered:
            service.AddServer(_CLICK_HANDLER)

        # SetActiveServers belongs to MultiServerService. IronPython normally
        # exposes it from the runtime type; getattr gives a clear diagnostic on
        # Python/.NET combinations where that cast is not surfaced.
        get_active = getattr(service, "GetActiveServerIds", None)
        set_active = getattr(service, "SetActiveServers", None)
        if get_active is None or set_active is None:
            raise RuntimeError("Revit kon de multi-serveractivatie niet openen.")
        active = list(get_active())
        if MARKER_SERVER_ID not in active:
            active.append(MARKER_SERVER_ID)
            typed = List[Guid]()
            for server_id in active:
                typed.Add(server_id)
            set_active(typed)
        self._service_ready = True
        self._service_warning = ""

    def _ensure_plot_events(self, uiapp):
        if self._events_ready:
            return
        application = uiapp.Application
        application.DocumentPrinting += self._before_print
        application.DocumentPrinted += self._after_print
        application.FileExporting += self._before_export
        application.FileExported += self._after_export
        self._application = application
        self._events_ready = True

    def _before_print(self, sender, args):
        try:
            self.clear_document(args.Document)
        except Exception:
            pass

    def _after_print(self, sender, args):
        # The panel timer restores markers after Revit completes printing.
        pass

    def _before_export(self, sender, args):
        try:
            self.clear_document(args.Document)
        except Exception:
            pass

    def _after_export(self, sender, args):
        # The panel timer restores markers after Revit completes exporting.
        pass
