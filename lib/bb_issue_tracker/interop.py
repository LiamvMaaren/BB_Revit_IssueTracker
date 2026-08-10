# -*- coding: utf-8 -*-

import uuid

from bb_issue_tracker.constants import COMMAND_APPDOMAIN_KEY


def set_pending_command(command):
    from System import AppDomain
    value = "{0}|{1}".format(command, uuid.uuid4().hex)
    AppDomain.CurrentDomain.SetData(COMMAND_APPDOMAIN_KEY, value)


def pop_pending_command():
    from System import AppDomain
    value = AppDomain.CurrentDomain.GetData(COMMAND_APPDOMAIN_KEY)
    if value:
        AppDomain.CurrentDomain.SetData(COMMAND_APPDOMAIN_KEY, None)
        return str(value).split("|", 1)[0]
    return None
