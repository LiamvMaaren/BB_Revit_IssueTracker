# -*- coding: utf-8 -*-
"""Exclusive lock files and atomic JSON writes suitable for SMB shares."""

import datetime
import io
import json
import os
import socket
import time
import uuid


class LockBusyError(Exception):
    def __init__(self, path, owner=None):
        self.path = path
        self.owner = owner or {}
        message = "Bestand is vergrendeld: {0}".format(path)
        Exception.__init__(self, message)


class RevisionConflictError(Exception):
    pass


def utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_json(path, default=None):
    try:
        with io.open(path, "r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except Exception:
        return default


class FileLock(object):
    def __init__(self, path, user_id="", timeout_seconds=30):
        self.path = path
        self.user_id = user_id
        self.timeout_seconds = max(0, int(timeout_seconds))
        self.token = str(uuid.uuid4())
        self.acquired = False

    def acquire(self):
        started = time.time()
        payload = {
            "token": self.token,
            "user_id": self.user_id,
            "computer": socket.gethostname(),
            "process_id": os.getpid(),
            "created_at": utc_now()
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        while True:
            try:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                descriptor = os.open(self.path, flags)
                try:
                    os.write(descriptor, encoded)
                finally:
                    os.close(descriptor)
                self.acquired = True
                return self
            except OSError:
                if time.time() - started >= self.timeout_seconds:
                    raise LockBusyError(self.path, _read_json(self.path, {}))
                time.sleep(0.15)

    def release(self):
        if not self.acquired:
            return
        owner = _read_json(self.path, {}) or {}
        if owner.get("token") == self.token:
            try:
                os.remove(self.path)
            except OSError:
                pass
        self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        return False


def ensure_directory(path):
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise


def atomic_write_json(path, data, backup_path=None):
    folder = os.path.dirname(path)
    ensure_directory(folder)
    temp_path = "{0}.{1}.tmp".format(path, uuid.uuid4().hex)
    try:
        with io.open(temp_path, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write(u"\n")
            stream.flush()
        _atomic_replace(temp_path, path, backup_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _atomic_replace(source, destination, backup_path=None):
    try:
        from System.IO import File
        if os.path.exists(destination):
            if backup_path:
                ensure_directory(os.path.dirname(backup_path))
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                File.Replace(source, destination, backup_path, True)
            else:
                File.Replace(source, destination, None, True)
        else:
            File.Move(source, destination)
        return
    except ImportError:
        pass

    if backup_path and os.path.exists(destination):
        ensure_directory(os.path.dirname(backup_path))
        with io.open(destination, "rb") as source_stream:
            with io.open(backup_path, "wb") as target_stream:
                target_stream.write(source_stream.read())
    if hasattr(os, "replace"):
        os.replace(source, destination)
    else:
        if os.path.exists(destination):
            os.remove(destination)
        os.rename(source, destination)
