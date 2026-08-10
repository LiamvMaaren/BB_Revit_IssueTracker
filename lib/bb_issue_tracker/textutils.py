# -*- coding: utf-8 -*-

import hashlib
import re

try:
    text_type = unicode
except NameError:
    text_type = str


def to_text(value):
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return text_type(value)
    except Exception:
        return u""


def slugify(value, fallback="item"):
    value = to_text(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or fallback


def safe_folder_name(value, fallback="project"):
    value = to_text(value).strip()
    value = re.sub(r"[<>:\"/\\|?*]+", "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip(" ._")
    return value or fallback


def stable_hash(value, length=12):
    raw = to_text(value).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:length]
