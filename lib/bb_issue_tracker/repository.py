# -*- coding: utf-8 -*-
"""File-based multi-user issue repository.

The repository deliberately contains no Revit imports so it can be tested and
used by background workers.
"""

import copy
import io
import json
import os
import shutil
import uuid

from bb_issue_tracker.locking import (
    FileLock, RevisionConflictError, atomic_write_json, ensure_directory, utc_now
)
from bb_issue_tracker.models import normalize_issue
from bb_issue_tracker.settings import extension_root, load_settings
from bb_issue_tracker.textutils import safe_folder_name, slugify, to_text


class RepositoryError(Exception):
    pass


class IssueRepository(object):
    def __init__(self, settings_values=None):
        self.settings = settings_values or load_settings()
        self.root = os.path.normpath(self.settings.get("storage_root", ""))
        self.config_dir = os.path.join(self.root, "config")
        self.projects_dir = os.path.join(self.root, "projects")
        self.logs_dir = os.path.join(self.root, "logs")

    @property
    def projects_path(self):
        return os.path.join(self.config_dir, "projects.json")

    @property
    def users_path(self):
        return os.path.join(self.config_dir, "users.json")

    def ensure_layout(self):
        if not self.root:
            raise RepositoryError("Geen centrale opslagroot geconfigureerd.")
        ensure_directory(self.root)
        ensure_directory(self.config_dir)
        ensure_directory(self.projects_dir)
        ensure_directory(self.logs_dir)
        if not os.path.isfile(self.projects_path):
            atomic_write_json(self.projects_path, [])
        if not os.path.isfile(self.users_path):
            sample = os.path.join(extension_root(), "config", "users.sample.json")
            if os.path.isfile(sample):
                with io.open(sample, "r", encoding="utf-8-sig") as stream:
                    users = json.load(stream)
            else:
                users = []
            atomic_write_json(self.users_path, users)

    def test_connection(self):
        try:
            self.ensure_layout()
            return True, "Netwerk beschikbaar"
        except Exception as error:
            return False, to_text(error)

    def _read_json(self, path, default=None):
        try:
            with io.open(path, "r", encoding="utf-8-sig") as stream:
                return json.load(stream)
        except IOError:
            if default is not None:
                return copy.deepcopy(default)
            raise
        except ValueError as error:
            raise RepositoryError("Ongeldige JSON in {0}: {1}".format(path, error))

    def load_users(self):
        self.ensure_layout()
        users = self._read_json(self.users_path, [])
        return users if isinstance(users, list) else []

    def load_projects(self):
        self.ensure_layout()
        projects = self._read_json(self.projects_path, [])
        return projects if isinstance(projects, list) else []

    def register_project(self, project, user_id=""):
        self.ensure_layout()
        project = copy.deepcopy(project or {})
        project_key = project.get("project_key") or slugify(project.get("project_number"), "project")
        project["project_key"] = project_key
        lock_path = self.projects_path + ".lock"
        timeout = self.settings.get("lock_timeout_seconds", 30)
        with FileLock(lock_path, user_id, timeout):
            projects = self._read_json(self.projects_path, [])
            for registered in projects:
                if registered.get("project_key") == project_key:
                    registered.update({
                        "project_number": project.get("project_number", registered.get("project_number", "")),
                        "project_name": project.get("project_name", registered.get("project_name", "")),
                        "active": True
                    })
                    folder_name = registered.get("folder") or self._default_project_folder(project)
                    registered["folder"] = folder_name
                    atomic_write_json(self.projects_path, projects)
                    project["folder"] = folder_name
                    self._ensure_project_layout(folder_name)
                    return project
            folder_name = self._default_project_folder(project)
            registered = {
                "project_key": project_key,
                "project_number": project.get("project_number", ""),
                "project_name": project.get("project_name", ""),
                "folder": folder_name,
                "active": True
            }
            projects.append(registered)
            atomic_write_json(self.projects_path, projects)
            project["folder"] = folder_name
            self._ensure_project_layout(folder_name)
            return project

    def _default_project_folder(self, project):
        number = project.get("project_number") or project.get("project_key") or "Project"
        name = project.get("project_name") or "Onbekend"
        return safe_folder_name("{0}_{1}".format(number, name), "Project")

    def _ensure_project_layout(self, folder_name):
        project_folder = os.path.join(self.projects_dir, folder_name)
        for child in ("issues", "screenshots", "backups", "locks"):
            ensure_directory(os.path.join(project_folder, child))
        counter = os.path.join(project_folder, "counter.json")
        if not os.path.isfile(counter):
            atomic_write_json(counter, {"last_number": 0, "updated_at": utc_now()})
        return project_folder

    def _project_folder(self, project, create=False, user_id=""):
        project = project or {}
        folder_name = project.get("folder")
        if not folder_name:
            for registered in self.load_projects():
                if registered.get("project_key") == project.get("project_key"):
                    folder_name = registered.get("folder")
                    break
        if not folder_name and create:
            registered = self.register_project(project, user_id)
            folder_name = registered.get("folder")
        if not folder_name:
            raise RepositoryError("Project is niet geregistreerd: {0}".format(project.get("project_key", "")))
        folder = os.path.join(self.projects_dir, folder_name)
        if create:
            self._ensure_project_layout(folder_name)
        return folder

    def reserve_issue_id(self, project, user_id=""):
        project = self.register_project(project, user_id)
        project_folder = self._project_folder(project, create=True, user_id=user_id)
        counter_path = os.path.join(project_folder, "counter.json")
        lock_path = os.path.join(project_folder, "locks", "counter.lock")
        timeout = self.settings.get("lock_timeout_seconds", 30)
        with FileLock(lock_path, user_id, timeout):
            counter = self._read_json(counter_path, {"last_number": 0})
            number = int(counter.get("last_number", 0)) + 1
            counter["last_number"] = number
            counter["updated_at"] = utc_now()
            atomic_write_json(counter_path, counter)
        project_number = safe_folder_name(
            project.get("project_number") or project.get("project_key") or "MODEL",
            "MODEL"
        ).upper()
        pattern = self.settings.get("issue_id_format", "{project_number}-#{number}")
        try:
            return pattern.format(project_number=project_number, number=number), project
        except Exception:
            return "{0}-#{1}".format(project_number, number), project

    def load_all_issues(self):
        self.ensure_layout()
        issues = []
        errors = []
        if not os.path.isdir(self.projects_dir):
            return issues, errors
        for folder_name in sorted(os.listdir(self.projects_dir)):
            issue_dir = os.path.join(self.projects_dir, folder_name, "issues")
            if not os.path.isdir(issue_dir):
                continue
            for filename in sorted(os.listdir(issue_dir)):
                if not filename.lower().endswith(".json"):
                    continue
                path = os.path.join(issue_dir, filename)
                try:
                    issue = normalize_issue(self._read_json(path))
                    issue["project"].setdefault("folder", folder_name)
                    issues.append(issue)
                except Exception as error:
                    errors.append({"path": path, "error": to_text(error)})
        return issues, errors

    def issue_path(self, issue):
        project_folder = self._project_folder(issue.get("project", {}), create=False)
        return os.path.join(project_folder, "issues", "{0}.json".format(issue.get("issue_id")))

    def primary_image_path(self, issue):
        issue = normalize_issue(issue)
        preview = issue.get("preview", {})
        primary_id = preview.get("primary_image_id")
        for image in preview.get("images", []):
            if image.get("image_id") == primary_id:
                relative = image.get("relative_path")
                if relative:
                    project_folder = self._project_folder(issue.get("project", {}), create=False)
                    return os.path.normpath(os.path.join(project_folder, relative.replace("/", os.sep)))
        return ""

    def create_issue(self, raw_issue, snapshot_path, snapshot_source, user):
        issue = normalize_issue(raw_issue)
        user_id = user.get("user_id", "")
        issue_id, registered_project = self.reserve_issue_id(issue.get("project", {}), user_id)
        issue["project"] = registered_project
        issue["issue_id"] = issue_id
        issue["revision"] = 1
        now = utc_now()
        issue["created_at"] = now
        issue["updated_at"] = now
        issue["created_by_user_id"] = user_id
        issue["created_by"] = user.get("display_name", "")
        issue["last_modified_by_user_id"] = user_id
        issue["last_modified_by"] = user.get("display_name", "")
        issue["history"].append({
            "user_id": user_id,
            "user": user.get("display_name", ""),
            "date": now,
            "action": "Issue aangemaakt",
            "changes": []
        })
        if snapshot_path:
            self._attach_snapshot(issue, snapshot_path, snapshot_source, user, 1)
        destination = self.issue_path(issue)
        lock_path = os.path.join(
            self._project_folder(issue.get("project", {})), "locks", "{0}.lock".format(issue_id)
        )
        timeout = self.settings.get("lock_timeout_seconds", 30)
        with FileLock(lock_path, user_id, timeout):
            if os.path.exists(destination):
                raise RepositoryError("Issue-ID bestaat al: {0}".format(issue_id))
            atomic_write_json(destination, issue)
        return issue

    def update_issue(self, raw_issue, expected_revision, user, snapshot_path=None, snapshot_source=None):
        issue = normalize_issue(raw_issue)
        user_id = user.get("user_id", "")
        destination = self.issue_path(issue)
        project_folder = self._project_folder(issue.get("project", {}))
        lock_path = os.path.join(project_folder, "locks", "{0}.lock".format(issue.get("issue_id")))
        timeout = self.settings.get("lock_timeout_seconds", 30)
        with FileLock(lock_path, user_id, timeout):
            current = normalize_issue(self._read_json(destination))
            current_revision = int(current.get("revision", 0))
            if current_revision != int(expected_revision):
                raise RevisionConflictError(
                    "Issue is gewijzigd door een ander. Verwacht revisie {0}, gevonden {1}.".format(
                        expected_revision, current_revision
                    )
                )
            new_revision = current_revision + 1
            issue["revision"] = new_revision
            issue["created_at"] = current.get("created_at", issue.get("created_at", ""))
            issue["created_by_user_id"] = current.get("created_by_user_id", "")
            issue["created_by"] = current.get("created_by", "")
            issue["last_modified_by_user_id"] = user_id
            issue["last_modified_by"] = user.get("display_name", "")
            issue["updated_at"] = utc_now()
            if snapshot_path:
                if not issue.get("preview", {}).get("images"):
                    issue["preview"] = copy.deepcopy(current.get("preview", {}))
                self._attach_snapshot(issue, snapshot_path, snapshot_source or {}, user, new_revision)
            changes = self._field_changes(current, issue)
            issue["history"].append({
                "user_id": user_id,
                "user": user.get("display_name", ""),
                "date": issue["updated_at"],
                "action": "Issue bijgewerkt",
                "changes": changes
            })
            backup = os.path.join(
                project_folder, "backups",
                "{0}_r{1:04d}.json".format(issue.get("issue_id"), current_revision)
            )
            atomic_write_json(destination, issue, backup_path=backup)
        return issue

    def refresh_anchor_location(self, issue_id, project, resolved_location, user_id=""):
        """Persist only a host-resolved XYZ fallback without overwriting edits.

        The anchor identity must still match the stored issue. This protects a
        newly chosen location from a stale marker refresh. Automatic updates do
        increment the revision, but do not add noisy history entries.
        """
        stub = {"issue_id": issue_id, "project": copy.deepcopy(project or {})}
        destination = self.issue_path(stub)
        project_folder = self._project_folder(project or {})
        lock_path = os.path.join(project_folder, "locks", "{0}.lock".format(issue_id))
        timeout = self.settings.get("lock_timeout_seconds", 30)
        with FileLock(lock_path, user_id or "bb-issue-tracker", timeout):
            current = normalize_issue(self._read_json(destination))
            current_location = current.get("location", {}) or {}
            incoming_location = copy.deepcopy(resolved_location or {})
            if self._anchor_identity(current_location.get("anchor")) != self._anchor_identity(
                    incoming_location.get("anchor")):
                return current, False

            current_point = current_location.get("last_known_xyz_internal") or current_location.get("point_xyz_internal") or []
            incoming_point = incoming_location.get("last_known_xyz_internal") or incoming_location.get("point_xyz_internal") or []
            current_status = (current_location.get("anchor") or {}).get("host_status", "")
            incoming_status = (incoming_location.get("anchor") or {}).get("host_status", "")
            if not self._point_changed(current_point, incoming_point) and current_status == incoming_status:
                return current, False

            updated_location = copy.deepcopy(current_location)
            if incoming_point:
                updated_location["point_xyz_internal"] = list(incoming_point[:3])
                updated_location["last_known_xyz_internal"] = list(incoming_point[:3])
            stored_anchor = copy.deepcopy(current_location.get("anchor") or {})
            incoming_anchor = incoming_location.get("anchor") or {}
            stored_anchor["host_status"] = incoming_status
            if incoming_anchor.get("last_resolved_at"):
                stored_anchor["last_resolved_at"] = incoming_anchor.get("last_resolved_at")
            updated_location["anchor"] = stored_anchor

            current_revision = int(current.get("revision", 0))
            current["location"] = updated_location
            current["revision"] = current_revision + 1
            current["updated_at"] = utc_now()
            backup = os.path.join(
                project_folder, "backups",
                "{0}_r{1:04d}.json".format(issue_id, current_revision)
            )
            atomic_write_json(destination, current, backup_path=backup)
            return current, True

    @staticmethod
    def _anchor_identity(anchor):
        anchor = anchor or {}
        anchor_type = to_text(anchor.get("type", "xyz"))
        if anchor_type == "face":
            return (
                "face",
                to_text(anchor.get("element_unique_id", "")),
                to_text(anchor.get("stable_reference", "")),
                tuple(anchor.get("uv") or [])
            )
        if anchor_type == "level":
            return (
                "level",
                to_text(anchor.get("level_unique_id", "")),
                tuple(anchor.get("xy_internal") or []),
                anchor.get("offset_internal")
            )
        return ("xyz",)

    @staticmethod
    def _point_changed(first, second, tolerance=0.0001):
        if not first or not second or len(first) < 3 or len(second) < 3:
            return bool(first) != bool(second) or first != second
        try:
            return any(
                abs(float(first[index]) - float(second[index])) > tolerance
                for index in range(3)
            )
        except Exception:
            return first != second

    def _attach_snapshot(self, issue, source_path, source_metadata, user, revision):
        if not source_path or not os.path.isfile(source_path):
            raise RepositoryError("Snapshotbestand bestaat niet: {0}".format(source_path))
        project_folder = self._project_folder(issue.get("project", {}), create=True, user_id=user.get("user_id", ""))
        issue_folder = os.path.join(project_folder, "screenshots", issue.get("issue_id"))
        ensure_directory(issue_folder)
        extension = os.path.splitext(source_path)[1].lower() or ".png"
        image_id = "img-{0}".format(uuid.uuid4().hex)
        filename = "preview_r{0:04d}{1}".format(int(revision), extension)
        destination = os.path.join(issue_folder, filename)
        shutil.copy2(source_path, destination)
        relative = os.path.relpath(destination, project_folder).replace(os.sep, "/")
        preview = issue.setdefault("preview", {"primary_image_id": "", "images": []})
        preview.setdefault("images", [])
        preview["images"].append({
            "image_id": image_id,
            "relative_path": relative,
            "created_at": utc_now(),
            "created_by_user_id": user.get("user_id", ""),
            "source": copy.deepcopy(source_metadata or {})
        })
        preview["primary_image_id"] = image_id

    def _field_changes(self, before, after):
        changes = []
        fields = (
            "title", "description", "status", "priority", "issue_type",
            "assigned_to_user_id", "assigned_to", "due_date", "location"
        )
        for field in fields:
            if before.get(field) != after.get(field):
                changes.append({
                    "field": field,
                    "from": before.get(field),
                    "to": after.get(field)
                })
        before_preview = before.get("preview", {}).get("primary_image_id")
        after_preview = after.get("preview", {}).get("primary_image_id")
        if before_preview != after_preview:
            changes.append({"field": "preview", "from": before_preview, "to": after_preview})
        return changes
