# -*- coding: utf-8 -*-

from bb_issue_tracker.models import normalize_issue
from bb_issue_tracker.status import display_status, is_overdue
from bb_issue_tracker.textutils import to_text


def _contains(haystack, needle):
    return to_text(needle).lower() in to_text(haystack).lower()


def issue_matches(raw_issue, filters):
    issue = normalize_issue(raw_issue)
    filters = filters or {}
    project = issue.get("project", {})
    location = issue.get("location", {})
    visible_status = display_status(issue)

    if filters.get("project_key") and project.get("project_key") != filters.get("project_key"):
        return False
    if filters.get("status") and visible_status != filters.get("status"):
        return False
    if filters.get("priority") and issue.get("priority") != filters.get("priority"):
        return False
    if filters.get("issue_type") and issue.get("issue_type") != filters.get("issue_type"):
        return False
    if filters.get("assigned_to_user_id") and issue.get("assigned_to_user_id") != filters.get("assigned_to_user_id"):
        return False
    if filters.get("created_by_user_id") and issue.get("created_by_user_id") != filters.get("created_by_user_id"):
        return False
    if filters.get("active_view_unique_id") and location.get("view_unique_id") != filters.get("active_view_unique_id"):
        return False
    if filters.get("overdue") and not is_overdue(issue):
        return False
    query = to_text(filters.get("query")).strip().lower()
    if query:
        comment_text = " ".join(to_text(item.get("text")) for item in issue.get("comments", []))
        fields = [
            issue.get("issue_id"), issue.get("title"), issue.get("description"),
            issue.get("assigned_to"), issue.get("created_by"), issue.get("issue_type"),
            visible_status, project.get("project_number"), project.get("project_name"),
            location.get("view_name"), comment_text
        ]
        if not any(_contains(field, query) for field in fields):
            return False
    return True


def filter_issues(issues, filters):
    result = [normalize_issue(issue) for issue in (issues or []) if issue_matches(issue, filters)]
    result.sort(key=lambda item: (item.get("updated_at", ""), item.get("issue_id", "")), reverse=True)
    return result
