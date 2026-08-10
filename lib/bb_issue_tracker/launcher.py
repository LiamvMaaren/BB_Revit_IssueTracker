# -*- coding: utf-8 -*-

import os

from pyrevit import forms


ACTION_DASHBOARD = "DASHBOARD"
ACTION_NEW_ISSUE = "NEW_ISSUE"


class IssueTrackerLauncher(forms.WPFWindow):
    def __init__(self):
        self.selected_action = None
        xaml_path = os.path.join(
            os.path.dirname(__file__),
            "ui",
            "launcher.xaml"
        )
        forms.WPFWindow.__init__(self, xaml_path)

    def dashboard_click(self, sender, args):
        self.selected_action = ACTION_DASHBOARD
        self.Close()

    def new_issue_click(self, sender, args):
        self.selected_action = ACTION_NEW_ISSUE
        self.Close()

    def cancel_click(self, sender, args):
        self.Close()


def show_launcher():
    window = IssueTrackerLauncher()
    window.ShowDialog()
    return window.selected_action
