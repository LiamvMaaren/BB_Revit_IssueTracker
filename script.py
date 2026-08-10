# -*- coding: utf-8 -*-

import os
import sys

from pyrevit import forms

bundle_dir = os.path.dirname(os.path.abspath(__file__))
bundle_lib = os.path.join(bundle_dir, "lib")
if bundle_lib not in sys.path:
    sys.path.insert(0, bundle_lib)

from bb_issue_tracker.constants import COMMAND_NEW_ISSUE, COMMAND_REFRESH, PANEL_ID
from bb_issue_tracker.interop import set_pending_command
from bb_issue_tracker.launcher import (
    ACTION_DASHBOARD,
    ACTION_NEW_ISSUE,
    show_launcher
)


def _open_tracker(command):
    try:
        forms.open_dockable_panel(PANEL_ID)
        # The dispatcher timer can only run after this command yields back to
        # Revit, so queueing after Show avoids leaving a stale command when the
        # pane could not be opened.
        set_pending_command(command)
    except Exception:
        forms.alert(
            "Het BB Issue Tracker-paneel is nog niet geregistreerd. "
            "Herlaad pyRevit zonder geopende documenten of start Revit opnieuw.",
            title="BB Issue Tracker",
            warn_icon=True
        )


selected_action = show_launcher()
if selected_action == ACTION_DASHBOARD:
    _open_tracker(COMMAND_REFRESH)
elif selected_action == ACTION_NEW_ISSUE:
    _open_tracker(COMMAND_NEW_ISSUE)
