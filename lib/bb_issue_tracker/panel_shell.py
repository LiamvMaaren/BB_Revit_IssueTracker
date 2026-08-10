# -*- coding: utf-8 -*-
"""Startup-safe dockable pane shell for Revit 2024-2026."""

import os

from pyrevit import forms, UI
from System import TimeSpan
from System.Windows import Visibility
from System.Windows.Threading import DispatcherTimer

from bb_issue_tracker.constants import PANEL_ID, PANEL_TITLE
from bb_issue_tracker.interop import pop_pending_command
from bb_issue_tracker.safe_external_event import SafeExternalEventBridge


_initial_state = UI.DockablePaneState()
_initial_state.DockPosition = UI.DockPosition.Right


class BBIssueTrackerPanel(forms.WPFPanel):
    """Small registered shell; the dashboard is constructed only on demand."""

    panel_id = PANEL_ID
    panel_title = PANEL_TITLE
    panel_source = os.path.join(
        os.path.dirname(__file__), "ui", "panel_shell.xaml"
    )
    initial_state = _initial_state

    def __init__(self):
        self._content = None
        self._bridge = None
        forms.WPFPanel.__init__(self)

        # ExternalEvent.Create belongs in a valid Revit API context. The shell
        # is instantiated by RegisterDockablePane during pyRevit startup, so we
        # create only the minimal lazy proxy here.
        self._bridge = SafeExternalEventBridge()
        self.IsVisibleChanged += self._visibility_changed

        self._command_timer = DispatcherTimer()
        self._command_timer.Interval = TimeSpan.FromMilliseconds(300)
        self._command_timer.Tick += self._command_tick
        self._command_timer.Start()

    def _command_tick(self, sender, args):
        command = pop_pending_command()
        if command:
            self._activate(command)

    def _visibility_changed(self, sender, args):
        if self._content is not None:
            try:
                self._content.set_panel_visible(bool(self.IsVisible))
            except Exception:
                pass

    def _activate(self, command):
        try:
            if self._content is None:
                self.loading_text.Text = "Issue Tracker wordt geladen..."
                from bb_issue_tracker.panel import BBIssueTrackerContent
                self._content = BBIssueTrackerContent(self._bridge)
                self.content_host.Content = self._content
                self.placeholder.Visibility = Visibility.Collapsed
                self._content.set_panel_visible(bool(self.IsVisible))
            self._content.handle_command(command)
        except Exception as error:
            self.placeholder.Visibility = Visibility.Visible
            self.loading_text.Text = "Issue Tracker kon niet worden geopend."
            self.error_text.Text = str(error)
            try:
                self.logger.error(
                    "BB Issue Tracker kon niet worden geactiveerd: {0}".format(error)
                )
            except Exception:
                pass
