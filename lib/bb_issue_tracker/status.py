# -*- coding: utf-8 -*-
"""Canonical and derived status rules for BB Issue Tracker.

Only Open, Opgelost and Gesloten are persisted.  Te laat is a derived display
state for an open issue whose due date is before the current local date.
"""

import datetime

from bb_issue_tracker.textutils import to_text


OPEN = "Open"
RESOLVED = "Opgelost"
CLOSED = "Gesloten"
OVERDUE = "Te laat"

STORED_STATUSES = (OPEN, RESOLVED, CLOSED)
DISPLAY_STATUSES = (OPEN, RESOLVED, CLOSED, OVERDUE)

LEGACY_STATUS_MAP = {
    "In behandeling": OPEN,
    "Wacht op controle": OPEN,
    "Genegeerd": CLOSED,
    OVERDUE: OPEN
}

STATUS_PALETTE = {
    OPEN: {
        "background": "#FFF2E4",
        "foreground": "#A44A00",
        "border": "#F2B46D",
        "marker": "#F28C28"
    },
    RESOLVED: {
        "background": "#E8F7ED",
        "foreground": "#237A42",
        "border": "#A8D9B7",
        "marker": "#2EAD60"
    },
    CLOSED: {
        "background": "#F0F2F4",
        "foreground": "#697680",
        "border": "#D1D7DB",
        "marker": "#B9C0C5"
    },
    OVERDUE: {
        "background": "#FDECEC",
        "foreground": "#B42318",
        "border": "#F2AAA5",
        "marker": "#D9362B"
    }
}


def canonical_status(value):
    """Return one of the three statuses that may be stored in JSON."""
    text = to_text(value).strip()
    text = LEGACY_STATUS_MAP.get(text, text)
    if text not in STORED_STATUSES:
        return OPEN
    return text


def _date_value(value):
    text = to_text(value).strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def is_overdue(issue, today=None):
    """True when an open issue has a deadline before the active local date."""
    issue = issue or {}
    if canonical_status(issue.get("status")) != OPEN:
        return False
    due = _date_value(issue.get("due_date"))
    if due is None:
        return False
    current = today or datetime.date.today()
    return due < current


def display_status(issue, today=None):
    """Return the dashboard/marker status, including the derived Te laat."""
    status = canonical_status((issue or {}).get("status"))
    if status == OPEN and is_overdue(issue, today=today):
        return OVERDUE
    return status


def marker_status(issue, today=None):
    """Return a visible marker state, or an empty string for closed issues."""
    status = display_status(issue, today=today)
    return "" if status == CLOSED else status


def palette_for(status):
    return STATUS_PALETTE.get(status, STATUS_PALETTE[CLOSED])
