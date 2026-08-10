# -*- coding: utf-8 -*-

import copy
import datetime
import os
import tempfile
import uuid

from pyrevit import forms, UI

from System import DateTime, TimeSpan, Uri, UriKind
from System.Collections.ObjectModel import ObservableCollection
from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage, MessageBoxResult, Visibility
from System.Windows.Media import BrushConverter
from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage
from System.Windows.Threading import DispatcherTimer
from Microsoft.Win32 import OpenFileDialog

from bb_issue_tracker.constants import (
    COMMAND_MY_ISSUES, COMMAND_NEW_ISSUE, COMMAND_REFRESH,
    PANEL_ID, PANEL_TITLE, REQUEST_CAPTURE_SNAPSHOT, REQUEST_CLEAR_MARKERS,
    REQUEST_GET_CONTEXT, REQUEST_NAVIGATE_LOCATION, REQUEST_NAVIGATE_VIEW,
    REQUEST_PICK_LOCATION, REQUEST_SYNC_MARKERS
)
from bb_issue_tracker.filters import filter_issues
from bb_issue_tracker.locking import LockBusyError, RevisionConflictError, ensure_directory, utc_now
from bb_issue_tracker.models import empty_issue, normalize_issue
from bb_issue_tracker.repository import IssueRepository
from bb_issue_tracker.settings import load_settings
from bb_issue_tracker.status import (
    DISPLAY_STATUSES, canonical_status, display_status, palette_for
)
from bb_issue_tracker.textutils import to_text
from bb_issue_tracker.users import active_users, resolve_current_user


class ComboItem(object):
    def __init__(self, key, label, data=None):
        self.Key = key or ""
        self.Label = label or ""
        self.Data = data

    def __str__(self):
        return self.Label


class IssueRow(object):
    def __init__(self, issue):
        self.Issue = issue
        project = issue.get("project", {})
        location = issue.get("location", {})
        self.Id = issue.get("issue_id", "")
        self.Title = issue.get("title", "")
        self.Assigned = issue.get("assigned_to", "") or "Niet toegewezen"
        self.Status = display_status(issue)
        self.Priority = issue.get("priority", "")
        self.DueDate = issue.get("due_date", "")
        self.Location = location.get("view_name", "") or "Geen locatie"
        footer_parts = [self.Priority or "Geen prioriteit"]
        if self.DueDate:
            footer_parts.append(self.DueDate)
        self.Footer = " · ".join(footer_parts)
        self.Project = "{0} {1}".format(
            project.get("project_number", ""), project.get("project_name", "")
        ).strip()
        palette = palette_for(self.Status)
        background = palette.get("background")
        foreground = palette.get("foreground")
        border = palette.get("border")
        converter = BrushConverter()
        self.StatusBackground = converter.ConvertFromString(background)
        self.StatusForeground = converter.ConvertFromString(foreground)
        self.StatusBorder = converter.ConvertFromString(border)


_initial_state = UI.DockablePaneState()
_initial_state.DockPosition = UI.DockPosition.Right


class BBIssueTrackerContent(forms.WPFPanel):
    panel_id = PANEL_ID
    panel_title = PANEL_TITLE
    panel_source = os.path.join(os.path.dirname(__file__), "ui", "dashboard.xaml")
    initial_state = _initial_state

    def __init__(self, bridge):
        self._ready = False
        self._loading_ui = False
        self._dirty = False
        self._is_new = False
        self._issues = []
        self._projects = []
        self._users = []
        self._rows = ObservableCollection[object]()
        self._current_issue = None
        self._current_row = None
        self._selected_issue = None
        self._selected_row = None
        self._detail_window = None
        self._expected_revision = 0
        self._pending_snapshot_path = ""
        self._pending_snapshot_source = {}
        self._context = {"has_document": False, "project": {}, "model": {}, "view": {}}
        self._current_user = None
        self._load_again = False
        self._select_issue_id_after_refresh = ""
        self._start_new_after_context = False
        self._network_available = False
        self._project_filter_initialized = False
        self._panel_visible = True
        self._marker_count = 0
        self._marker_warning = ""
        self._applying_anchor_updates = False
        self._settings = load_settings()
        self._repository = IssueRepository(self._settings)
        self._temp_dir = os.path.join(tempfile.gettempdir(), "BBIssueTracker", uuid.uuid4().hex)
        ensure_directory(self._temp_dir)
        self._marker_icon_dir = os.path.join(self._temp_dir, "markers")
        self._marker_fallback_dir = os.path.join(os.path.dirname(__file__), "assets", "markers")
        ensure_directory(self._marker_icon_dir)
        forms.WPFPanel.__init__(self)

        self.issue_grid.ItemsSource = self._rows
        self._bridge = bridge
        self._bridge.set_panel(self)

        self._timer = DispatcherTimer()
        self._timer.Interval = TimeSpan.FromSeconds(
            max(5, int(self._settings.get("refresh_seconds", 10)))
        )
        self._timer.Tick += self._timer_tick
        self._timer.Start()

        self._marker_timer = DispatcherTimer()
        self._marker_timer.Interval = TimeSpan.FromSeconds(
            max(1, int(self._settings.get("marker_refresh_seconds", 1)))
        )
        self._marker_timer.Tick += self._marker_timer_tick
        self._marker_timer.Start()

        self._populate_static_combos()
        self._clear_editor()
        self._update_browser_selection()
        self._set_network(False, "Netwerkverbinding wordt gecontroleerd")
        self._set_marker_indicator("Markers: 0", "#98A2B3", "Markers worden gecontroleerd.")
        self._ready = True

    # ----- loading and context -------------------------------------------------

    def refresh_data(self):
        if self._loading_ui:
            self._load_again = True
            return
        self._set_status("Issues worden ingelezen...")
        self._loading_ui = True
        try:
            issues, errors = self._repository.load_all_issues()
            result = {
                "issues": issues,
                "errors": errors,
                "users": self._repository.load_users(),
                "projects": self._repository.load_projects()
            }
            self._issues = result.get("issues", [])
            self._users = result.get("users", [])
            self._projects = result.get("projects", [])
            self._resolve_user()
            self._populate_dynamic_combos()
            self.apply_filters()
            error_count = len(result.get("errors", []))
            if error_count:
                self._set_status("{0} issuebestand(en) konden niet worden gelezen.".format(error_count))
            else:
                self._set_status("{0} issues geladen".format(len(self._issues)))
            self._set_network(True, "Netwerk beschikbaar")
            self._request_marker_sync()
        except Exception as error:
            self._set_network(False, to_text(error))
            self._set_status("Centrale opslag niet bereikbaar; bestaande lijst blijft zichtbaar.")
        finally:
            self._loading_ui = False
        if self._load_again:
            self._load_again = False
            self.refresh_data()

    def on_context_received(self, context):
        self._context = context or {"has_document": False, "project": {}, "model": {}, "view": {}}
        self._resolve_user()
        project = self._context.get("project", {})
        if self._context.get("has_document"):
            label = "{0} · {1}".format(
                project.get("project_number", "Geen projectnummer"),
                project.get("project_name", "Onbekend project")
            )
        else:
            label = "Geen actief Revit-project"
        self.project_context_text.Text = label
        if self._projects:
            self._populate_dynamic_combos()
        if self.active_view_toggle.IsChecked:
            self.apply_filters()
        self._request_marker_sync()
        if self._start_new_after_context:
            self._start_new_after_context = False
            self.start_new_issue(context_verified=True)

    def _resolve_user(self):
        self._current_user = resolve_current_user(
            self._users,
            self._context.get("windows_username", os.environ.get("USERNAME", "")),
            self._context.get("revit_username", "")
        )

    # ----- filters -------------------------------------------------------------

    def _populate_static_combos(self):
        status_filters = self._settings.get("status_filters", list(DISPLAY_STATUSES))
        self._set_combo(self.status_filter, [ComboItem("", "Alle statussen")] + [
            ComboItem(value, value) for value in status_filters
        ], "")
        self._set_combo(self.priority_filter, [ComboItem("", "Alle prioriteiten")] + [
            ComboItem(value, value) for value in self._settings.get("priorities", [])
        ], "")
        self._set_combo(self.type_filter, [ComboItem("", "Alle types")] + [
            ComboItem(value, value) for value in self._settings.get("issue_types", [])
        ], "")
        self._populate_editor_static_combos()

    def _populate_editor_static_combos(self):
        if self._detail_window is None:
            return
        self._set_combo(self.status_combo, [ComboItem(value, value) for value in self._settings.get("statuses", [])], "Open")
        self._set_combo(self.priority_combo, [ComboItem(value, value) for value in self._settings.get("priorities", [])], "Normaal")
        self._set_combo(self.type_combo, [ComboItem(value, value) for value in self._settings.get("issue_types", [])], "Controle")

    def _populate_dynamic_combos(self):
        selected_project = self._selected_key(self.project_filter)
        project_items = [ComboItem("", "Alle projecten")]
        projects = list(self._projects)
        active_project = self._context.get("project", {})
        active_key = active_project.get("project_key", "")
        if active_key and not any(item.get("project_key") == active_key for item in projects):
            projects.append(active_project)
        for project in sorted(projects, key=lambda item: to_text(item.get("project_number"))):
            label = "{0} · {1}".format(project.get("project_number", ""), project.get("project_name", "")).strip(" ·")
            project_items.append(ComboItem(project.get("project_key", ""), label, project))
        if not self._project_filter_initialized and active_key:
            selected_project = active_key
            self._project_filter_initialized = True
        self._set_combo(self.project_filter, project_items, selected_project)

        self._resolve_user()
        user_items = active_users(self._users, self._current_user)
        assigned_selected = self._selected_key(self.assigned_combo) if self._detail_window is not None else ""
        filter_selected = self._selected_key(self.person_filter)
        combo_items = [ComboItem(user.get("user_id", ""), user.get("display_name", ""), user) for user in user_items]
        self._set_combo(self.person_filter, [ComboItem("", "Iedereen")] + combo_items, filter_selected)
        if self._detail_window is not None:
            self._set_combo(self.assigned_combo, [ComboItem("", "Niet toegewezen")] + combo_items, assigned_selected)

    def apply_filters(self):
        if not self._ready:
            return
        current_user_id = self._current_user.get("user_id", "") if self._current_user else ""
        filters = {
            "project_key": self._selected_key(self.project_filter),
            "status": self._selected_key(self.status_filter),
            "priority": self._selected_key(self.priority_filter),
            "issue_type": self._selected_key(self.type_filter),
            "assigned_to_user_id": current_user_id if self.my_issues_toggle.IsChecked else self._selected_key(self.person_filter),
            "created_by_user_id": current_user_id if self.created_by_me_toggle.IsChecked else "",
            "active_view_unique_id": self._context.get("view", {}).get("view_unique_id", "") if self.active_view_toggle.IsChecked else "",
            "overdue": bool(self.overdue_toggle.IsChecked),
            "query": self.search_text.Text
        }
        filtered = filter_issues(self._issues, filters)
        desired_id = self._select_issue_id_after_refresh
        if not desired_id and self._selected_issue:
            desired_id = self._selected_issue.get("issue_id", "")
        if not desired_id and self._current_issue and not self._is_new:
            desired_id = self._current_issue.get("issue_id", "")
        self._rows.Clear()
        selected_row = None
        for issue in filtered:
            row = IssueRow(issue)
            self._rows.Add(row)
            if desired_id and issue.get("issue_id") == desired_id:
                selected_row = row
        self.issue_count_text.Text = "{0} van {1}".format(len(filtered), len(self._issues))
        if selected_row:
            self._loading_ui = True
            try:
                self.issue_grid.SelectedItem = selected_row
                self._selected_row = selected_row
                self._selected_issue = normalize_issue(selected_row.Issue)
            finally:
                self._loading_ui = False
            self.issue_grid.ScrollIntoView(selected_row)
            self._select_issue_id_after_refresh = ""
            self._update_browser_selection()
        elif desired_id:
            self._loading_ui = True
            try:
                self.issue_grid.SelectedItem = None
                self._selected_row = None
                self._selected_issue = None
            finally:
                self._loading_ui = False
            self._update_browser_selection()

    def filter_changed(self, sender, args):
        if self._ready and not self._loading_ui:
            if sender == self.active_view_toggle and self.active_view_toggle.IsChecked:
                self._bridge.enqueue(REQUEST_GET_CONTEXT)
            self.apply_filters()

    def search_changed(self, sender, args):
        if self._ready:
            self.apply_filters()

    def reset_filters_click(self, sender, args):
        self._loading_ui = True
        try:
            self._select_combo(self.project_filter, "")
            self._select_combo(self.person_filter, "")
            self._select_combo(self.status_filter, "")
            self._select_combo(self.priority_filter, "")
            self._select_combo(self.type_filter, "")
            self.search_text.Text = ""
            self.my_issues_toggle.IsChecked = False
            self.created_by_me_toggle.IsChecked = False
            self.overdue_toggle.IsChecked = False
            self.active_view_toggle.IsChecked = False
        finally:
            self._loading_ui = False
        self.apply_filters()

    # ----- issue selection and editing ----------------------------------------

    def issue_selection_changed(self, sender, args):
        if not self._ready or self._loading_ui:
            return
        row = self.issue_grid.SelectedItem
        if row is None:
            self._selected_issue = None
            self._selected_row = None
            self._update_browser_selection()
            return
        self._selected_issue = normalize_issue(row.Issue)
        self._selected_row = row
        self._update_browser_selection()

    def issue_double_click(self, sender, args):
        self.open_selected_issue_click(sender, args)

    def open_selected_issue_click(self, sender, args):
        row = self.issue_grid.SelectedItem
        if row is None:
            row = self._selected_row
        if row is None:
            self._set_status("Selecteer eerst een issue.")
            return
        incoming_id = row.Issue.get("issue_id", "")
        current_id = self._current_issue.get("issue_id", "") if self._current_issue else ""
        if self._dirty and (self._is_new or incoming_id != current_id):
            if not self._confirm_discard_changes():
                return
        self._load_issue(row.Issue, row)
        self._show_detail_window()

    def browser_open_location_click(self, sender, args):
        issue = self._selected_issue
        if not issue or not issue.get("location"):
            self._set_status("Dit issue heeft nog geen opgeslagen locatie.")
            return
        self._navigate_issue(issue, issue.get("location"), REQUEST_NAVIGATE_LOCATION)

    def _load_issue(self, issue, row=None):
        self._ensure_detail_window()
        self._current_issue = normalize_issue(issue)
        self._current_row = row
        self._expected_revision = int(self._current_issue.get("revision", 0))
        self._is_new = False
        self._pending_snapshot_path = ""
        self._pending_snapshot_source = {}
        self._loading_ui = True
        try:
            self.detail_issue_id.Text = self._current_issue.get("issue_id", "")
            self.detail_header_title.Text = self._current_issue.get("title", "") or "Issuedetail"
            self.detail_metadata.Text = "Revisie {0} · bijgewerkt {1}".format(
                self._current_issue.get("revision", ""), self._current_issue.get("updated_at", "")
            )
            self.title_text.Text = self._current_issue.get("title", "")
            self.description_text.Text = self._current_issue.get("description", "")
            self._select_combo(self.assigned_combo, self._current_issue.get("assigned_to_user_id", ""))
            self._select_combo(self.status_combo, self._current_issue.get("status", "Open"))
            self._select_combo(self.priority_combo, self._current_issue.get("priority", "Normaal"))
            self._select_combo(self.type_combo, self._current_issue.get("issue_type", "Controle"))
            self._set_due_date(self._current_issue.get("due_date", ""))
            self._update_detail_context()
            self._update_location_labels()
            self._update_comments()
            try:
                preview_path = self._repository.primary_image_path(self._current_issue)
            except Exception:
                preview_path = ""
            self._show_image(preview_path)
        finally:
            self._loading_ui = False
        self._set_dirty(False)

    def start_new_issue(self, context_verified=False):
        if not self._network_available:
            self._validation_message("De centrale opslag is niet bereikbaar. Nieuwe issues zijn tijdelijk uitgeschakeld.")
            return
        if not context_verified:
            self._start_new_after_context = True
            self._bridge.enqueue(REQUEST_GET_CONTEXT)
            self._set_status("Actieve Revit-context wordt opgehaald...")
            return
        if not self._context.get("has_document"):
            self._validation_message("Open eerst een Revit-project om een issue aan te maken.")
            return
        if self._dirty and not self._confirm_discard_changes():
            return
        self._ensure_detail_window()
        self._resolve_user()
        self._current_issue = empty_issue(
            self._context.get("project", {}), self._context.get("model", {}), self._current_user
        )
        self._current_row = None
        self._expected_revision = 0
        self._is_new = True
        self._pending_snapshot_path = ""
        self._pending_snapshot_source = {}
        self._selected_issue = None
        self._selected_row = None
        self._loading_ui = True
        try:
            self.issue_grid.SelectedItem = None
            self.detail_issue_id.Text = "Nieuw issue"
            self.detail_header_title.Text = "Nieuw controlepunt"
            self.detail_metadata.Text = "Kies eerst de locatie in Revit"
            self.title_text.Text = ""
            self.description_text.Text = ""
            self._select_combo(self.assigned_combo, "")
            self._select_combo(self.status_combo, "Open")
            self._select_combo(self.priority_combo, "Normaal")
            self._select_combo(self.type_combo, "Controle")
            self._set_due_date("")
            self.comments_list.ItemsSource = []
            self.history_list.ItemsSource = []
            self.new_comment_text.Text = ""
            self._show_image("")
            self._update_detail_context()
            self._update_location_labels()
        finally:
            self._loading_ui = False
        self._update_browser_selection()
        self._set_dirty(True)
        self._show_detail_window()
        self._queue_pick_location(auto_snapshot=True)

    def editor_changed(self, sender, args):
        if self._ready and not self._loading_ui and self._current_issue is not None:
            if sender == self.title_text:
                self.detail_header_title.Text = to_text(self.title_text.Text).strip() or "Issuedetail"
            self._set_dirty(True)

    def cancel_changes_click(self, sender, args):
        if self._is_new:
            if self._confirm_discard_changes():
                self._clear_editor()
                if self._detail_window:
                    self._detail_window.Close()
        elif self._current_issue:
            issue_id = self._current_issue.get("issue_id")
            original = next((item for item in self._issues if item.get("issue_id") == issue_id), None)
            if original:
                self._load_issue(original, self._current_row)

    def _clear_editor(self):
        self._current_issue = None
        self._current_row = None
        self._expected_revision = 0
        self._is_new = False
        self._pending_snapshot_path = ""
        self._pending_snapshot_source = {}
        if self._detail_window is not None:
            self._loading_ui = True
            try:
                self.detail_issue_id.Text = "Selecteer een issue"
                self.detail_header_title.Text = "Issuedetail"
                self.detail_metadata.Text = ""
                self.title_text.Text = ""
                self.description_text.Text = ""
                self.location_text.Text = "Niet gekozen"
                self.snapshot_source_text.Text = "Niet gekozen"
                self.comments_list.ItemsSource = []
                self.history_list.ItemsSource = []
                self._show_image("")
            finally:
                self._loading_ui = False
        self._set_dirty(False)

    # ----- Revit location and snapshot ----------------------------------------

    def _queue_pick_location(self, auto_snapshot=False):
        if self._current_issue is None:
            return
        target = self._new_temp_snapshot_path() if auto_snapshot else ""
        self._set_status("Klik de issue-locatie in Revit...")
        self._bridge.enqueue(
            REQUEST_PICK_LOCATION,
            auto_snapshot=auto_snapshot,
            target_path=target,
            expected_model_key=self._current_issue.get("model", {}).get("model_key", ""),
            pixel_size=self._settings.get("screenshot_pixel_size", 1800)
        )

    def pick_location_click(self, sender, args):
        if self._current_issue is None:
            self.start_new_issue()
            return
        if self._current_issue.get("location"):
            result = MessageBox.Show(
                "De navigatielocatie van dit issue wordt gewijzigd. Doorgaan?",
                PANEL_TITLE, MessageBoxButton.YesNo, MessageBoxImage.Warning
            )
            if result != MessageBoxResult.Yes:
                return
        self._queue_pick_location(auto_snapshot=False)

    def capture_snapshot_click(self, sender, args):
        if self._current_issue is None:
            self.start_new_issue()
            return
        target = self._new_temp_snapshot_path()
        self._set_status("Huidige Revit-view wordt als nieuwe snapshot vastgelegd...")
        self._bridge.enqueue(
            REQUEST_CAPTURE_SNAPSHOT,
            target_path=target,
            expected_model_key=self._current_issue.get("model", {}).get("model_key", ""),
            pixel_size=self._settings.get("screenshot_pixel_size", 1800)
        )

    def choose_image_click(self, sender, args):
        if self._current_issue is None:
            self.start_new_issue()
            return
        dialog = OpenFileDialog()
        dialog.Title = "Kies een afbeelding voor de issue-preview"
        dialog.Filter = "Afbeeldingen (*.png;*.jpg;*.jpeg;*.bmp)|*.png;*.jpg;*.jpeg;*.bmp"
        if dialog.ShowDialog():
            self._pending_snapshot_path = dialog.FileName
            self._pending_snapshot_source = {
                "kind": "external_file",
                "original_filename": os.path.basename(dialog.FileName)
            }
            self._show_image(dialog.FileName)
            self._update_location_labels()
            self._set_dirty(True)

    def on_location_picked(self, location, snapshot_path, snapshot_source):
        if self._current_issue is None:
            return
        self._current_issue["location"] = location
        if snapshot_path:
            self._pending_snapshot_path = snapshot_path
            self._pending_snapshot_source = snapshot_source or {}
            self._show_image(snapshot_path)
        self._update_location_labels()
        self._set_dirty(True)
        self._set_status("Issue-locatie vastgelegd")
        self._request_marker_sync()

    def on_location_pick_cancelled(self):
        self._set_status("Locatiekeuze geannuleerd")
        if self._is_new and not self._current_issue.get("location"):
            self.detail_metadata.Text = "Nog geen locatie gekozen"

    def on_snapshot_captured(self, path, source):
        self._pending_snapshot_path = path
        self._pending_snapshot_source = source or {}
        self._show_image(path)
        self._update_location_labels()
        self._set_dirty(True)
        self._set_status("Nieuwe snapshot vastgelegd; issue-locatie is ongewijzigd")
        self._request_marker_sync()

    def open_location_click(self, sender, args):
        if not self._current_issue or not self._current_issue.get("location"):
            self._set_status("Dit issue heeft nog geen locatie.")
            return
        self._navigate(self._current_issue.get("location"), REQUEST_NAVIGATE_LOCATION)

    def open_preview_view_click(self, sender, args):
        source = self._pending_snapshot_source or self._primary_snapshot_source()
        if source.get("kind") != "revit_view" or not source.get("view_unique_id"):
            self._set_status("De hoofdpreview is niet afkomstig uit een Revit-view.")
            return
        self._navigate(source, REQUEST_NAVIGATE_VIEW)

    def _navigate(self, target, request_kind):
        if not self._current_issue:
            return
        self._navigate_issue(self._current_issue, target, request_kind)

    def _navigate_issue(self, issue, target, request_kind):
        model = issue.get("model", {})
        self._set_status("Revit-view wordt geopend...")
        self._bridge.enqueue(
            request_kind,
            target=target,
            model_key=model.get("model_key", ""),
            model_title=model.get("model_title", ""),
            section_box_size_m=self._settings.get("review_section_box_size_m", 4.0)
        )

    def on_navigation_completed(self, success, message):
        self._set_status(message)
        self._request_marker_sync()

    def on_revit_request_failed(self, kind, message):
        self._set_status(message)
        if kind in (REQUEST_SYNC_MARKERS, REQUEST_CLEAR_MARKERS):
            self._marker_warning = message
            self._set_marker_indicator("Markerfout", "#D9362B", message)
            return
        MessageBox.Show(message, PANEL_TITLE, MessageBoxButton.OK, MessageBoxImage.Warning)

    # ----- saving and comments -------------------------------------------------

    def add_comment_click(self, sender, args):
        if self._current_issue is None:
            return
        text = to_text(self.new_comment_text.Text).strip()
        if not text:
            return
        self._resolve_user()
        self._current_issue.setdefault("comments", []).append({
            "user_id": self._current_user.get("user_id", ""),
            "user": self._current_user.get("display_name", ""),
            "date": utc_now(),
            "text": text
        })
        self.new_comment_text.Text = ""
        self._update_comments()
        self._set_dirty(True)

    def save_click(self, sender, args):
        self._save_current()

    def _save_current(self):
        if self._current_issue is None:
            return
        title = to_text(self.title_text.Text).strip()
        if not title:
            self._validation_message("Vul een titel in.")
            return
        if not self._current_issue.get("location"):
            self._validation_message("Kies eerst een issue-locatie in Revit.")
            return
        has_existing_preview = bool(self._current_issue.get("preview", {}).get("primary_image_id"))
        if not self._pending_snapshot_path and not has_existing_preview:
            self._validation_message("Maak of kies eerst een snapshot voor de hoofdpreview.")
            return
        self._resolve_user()
        self._collect_editor_values()
        self._set_status("Issue wordt opgeslagen...")
        try:
            if self._is_new:
                saved = self._repository.create_issue(
                    self._current_issue,
                    self._pending_snapshot_path,
                    self._pending_snapshot_source,
                    self._current_user
                )
            else:
                saved = self._repository.update_issue(
                    self._current_issue,
                    self._expected_revision,
                    self._current_user,
                    self._pending_snapshot_path or None,
                    self._pending_snapshot_source or None
                )
        except LockBusyError as error:
            owner = error.owner or {}
            message = "Issue is vergrendeld door {0} op {1}.".format(
                owner.get("user_id", "een andere gebruiker"), owner.get("computer", "een andere computer")
            )
            self._validation_message(message)
            return
        except RevisionConflictError as error:
            self._validation_message(to_text(error) + "\nVervers de lijst en controleer de wijzigingen.")
            return
        except Exception as error:
            self._validation_message("Opslaan mislukt:\n{0}".format(to_text(error)))
            return
        self._load_issue(saved, None)
        self._select_issue_id_after_refresh = saved.get("issue_id", "")
        self._set_status("{0} opgeslagen".format(saved.get("issue_id", "Issue")))
        self.refresh_data()
        self._request_marker_sync()

    def _collect_editor_values(self):
        issue = self._current_issue
        issue["title"] = to_text(self.title_text.Text).strip()
        issue["description"] = to_text(self.description_text.Text).strip()
        issue["status"] = canonical_status(self._selected_key(self.status_combo) or "Open")
        issue["priority"] = self._selected_key(self.priority_combo) or "Normaal"
        issue["issue_type"] = self._selected_key(self.type_combo) or "Controle"
        assigned = self.assigned_combo.SelectedItem
        if assigned:
            issue["assigned_to_user_id"] = assigned.Key
            issue["assigned_to"] = assigned.Label if assigned.Key else ""
        else:
            issue["assigned_to_user_id"] = ""
            issue["assigned_to"] = ""
        selected_date = self.due_date_picker.SelectedDate
        if selected_date is None:
            issue["due_date"] = ""
        else:
            try:
                date_value = selected_date.Value if selected_date.HasValue else None
            except Exception:
                date_value = selected_date
            issue["due_date"] = date_value.ToString("yyyy-MM-dd") if date_value else ""

    # ----- UI helpers ----------------------------------------------------------

    def _ensure_detail_window(self):
        if self._detail_window is not None:
            return self._detail_window
        from bb_issue_tracker.detail_window import IssueDetailWindow
        window = IssueDetailWindow(self)
        self._detail_window = window
        control_names = (
            "detail_issue_id", "detail_header_title", "detail_metadata",
            "dirty_indicator", "preview_image", "preview_empty_text",
            "snapshot_count_text", "location_text", "snapshot_source_text",
            "title_text", "description_text", "assigned_combo", "status_combo",
            "priority_combo", "type_combo", "due_date_picker",
            "comments_list", "history_list", "new_comment_text",
            "pick_location_button", "capture_snapshot_button",
            "choose_image_button", "add_comment_button", "save_button",
            "detail_project_text", "detail_model_text",
            "detail_created_by_text", "detail_created_text",
            "detail_status_text", "detail_network_dot", "detail_network_text"
        )
        for name in control_names:
            setattr(self, name, getattr(window, name))
        self._populate_editor_static_combos()
        self._populate_dynamic_combos()
        self._sync_detail_network()
        return window

    def _show_detail_window(self):
        window = self._ensure_detail_window()
        try:
            if not window.IsVisible:
                window.Show()
            window.Activate()
        except Exception:
            self._detail_window = None
            window = self._ensure_detail_window()
            window.Show()
            window.Activate()

    def can_close_detail_window(self):
        if self._dirty and not self._confirm_discard_changes():
            return False
        self._set_dirty(False)
        return True

    def on_detail_window_closed(self, window):
        if window != self._detail_window:
            return
        self._detail_window = None
        self._current_issue = None
        self._current_row = None
        self._expected_revision = 0
        self._is_new = False
        self._pending_snapshot_path = ""
        self._pending_snapshot_source = {}
        self._dirty = False

    def _update_browser_selection(self):
        issue = self._selected_issue
        if not issue:
            self.selected_empty_text.Visibility = Visibility.Visible
            self.selected_issue_content.Visibility = Visibility.Collapsed
            self.browser_preview_image.Source = None
            return
        self.selected_empty_text.Visibility = Visibility.Collapsed
        self.selected_issue_content.Visibility = Visibility.Visible
        self.browser_selected_id.Text = issue.get("issue_id", "")
        self.browser_selected_title.Text = issue.get("title", "") or "Zonder titel"
        location = issue.get("location", {})
        self.browser_selected_meta.Text = location.get("view_name", "") or "Geen Revit-locatie"
        assigned = issue.get("assigned_to", "") or "Niet toegewezen"
        self.browser_selected_status.Text = "{0} · {1} · {2}".format(
            display_status(issue),
            issue.get("priority", "Geen prioriteit"),
            assigned
        )
        try:
            preview_path = self._repository.primary_image_path(issue)
        except Exception:
            preview_path = ""
        self._show_browser_image(preview_path)

    def _update_detail_context(self):
        if not self._current_issue or self._detail_window is None:
            return
        project = self._current_issue.get("project", {})
        model = self._current_issue.get("model", {})
        self.detail_project_text.Text = "{0} · {1}".format(
            project.get("project_number", ""),
            project.get("project_name", "")
        ).strip(" ·")
        self.detail_model_text.Text = model.get("model_title", "") or "Onbekend model"
        self.detail_created_by_text.Text = self._current_issue.get("created_by", "") or "Nog niet opgeslagen"
        self.detail_created_text.Text = self._current_issue.get("created_at", "") or "Nog niet opgeslagen"

    def handle_command(self, command):
        """Handle a launcher command after the dockable shell is visible."""
        self._bridge.enqueue_latest(REQUEST_GET_CONTEXT)
        self.refresh_data()
        if command == COMMAND_NEW_ISSUE:
            self.start_new_issue()
        elif command == COMMAND_MY_ISSUES:
            self.my_issues_toggle.IsChecked = True
            self.apply_filters()

    def _timer_tick(self, sender, args):
        self._bridge.enqueue_latest(REQUEST_GET_CONTEXT)
        self.refresh_data()

    def _marker_timer_tick(self, sender, args):
        self._request_marker_sync()

    def set_panel_visible(self, visible):
        self._panel_visible = bool(visible)
        if self._panel_visible:
            self._request_marker_sync()
        else:
            self._bridge.enqueue_latest(REQUEST_CLEAR_MARKERS)

    def _marker_payload(self):
        payload = []
        for issue in self._issues:
            payload.append({
                "issue_id": issue.get("issue_id", ""),
                "revision": issue.get("revision", 0),
                "project": copy.deepcopy(issue.get("project", {})),
                "title": issue.get("title", ""),
                "status": issue.get("status", "Open"),
                "due_date": issue.get("due_date", ""),
                "model": {
                    "model_key": issue.get("model", {}).get("model_key", ""),
                    "model_title": issue.get("model", {}).get("model_title", ""),
                    "central_model_path": issue.get("model", {}).get("central_model_path", "")
                },
                "location": copy.deepcopy(issue.get("location", {}))
            })
        return payload

    def _request_marker_sync(self):
        if not self._ready or not self._panel_visible:
            return
        self._bridge.enqueue_latest(
            REQUEST_SYNC_MARKERS,
            issues=self._marker_payload(),
            icon_dir=self._marker_icon_dir,
            fallback_dir=self._marker_fallback_dir
        )

    def on_marker_sync_completed(self, success, count, warnings, location_updates=None):
        self._apply_anchor_location_updates(location_updates or [])
        self._marker_count = int(count or 0)
        warning_text = " | ".join(warnings or [])
        if warning_text:
            self._marker_warning = warning_text
            self._set_marker_indicator(
                "Markers: {0} · waarschuwing".format(self._marker_count),
                "#F59E0B",
                warning_text
            )
            self._set_status("Markerwaarschuwing: {0}".format(warning_text))
        else:
            self._marker_warning = ""
            self._set_marker_indicator(
                "Markers: {0}".format(self._marker_count),
                "#22A06B" if self._marker_count else "#98A2B3",
                "{0} issue-marker(s) in de actieve view.".format(self._marker_count)
            )


    def _apply_anchor_location_updates(self, updates):
        if self._applying_anchor_updates or not updates:
            return
        self._applying_anchor_updates = True
        try:
            self._resolve_user()
            user_id = self._current_user.get("user_id", "") if self._current_user else ""
            for update in updates:
                issue_id = update.get("issue_id", "")
                issue = next(
                    (item for item in self._issues if item.get("issue_id") == issue_id),
                    None
                )
                if issue is None:
                    continue
                try:
                    saved, changed = self._repository.refresh_anchor_location(
                        issue_id,
                        issue.get("project", {}),
                        update.get("location", {}),
                        user_id
                    )
                except (LockBusyError, RevisionConflictError):
                    # A later marker refresh retries after the other writer is done.
                    continue
                except Exception:
                    continue
                if not changed:
                    continue

                for index, existing in enumerate(self._issues):
                    if existing.get("issue_id") == issue_id:
                        self._issues[index] = saved
                        break
                for row in self._rows:
                    if row.Issue.get("issue_id") == issue_id:
                        row.Issue = saved
                        break
                if self._selected_issue and self._selected_issue.get("issue_id") == issue_id:
                    self._selected_issue = normalize_issue(saved)
                    self._update_browser_selection()
                if self._current_issue and self._current_issue.get("issue_id") == issue_id:
                    self._current_issue["location"] = copy.deepcopy(saved.get("location", {}))
                    self._current_issue["revision"] = saved.get("revision", 0)
                    self._current_issue["updated_at"] = saved.get("updated_at", "")
                    self._expected_revision = int(saved.get("revision", 0))
                    self._update_location_labels()
        finally:
            self._applying_anchor_updates = False

    def on_marker_clicked(self, issue_id):
        issue = next(
            (item for item in self._issues if item.get("issue_id") == issue_id),
            None
        )
        if issue is None:
            self.refresh_data()
            issue = next(
                (item for item in self._issues if item.get("issue_id") == issue_id),
                None
            )
        if issue is None:
            self._set_status("Het aangeklikte issue is niet meer beschikbaar.")
            return
        current_id = self._current_issue.get("issue_id", "") if self._current_issue else ""
        if self._dirty and (self._is_new or issue_id != current_id):
            if not self._confirm_discard_changes():
                return

        row = next(
            (item for item in self._rows if item.Issue.get("issue_id") == issue_id),
            None
        )
        self._loading_ui = True
        try:
            if row is not None:
                self.issue_grid.SelectedItem = row
                self.issue_grid.ScrollIntoView(row)
                self._selected_row = row
            else:
                self.issue_grid.SelectedItem = None
                self._selected_row = None
            self._selected_issue = normalize_issue(issue)
        finally:
            self._loading_ui = False
        self._update_browser_selection()
        self._load_issue(issue, row)
        self._show_detail_window()
        if row is None:
            self._set_status("Issue geopend via marker; de huidige filters verbergen het in de lijst.")
        else:
            self._set_status("{0} geopend via marker".format(issue_id))

    def refresh_click(self, sender, args):
        self._bridge.enqueue(REQUEST_GET_CONTEXT)
        self.refresh_data()

    def new_issue_click(self, sender, args):
        self.start_new_issue()

    def _set_combo(self, combo, items, selected_key=""):
        self._loading_ui = True
        try:
            combo.ItemsSource = items
            self._select_combo(combo, selected_key)
            if combo.SelectedIndex < 0 and items:
                combo.SelectedIndex = 0
        finally:
            self._loading_ui = False

    def _select_combo(self, combo, key):
        combo.SelectedValue = key or ""
        if combo.SelectedIndex < 0 and combo.Items.Count:
            combo.SelectedIndex = 0

    def _selected_key(self, combo):
        item = combo.SelectedItem
        return item.Key if item else ""

    def _set_due_date(self, value):
        if value:
            try:
                parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
                self.due_date_picker.SelectedDate = DateTime(parsed.year, parsed.month, parsed.day)
                return
            except Exception:
                pass
        self.due_date_picker.SelectedDate = None

    def _update_location_labels(self):
        location = self._current_issue.get("location", {}) if self._current_issue else {}
        if location:
            sheet = location.get("sheet_number")
            suffix = " · blad {0}".format(sheet) if sheet else ""
            self.location_text.Text = "{0}{1}".format(location.get("view_name", "Onbekende view"), suffix)
        else:
            self.location_text.Text = "Niet gekozen"
        source = self._pending_snapshot_source or self._primary_snapshot_source()
        kind = source.get("kind")
        if kind == "revit_view":
            self.snapshot_source_text.Text = source.get("view_name", "Revit-view")
        elif kind == "external_file":
            self.snapshot_source_text.Text = source.get("original_filename", "Externe afbeelding")
        elif kind:
            self.snapshot_source_text.Text = "Afbeelding ({0})".format(kind)
        else:
            self.snapshot_source_text.Text = "Niet gekozen"

    def _primary_snapshot_source(self):
        if not self._current_issue:
            return {}
        preview = self._current_issue.get("preview", {})
        primary = preview.get("primary_image_id")
        for image in preview.get("images", []):
            if image.get("image_id") == primary:
                return image.get("source", {}) or {}
        return {}

    def _update_comments(self):
        comment_lines = []
        history_lines = []
        if self._current_issue:
            for comment in self._current_issue.get("comments", []):
                comment_lines.append("{0} · {1}\n{2}".format(
                    comment.get("date", ""), comment.get("user", ""), comment.get("text", "")
                ))
            for entry in reversed(self._current_issue.get("history", [])):
                history_lines.append("{0} · {1}\n{2}".format(
                    entry.get("date", ""), entry.get("user", ""), entry.get("action", "")
                ))
        self.comments_list.ItemsSource = comment_lines
        self.history_list.ItemsSource = history_lines

    def _show_image(self, path):
        self.preview_image.Source = None
        if self._current_issue and hasattr(self, "snapshot_count_text"):
            count = len(self._current_issue.get("preview", {}).get("images", []))
            if self._pending_snapshot_path:
                count += 1
            self.snapshot_count_text.Text = "{0} afbeelding(en)".format(count)
        if not path or not os.path.isfile(path):
            self.preview_empty_text.Visibility = Visibility.Visible
            return
        try:
            self.preview_image.Source = self._load_bitmap(path)
            self.preview_empty_text.Visibility = Visibility.Collapsed
        except Exception:
            self.preview_empty_text.Visibility = Visibility.Visible

    def _show_browser_image(self, path):
        self.browser_preview_image.Source = None
        if not path or not os.path.isfile(path):
            self.browser_preview_empty.Visibility = Visibility.Visible
            return
        try:
            self.browser_preview_image.Source = self._load_bitmap(path)
            self.browser_preview_empty.Visibility = Visibility.Collapsed
        except Exception:
            self.browser_preview_empty.Visibility = Visibility.Visible

    def _load_bitmap(self, path):
        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.UriSource = Uri(path, UriKind.Absolute)
        bitmap.EndInit()
        bitmap.Freeze()
        return bitmap

    def _new_temp_snapshot_path(self):
        return os.path.join(self._temp_dir, "snapshot_{0}.png".format(uuid.uuid4().hex))

    def _set_dirty(self, value):
        self._dirty = bool(value)
        if self._detail_window is not None:
            self.dirty_indicator.Visibility = Visibility.Visible if self._dirty else Visibility.Collapsed

    def _confirm_discard_changes(self):
        if not self._dirty:
            return True
        result = MessageBox.Show(
            "Er zijn niet-opgeslagen wijzigingen. Wijzigingen verwerpen?",
            PANEL_TITLE, MessageBoxButton.YesNo, MessageBoxImage.Warning
        )
        return result == MessageBoxResult.Yes

    def _validation_message(self, message):
        self._set_status(message.replace("\n", " "))
        MessageBox.Show(message, PANEL_TITLE, MessageBoxButton.OK, MessageBoxImage.Warning)

    def _set_status(self, message):
        self.status_text.Text = to_text(message)
        if self._detail_window is not None:
            self.detail_status_text.Text = to_text(message)

    def _set_marker_indicator(self, text, color, tooltip=""):
        try:
            self.marker_text.Text = to_text(text)
            self.marker_text.ToolTip = to_text(tooltip or text)
            self.marker_dot.Fill = BrushConverter().ConvertFromString(color)
            self.marker_dot.ToolTip = to_text(tooltip or text)
        except Exception:
            pass

    def _set_network(self, available, message):
        self._network_available = bool(available)
        self.network_text.Text = "Netwerk beschikbaar" if available else "Netwerk niet bereikbaar"
        color = "#16A34A" if available else "#DC2626"
        self.network_dot.Fill = BrushConverter().ConvertFromString(color)
        self.network_text.ToolTip = to_text(message)
        editable_controls = [self.new_issue_button]
        if self._detail_window is not None:
            editable_controls.extend((
                self.pick_location_button, self.capture_snapshot_button,
                self.choose_image_button, self.add_comment_button,
                self.save_button, self.assigned_combo, self.status_combo, self.priority_combo,
                self.type_combo, self.due_date_picker, self.new_comment_text
            ))
        for control in editable_controls:
            control.IsEnabled = bool(available)
        if self._detail_window is not None:
            self.title_text.IsReadOnly = not bool(available)
            self.description_text.IsReadOnly = not bool(available)
            self._sync_detail_network()

    def _sync_detail_network(self):
        if self._detail_window is None:
            return
        available = bool(self._network_available)
        color = "#16A34A" if available else "#DC2626"
        self.detail_network_dot.Fill = BrushConverter().ConvertFromString(color)
        self.detail_network_text.Text = "Netwerk beschikbaar" if available else "Netwerk niet bereikbaar"
        for control in (
            self.pick_location_button, self.capture_snapshot_button,
            self.choose_image_button, self.add_comment_button,
            self.save_button,
            self.assigned_combo, self.status_combo, self.priority_combo,
            self.type_combo, self.due_date_picker, self.new_comment_text
        ):
            control.IsEnabled = available
        self.title_text.IsReadOnly = not available
        self.description_text.IsReadOnly = not available
