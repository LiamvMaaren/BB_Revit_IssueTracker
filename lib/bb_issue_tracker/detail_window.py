# -*- coding: utf-8 -*-
"""Modeless issue-detail window owned by the dockable browser controller."""

import os

from pyrevit import forms


class IssueDetailWindow(forms.WPFWindow):
    """Large modeless editor that forwards its UI events to the browser."""

    def __init__(self, controller):
        self.controller = controller
        xaml_path = os.path.join(
            os.path.dirname(__file__),
            "ui",
            "detail.xaml"
        )
        forms.WPFWindow.__init__(self, xaml_path, handle_esc=False)
        self.Closing += self._window_closing
        self.Closed += self._window_closed

    def _window_closing(self, sender, args):
        if self.controller and not self.controller.can_close_detail_window():
            args.Cancel = True

    def _window_closed(self, sender, args):
        if self.controller:
            self.controller.on_detail_window_closed(self)
        self.controller = None

    def editor_changed(self, sender, args):
        self.controller.editor_changed(sender, args)

    def pick_location_click(self, sender, args):
        self.controller.pick_location_click(sender, args)

    def capture_snapshot_click(self, sender, args):
        self.controller.capture_snapshot_click(sender, args)

    def choose_image_click(self, sender, args):
        self.controller.choose_image_click(sender, args)

    def open_location_click(self, sender, args):
        self.controller.open_location_click(sender, args)

    def open_preview_view_click(self, sender, args):
        self.controller.open_preview_view_click(sender, args)

    def add_comment_click(self, sender, args):
        self.controller.add_comment_click(sender, args)

    def cancel_changes_click(self, sender, args):
        self.controller.cancel_changes_click(sender, args)

    def save_click(self, sender, args):
        self.controller.save_click(sender, args)

    def close_click(self, sender, args):
        self.Close()
