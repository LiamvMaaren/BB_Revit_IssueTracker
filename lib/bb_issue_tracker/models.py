# -*- coding: utf-8 -*-

import copy

from bb_issue_tracker.constants import SCHEMA_VERSION
from bb_issue_tracker.locking import utc_now
from bb_issue_tracker.status import canonical_status
from bb_issue_tracker.textutils import slugify, to_text


def normalize_location(raw_location):
    location = copy.deepcopy(raw_location or {})
    point = location.get("last_known_xyz_internal") or location.get("point_xyz_internal") or []
    if point:
        values = [float(value) for value in point[:3]]
        location["point_xyz_internal"] = values
        location.setdefault("last_known_xyz_internal", list(values))
        anchor = location.get("anchor") or {}
        if not anchor:
            anchor = {
                "type": "xyz",
                "host_status": "fallback",
                "created_point_xyz_internal": list(values)
            }
        anchor.setdefault("type", "xyz")
        anchor.setdefault(
            "host_status",
            "fallback" if anchor.get("type") == "xyz" else "unknown"
        )
        location["anchor"] = anchor
    return location


def empty_issue(project, model, user):
    now = utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "issue_id": "",
        "project": copy.deepcopy(project or {}),
        "model": copy.deepcopy(model or {}),
        "location": {},
        "preview": {
            "primary_image_id": "",
            "images": []
        },
        "title": "",
        "description": "",
        "status": "Open",
        "priority": "Normaal",
        "issue_type": "Controle",
        "created_by_user_id": user.get("user_id", "") if user else "",
        "created_by": user.get("display_name", "") if user else "",
        "assigned_to_user_id": "",
        "assigned_to": "",
        "last_modified_by_user_id": user.get("user_id", "") if user else "",
        "last_modified_by": user.get("display_name", "") if user else "",
        "created_at": now,
        "updated_at": now,
        "due_date": "",
        "linked_element_ids": [],
        "linked_unique_ids": [],
        "comments": [],
        "history": []
    }


def normalize_issue(data):
    """Normalize schema 1.0 and the earlier flat proof-of-concept schema."""
    issue = copy.deepcopy(data or {})
    issue.setdefault("schema_version", SCHEMA_VERSION)
    issue.setdefault("revision", 1)
    issue.setdefault("project", {})
    issue.setdefault("model", {})
    issue.setdefault("location", {})
    issue.setdefault("preview", {"primary_image_id": "", "images": []})
    issue.setdefault("comments", [])
    issue.setdefault("history", [])

    if not issue["project"]:
        issue["project"] = {
            "project_key": issue.get("project_key", ""),
            "project_number": issue.get("project_number", ""),
            "project_name": issue.get("project_name", "")
        }
    if not issue["model"]:
        issue["model"] = {
            "model_key": issue.get("model_guid", "") or issue.get("model_title", ""),
            "model_title": issue.get("model_title", ""),
            "central_model_path": issue.get("central_model_path", "")
        }
    if not issue["location"] and issue.get("view_unique_id"):
        issue["location"] = {
            "view_id": issue.get("view_id", ""),
            "view_unique_id": issue.get("view_unique_id", ""),
            "view_name": issue.get("view_name", ""),
            "view_type": issue.get("view_type", ""),
            "point_xyz_internal": issue.get("point_xyz_internal", issue.get("point_xyz", [])),
            "zoom_corners": issue.get("zoom_corners", []),
            "sheet_id": issue.get("sheet_id", ""),
            "sheet_number": issue.get("sheet_number", ""),
            "sheet_name": issue.get("sheet_name", "")
        }
    issue["location"] = normalize_location(issue.get("location"))
    if not issue["preview"].get("images") and issue.get("screenshot_path"):
        image_id = "legacy-main"
        issue["preview"] = {
            "primary_image_id": image_id,
            "images": [{
                "image_id": image_id,
                "relative_path": issue.get("screenshot_path", ""),
                "created_at": issue.get("created_at", ""),
                "created_by_user_id": issue.get("created_by_user_id", ""),
                "source": {"kind": "legacy"}
            }]
        }

    defaults = {
        "issue_id": "", "title": "", "description": "", "status": "Open",
        "priority": "Normaal", "issue_type": "Controle",
        "created_by_user_id": "", "created_by": "",
        "assigned_to_user_id": "", "assigned_to": "",
        "last_modified_by_user_id": "", "last_modified_by": "",
        "created_at": "", "updated_at": "", "due_date": ""
    }
    for key, value in defaults.items():
        issue.setdefault(key, value)
    issue["status"] = canonical_status(issue.get("status"))
    issue.setdefault("linked_element_ids", [])
    issue.setdefault("linked_unique_ids", [])
    return issue


def make_user(display_name, windows_username="", revit_username=""):
    display = to_text(display_name).strip() or to_text(windows_username).strip() or "Onbekende gebruiker"
    return {
        "user_id": slugify(display),
        "display_name": display,
        "username": to_text(windows_username).strip(),
        "revit_username": to_text(revit_username).strip(),
        "email": "",
        "active": True
    }
