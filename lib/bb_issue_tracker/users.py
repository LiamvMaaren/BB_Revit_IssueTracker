# -*- coding: utf-8 -*-

from bb_issue_tracker.models import make_user
from bb_issue_tracker.textutils import to_text


def _same(left, right):
    return to_text(left).strip().lower() == to_text(right).strip().lower()


def resolve_current_user(users, windows_username, revit_username):
    users = users or []
    for user in users:
        if windows_username and _same(user.get("username"), windows_username):
            return user
    for user in users:
        if revit_username and _same(user.get("revit_username"), revit_username):
            return user
    display = revit_username or windows_username or "Onbekende gebruiker"
    return make_user(display, windows_username, revit_username)


def active_users(users, include=None):
    result = [user for user in (users or []) if user.get("active", True)]
    if include and not any(user.get("user_id") == include.get("user_id") for user in result):
        result.append(include)
    return sorted(result, key=lambda item: to_text(item.get("display_name")).lower())
