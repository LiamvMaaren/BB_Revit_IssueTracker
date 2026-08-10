# -*- coding: utf-8 -*-
"""Minimal ExternalEvent proxy used by the startup-safe dockable shell.

Only the Revit UI interface and a queue are imported during Revit startup. The
request implementation (document context, navigation, screenshots and selection)
is imported on the first actual ExternalEvent execution.
"""

from collections import deque

from pyrevit import UI


class LazyRevitRequestHandler(UI.IExternalEventHandler):
    def __init__(self):
        self.panel = None
        self.queue = deque()
        self.external_event = None
        self._implementation = None

    def set_panel(self, panel):
        self.panel = panel
        if self._implementation is not None:
            self._implementation.panel = panel

    def GetName(self):
        return "BB Issue Tracker lazy request queue"

    def Execute(self, uiapp):
        if not self.queue:
            return
        request = self.queue.popleft()
        try:
            if self._implementation is None:
                from bb_issue_tracker.revit.external_event import RevitRequestHandler
                self._implementation = RevitRequestHandler(self.panel)
                self._implementation.external_event = self.external_event
            else:
                self._implementation.panel = self.panel
            self._implementation._execute_request(uiapp, request)
        except Exception as error:
            self._call_panel(
                "on_revit_request_failed",
                request.get("kind", ""),
                str(error)
            )
        finally:
            if self.queue and self.external_event:
                try:
                    self.external_event.Raise()
                except Exception:
                    pass

    def _call_panel(self, method_name, *args):
        method = getattr(self.panel, method_name, None)
        if method:
            method(*args)


class SafeExternalEventBridge(object):
    def __init__(self):
        self.handler = LazyRevitRequestHandler()
        self.event = UI.ExternalEvent.Create(self.handler)
        self.handler.external_event = self.event

    def set_panel(self, panel):
        self.handler.set_panel(panel)

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
