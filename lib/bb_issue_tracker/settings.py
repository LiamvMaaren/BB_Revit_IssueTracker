# -*- coding: utf-8 -*-

import io
import json
import os


DEFAULTS = {
    "storage_root": r"P:\Data\pyRevit Support\BB_IssueTracker",
    "issue_id_format": "{project_number}-#{number}",
    "refresh_seconds": 10,
    "screenshot_pixel_size": 1800,
    "lock_timeout_seconds": 30,
    "statuses": ["Open", "Opgelost", "Gesloten"],
    "status_filters": ["Open", "Opgelost", "Gesloten", "Te laat"],
    "marker_refresh_seconds": 1,
    "review_section_box_size_m": 4.0,
    "priorities": ["Laag", "Normaal", "Hoog", "Kritiek"],
    "issue_types": [
        "Modelleerfout", "Tekenwerk", "Brandveiligheid", "Coördinatie",
        "Vraag", "Controle", "Afstemming opdrachtgever", "Overig"
    ]
}


def extension_root():
    """Return the self-contained pushbutton root.

    The historic function name is retained for internal compatibility. In this
    installation layout the package and its config both live inside the
    ``BB Issue Tracker.pushbutton`` bundle, not directly under ``.extension``.
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(package_dir))


def settings_path():
    return os.path.join(extension_root(), "config", "settings.json")


def load_settings(path=None):
    values = dict(DEFAULTS)
    source = path or settings_path()
    if os.path.isfile(source):
        with io.open(source, "r", encoding="utf-8-sig") as stream:
            loaded = json.load(stream)
        if isinstance(loaded, dict):
            values.update(loaded)
    override = os.environ.get("BB_ISSUE_TRACKER_ROOT")
    if override:
        values["storage_root"] = override
    values["storage_root"] = os.path.normpath(values["storage_root"])
    return values
