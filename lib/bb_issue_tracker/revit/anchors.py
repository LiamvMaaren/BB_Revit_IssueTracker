# -*- coding: utf-8 -*-
"""Smart Revit location anchors with a permanent XYZ fallback.

A face or level can provide a live location while the stored XYZ remains the
last durable fallback.  Deleting or invalidating a host therefore never removes
an issue marker.
"""

import copy
import math

from pyrevit import DB

from bb_issue_tracker.locking import utc_now
from bb_issue_tracker.revit.context import xyz_list_to_object, xyz_to_list
from bb_issue_tracker.textutils import to_text


ANCHOR_FACE = "face"
ANCHOR_LEVEL = "level"
ANCHOR_XYZ = "xyz"


def _element_id_text(element_id):
    if element_id is None:
        return ""
    try:
        return to_text(element_id.Value)
    except Exception:
        try:
            return to_text(element_id.IntegerValue)
        except Exception:
            return ""


def _valid_uv(uv):
    if uv is None:
        return False
    try:
        return not (
            math.isnan(float(uv.U)) or math.isnan(float(uv.V)) or
            math.isinf(float(uv.U)) or math.isinf(float(uv.V))
        )
    except Exception:
        return False


def _uv_to_list(uv):
    if not _valid_uv(uv):
        return []
    return [float(uv.U), float(uv.V)]


def _uv_from_list(values):
    if not values or len(values) < 2:
        return None
    try:
        return DB.UV(float(values[0]), float(values[1]))
    except Exception:
        return None


def _element_from_anchor(document, anchor):
    unique_id = to_text(anchor.get("element_unique_id", "")).strip()
    if unique_id:
        try:
            element = document.GetElement(unique_id)
            if element is not None:
                return element
        except Exception:
            pass
    element_id = to_text(anchor.get("element_id", "")).strip()
    if element_id:
        try:
            return document.GetElement(DB.ElementId(int(element_id)))
        except Exception:
            pass
    return None


def _level_from_anchor(document, anchor):
    unique_id = to_text(anchor.get("level_unique_id", "")).strip()
    if unique_id:
        try:
            level = document.GetElement(unique_id)
            if isinstance(level, DB.Level):
                return level
        except Exception:
            pass
    level_id = to_text(anchor.get("level_id", "")).strip()
    if level_id:
        try:
            level = document.GetElement(DB.ElementId(int(level_id)))
            if isinstance(level, DB.Level):
                return level
        except Exception:
            pass
    return None


def make_xyz_anchor(point):
    return {
        "type": ANCHOR_XYZ,
        "host_status": "fallback",
        "created_point_xyz_internal": xyz_to_list(point)
    }


def make_face_anchor(document, reference, point=None):
    """Create serializable face-host metadata from a picked Reference."""
    if reference is None:
        raise RuntimeError("Revit gaf geen geldige vlakreferentie terug.")
    element = document.GetElement(reference.ElementId)
    if element is None:
        raise RuntimeError("Het geselecteerde hostelement is niet gevonden.")

    stable_reference = reference.ConvertToStableRepresentation(document)
    face = None
    projection = None
    uv = None
    try:
        try:
            uv = reference.UVPoint
        except Exception:
            uv = None
        if not _valid_uv(uv):
            try:
                face = element.GetGeometryObjectFromReference(reference)
            except Exception:
                face = None
            if face is not None and point is not None:
                try:
                    projection = face.Project(point)
                    if projection is not None:
                        uv = projection.UVPoint
                except Exception:
                    uv = None
    finally:
        if projection is not None:
            try:
                projection.Dispose()
            except Exception:
                pass
        if face is not None:
            try:
                face.Dispose()
            except Exception:
                pass

    if not _valid_uv(uv):
        raise RuntimeError("Revit kon geen stabiele UV-positie op het vlak bepalen.")

    category_name = ""
    try:
        if element.Category is not None:
            category_name = to_text(element.Category.Name)
    except Exception:
        pass
    return {
        "type": ANCHOR_FACE,
        "host_status": "valid",
        "element_id": _element_id_text(element.Id),
        "element_unique_id": to_text(element.UniqueId),
        "element_name": to_text(getattr(element, "Name", "")),
        "category_name": category_name,
        "stable_reference": to_text(stable_reference),
        "uv": _uv_to_list(uv)
    }


def detect_level(document, view, point):
    """Return the view level or otherwise the nearest model level."""
    try:
        level = getattr(view, "GenLevel", None)
        if isinstance(level, DB.Level):
            return level
    except Exception:
        pass

    best = None
    best_distance = None
    try:
        levels = DB.FilteredElementCollector(document).OfClass(DB.Level).ToElements()
        for level in levels:
            distance = abs(float(level.Elevation) - float(point.Z))
            if best is None or distance < best_distance:
                best = level
                best_distance = distance
    except Exception:
        return None
    return best


def make_level_anchor(document, view, point):
    level = detect_level(document, view, point)
    if level is None:
        return None
    return {
        "type": ANCHOR_LEVEL,
        "host_status": "valid",
        "level_id": _element_id_text(level.Id),
        "level_unique_id": to_text(level.UniqueId),
        "level_name": to_text(level.Name),
        "xy_internal": [float(point.X), float(point.Y)],
        "offset_internal": float(point.Z) - float(level.Elevation)
    }


def anchor_identity(anchor):
    anchor = anchor or {}
    anchor_type = to_text(anchor.get("type", ANCHOR_XYZ))
    if anchor_type == ANCHOR_FACE:
        return (
            ANCHOR_FACE,
            to_text(anchor.get("element_unique_id", "")),
            to_text(anchor.get("stable_reference", "")),
            tuple(anchor.get("uv") or [])
        )
    if anchor_type == ANCHOR_LEVEL:
        return (
            ANCHOR_LEVEL,
            to_text(anchor.get("level_unique_id", "")),
            tuple(anchor.get("xy_internal") or []),
            anchor.get("offset_internal")
        )
    return (ANCHOR_XYZ,)


def _fallback_point(location):
    return xyz_list_to_object(
        location.get("last_known_xyz_internal") or
        location.get("point_xyz_internal") or []
    )


def resolve_location_point(document, raw_location):
    """Resolve the live host point and return point plus updated location data.

    The updated location is only different when the host status or last-known
    point changed.  Callers may persist it, but can always draw immediately.
    """
    location = copy.deepcopy(raw_location or {})
    anchor = location.get("anchor") or {}
    anchor_type = to_text(anchor.get("type", ANCHOR_XYZ)) or ANCHOR_XYZ
    point = None
    warning = ""

    if anchor_type == ANCHOR_FACE:
        reference = None
        face = None
        try:
            stable = to_text(anchor.get("stable_reference", "")).strip()
            if not stable:
                raise RuntimeError("stabiele vlakreferentie ontbreekt")
            reference = DB.Reference.ParseFromStableRepresentation(document, stable)
            element = _element_from_anchor(document, anchor)
            if element is None:
                try:
                    element = document.GetElement(reference.ElementId)
                except Exception:
                    element = None
            if element is None:
                raise RuntimeError("hostelement ontbreekt")
            face = element.GetGeometryObjectFromReference(reference)
            if face is None or not isinstance(face, DB.Face):
                raise RuntimeError("hostvlak bestaat niet meer")
            uv = _uv_from_list(anchor.get("uv"))
            if uv is None:
                raise RuntimeError("UV-positie ontbreekt")
            try:
                if not face.IsInside(uv):
                    raise RuntimeError("opgeslagen punt ligt niet meer op het hostvlak")
            except AttributeError:
                pass
            point = face.Evaluate(uv)
            anchor["host_status"] = "valid"
        except Exception as error:
            point = None
            anchor["host_status"] = "missing"
            warning = "Face-host ontbreekt; vaste XYZ-locatie wordt gebruikt ({0}).".format(
                to_text(error)
            )
        finally:
            if face is not None:
                try:
                    face.Dispose()
                except Exception:
                    pass
            if reference is not None:
                try:
                    reference.Dispose()
                except Exception:
                    pass

    elif anchor_type == ANCHOR_LEVEL:
        level = _level_from_anchor(document, anchor)
        if level is not None:
            xy = anchor.get("xy_internal") or []
            if len(xy) >= 2:
                try:
                    point = DB.XYZ(
                        float(xy[0]),
                        float(xy[1]),
                        float(level.Elevation) + float(anchor.get("offset_internal", 0.0))
                    )
                    anchor["host_status"] = "valid"
                except Exception:
                    point = None
        if point is None:
            anchor["host_status"] = "missing"
            warning = "Level-host ontbreekt; vaste XYZ-locatie wordt gebruikt."

    if point is None:
        point = _fallback_point(location)
        if anchor_type == ANCHOR_XYZ:
            anchor["host_status"] = "fallback"

    location["anchor"] = anchor
    if point is not None:
        values = xyz_to_list(point)
        old_values = location.get("last_known_xyz_internal") or location.get("point_xyz_internal") or []
        status_changed = to_text((raw_location or {}).get("anchor", {}).get("host_status", "")) != to_text(anchor.get("host_status", ""))
        point_changed = points_differ(old_values, values)
        location["point_xyz_internal"] = values
        location["last_known_xyz_internal"] = values
        if status_changed or point_changed:
            anchor["last_resolved_at"] = utc_now()
    return point, location, warning


def points_differ(first, second, tolerance=0.0001):
    if not first or not second or len(first) < 3 or len(second) < 3:
        return True
    try:
        return any(abs(float(first[index]) - float(second[index])) > tolerance for index in range(3))
    except Exception:
        return True
