"""Tracks cabinet alarms and staff acknowledgements.

Read apply_alarm() for ADC messages, acknowledge() for staff actions, and
_TRANSITIONS for the state diagram in code. Covers FR-ALM-01 to FR-ALM-04.
"""

from enum import Enum
import json
import math
import time


class _State(Enum):
    ACTIVE = "Active"
    ACKNOWLEDGED = "Acknowledged"
    CLEARED = "Cleared"


class _Event(Enum):
    RAISE = "adcAlarm"
    CLEAR = "adcClear"
    ACKNOWLEDGE = "acknowledge"


# Each row is a current state; each column is an incoming event.
_TRANSITIONS = {
    _State.ACTIVE: {_Event.RAISE: _State.ACTIVE, _Event.CLEAR: _State.CLEARED,
                    _Event.ACKNOWLEDGE: _State.ACKNOWLEDGED},
    _State.ACKNOWLEDGED: {_Event.RAISE: _State.ACKNOWLEDGED,
                          _Event.CLEAR: _State.CLEARED,
                          _Event.ACKNOWLEDGE: _State.ACKNOWLEDGED},
    _State.CLEARED: {_Event.RAISE: _State.ACTIVE, _Event.CLEAR: _State.CLEARED,
                     _Event.ACKNOWLEDGE: None},  # A cleared alarm cannot be acknowledged.
}


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Time must be finite nonnegative UTC seconds")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Time must be finite nonnegative UTC seconds")
    return float(value)


class AlarmManager:
    """Keeps alarm rules here so the dashboard does not change states directly.

    The same alarm name can exist on different cabinets, so both IDs identify it.
    The gateway checks messages first. Each alarm's sequence number must keep
    increasing, including after an ADC restart.
    """

    def __init__(self, store, access, cabinets: dict,
                 severities=("Critical", "Warning", "Info")):
        if not severities or any(not isinstance(x, str) or not x for x in severities):
            raise ValueError("Configure nonempty textual severity labels")
        self._store, self._access = store, access
        self._cabinets = {key: {"ward": value["ward"]}
                          for key, value in cabinets.items()}
        self._severities = frozenset(severities)

    def _key(self, cabinet_id, alarm_id):
        if cabinet_id not in self._cabinets:
            raise ValueError("Unknown cabinet")
        if not isinstance(alarm_id, str) or not alarm_id:
            raise ValueError("alarm_id must be a nonempty string")
        # Including the cabinet stops two cabinets sharing one alarm record.
        return json.dumps([cabinet_id, alarm_id])

    @staticmethod
    def _transition(record, event, actor=None, at=None):
        """Look up the next state and make any acknowledgement changes here."""
        old = _State(record["state"])
        new = _TRANSITIONS[old][event]
        if new is None:
            return False
        record["state"] = new.value
        # Only the first acknowledgement sets the user and time.
        if event == _Event.ACKNOWLEDGE and old == _State.ACTIVE:
            record["acknowledged_by"], record["acknowledged_at"] = actor, at
        # The condition has returned, so start a new acknowledgement cycle.
        if event == _Event.RAISE and old == _State.CLEARED:
            record["acknowledged_by"], record["acknowledged_at"] = None, None
        return True

    # ADC messages
    def apply_alarm(self, cabinet_id: str, alarm_id: str, severity: str,
                    occurred_at: float, received_at: float | None = None, *,
                    sequence: int, cleared: bool = False) -> bool:
        """Handle an ADC reporting or clearing an alarm.

        Return True after saving. Return False and log an old/duplicate message or a
        clear with no matching alarm. Invalid field values raise ValueError.
        A repeated report keeps the acknowledgement. A new occurrence after clearing
        starts without the old acknowledgement. The gateway saves the event history.
        """
        key = self._key(cabinet_id, alarm_id)
        if severity not in self._severities:
            raise ValueError("Unknown severity")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("sequence must be a nonnegative integer")
        if not isinstance(cleared, bool):
            raise ValueError("cleared must be True or False")
        occurred = _timestamp(occurred_at)
        at = _timestamp(time.time() if received_at is None else received_at)
        record = self._store.get("alarms", key)
        if record is None and cleared:
            reason = "No matching alarm to clear"
        elif record is not None and sequence <= record["sequence"]:
            reason = "Duplicate or out-of-order alarm event"
        else:
            reason = None
        if reason:
            self._store.audit("ADC:" + cabinet_id, "alarm_event", cabinet_id,
                              "Denied", at, reason)
            return False
        if record is None:
            record = {"cabinet_id": cabinet_id, "alarm_id": alarm_id,
                      "state": _State.ACTIVE.value,
                      "acknowledged_by": None, "acknowledged_at": None}
        self._transition(record, _Event.CLEAR if cleared else _Event.RAISE)
        record.update(severity=severity, occurred_at=occurred, received_at=at,
                      sequence=sequence)
        self._store.save("alarms", key, record)
        return True

    # Staff actions and dashboard views
    def acknowledge(self, actor: str | None, cabinet_id: str, alarm_id: str,
                    now: float | None = None) -> bool:
        """Record who noticed an alarm and when. This does not clear it.

        Check permission before looking up the alarm. Repeated acknowledgements keep
        the first user/time. Return False for a missing or cleared alarm. A denied
        user gets PermissionError. A failed database write is passed to the caller.
        """
        at = _timestamp(time.time() if now is None else now)
        target = "cabinet=" + str(cabinet_id) + "; alarm=" + str(alarm_id)
        if not self._access.authorize(actor, "acknowledge_alarm", cabinet_id,
                                      now=at, audit_target=target):
            raise PermissionError("Alarm acknowledgement denied")
        key = self._key(cabinet_id, alarm_id)
        record = self._store.get("alarms", key)
        if record is None or not self._transition(record, _Event.ACKNOWLEDGE, actor, at):
            self._store.audit(actor, "acknowledge_alarm", target, "Rejected", at,
                              "No active matching alarm")
            return False
        self._store.save("alarms", key, record)
        return True

    def list_alarms(self, actor: str | None, ward: str,
                    now: float | None = None) -> list[dict]:
        """Get a ward's active and acknowledged alarms that this user can view.

        Leave out cabinets the user cannot access. If none are allowed, raise
        PermissionError. Return new dictionaries so the caller cannot edit saved state.
        """
        at = _timestamp(time.time() if now is None else now)
        if not isinstance(ward, str) or not ward:
            raise ValueError("ward must be a nonempty string")
        cabinets = [key for key, value in self._cabinets.items() if value["ward"] == ward]
        allowed = {key for key in cabinets
                   if self._access.authorize(actor, "view_alarms", key, now=at)}
        if not allowed:
            if not cabinets:
                self._access.authorize(actor, "view_alarms", now=at)
            raise PermissionError("Alarm view denied")
        return [dict(record, ward=ward) for record in self._store.list("alarms")
                if record["cabinet_id"] in allowed and record["state"] != _State.CLEARED.value]
