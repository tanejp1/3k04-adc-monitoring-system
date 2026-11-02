"""Tracks the last valid message and reported health for each cabinet.

record_valid_update() handles messages. get_cabinet_status() checks whether a
cabinet has timed out. Covers FR-COM-02 and FR-HLT-01 to FR-HLT-03.
"""

from enum import Enum
import math
import time


class _State(Enum):
    UNKNOWN = "Unknown"
    AVAILABLE = "Available"
    ATTENTION = "Needs Attention"
    LOST = "Communication Lost"


class _Event(Enum):
    HEALTHY = "healthy"
    ISSUE = "issue"
    UPDATE = "valid non-health update"
    TIMEOUT = "timeout"


# A new message after a loss proves the connection is back.
# We still need a health report before calling the cabinet healthy.
_TRANSITIONS = {
    _State.UNKNOWN: {_Event.HEALTHY: _State.AVAILABLE, _Event.ISSUE: _State.ATTENTION,
                     _Event.UPDATE: _State.UNKNOWN, _Event.TIMEOUT: _State.LOST},
    _State.AVAILABLE: {_Event.HEALTHY: _State.AVAILABLE, _Event.ISSUE: _State.ATTENTION,
                       _Event.UPDATE: _State.AVAILABLE, _Event.TIMEOUT: _State.LOST},
    _State.ATTENTION: {_Event.HEALTHY: _State.AVAILABLE, _Event.ISSUE: _State.ATTENTION,
                       _Event.UPDATE: _State.ATTENTION, _Event.TIMEOUT: _State.LOST},
    _State.LOST: {_Event.HEALTHY: _State.AVAILABLE, _Event.ISSUE: _State.ATTENTION,
                 _Event.UPDATE: _State.UNKNOWN, _Event.TIMEOUT: _State.LOST},
}


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Time must be finite nonnegative UTC seconds")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Time must be finite nonnegative UTC seconds")
    return float(value)


class HealthMonitor:
    """Keeps timeout and health rules in one place.

    The gateway must validate a message before calling record_valid_update().
    Use UTC seconds for receipt times and the current time. Supply the agreed
    timeout as t_com; there is no course-provided default.
    """

    def __init__(self, store, cabinets: dict, t_com: float):
        self._t_com = _timestamp(t_com)
        if self._t_com == 0:
            raise ValueError("t_com must be positive")
        self._store = store
        self._cabinets = {key: {"ward": value["ward"]}
                          for key, value in cabinets.items()}

    def _record(self, cabinet_id):
        if cabinet_id not in self._cabinets:
            raise ValueError("Unknown cabinet")
        return self._store.get("health", cabinet_id) or {
            "cabinet_id": cabinet_id, "state": _State.UNKNOWN.value,
            "reported_health": "Unknown", "last_valid_update": None,
            "last_health_update": None,
        }

    @staticmethod
    def _transition(record, event):
        record["state"] = _TRANSITIONS[_State(record["state"])][event].value

    def _expire(self, record, now):
        # No first message means Unknown, rather than a made-up last-update time.
        seen = record["last_valid_update"] is not None
        fresh = seen and now - record["last_valid_update"] < self._t_com
        if seen and not fresh:
            self._transition(record, _Event.TIMEOUT)
        return seen, fresh

    # Called by the gateway after it checks a message
    def record_valid_update(self, cabinet_id: str, received_at: float | None = None,
                            health: str | None = None) -> bool:
        """Save the receipt of a valid message from a cabinet.

        health is "healthy", "issue", or None for a message without health data.
        Return False and log an older receipt. Invalid values raise ValueError.
        An ordinary inventory/status message cannot prove the cabinet is healthy.
        The gateway handles event history; database errors go back to the caller.
        """
        at = _timestamp(time.time() if received_at is None else received_at)
        if health not in (None, "healthy", "issue"):
            raise ValueError("health must be healthy, issue, or None")
        record = self._record(cabinet_id)
        last = record["last_valid_update"]
        if last is not None and at < last:
            self._store.audit("ADC:" + cabinet_id, "health_update", cabinet_id,
                              "Denied", at, "Older receipt time")
            return False
        self._expire(record, at)
        event = _Event.UPDATE if health is None else _Event(health)
        self._transition(record, event)
        record["last_valid_update"] = at
        if health is not None:
            record["reported_health"] = health
            record["last_health_update"] = at
        self._store.save("health", cabinet_id, record)
        return True

    # Called when the dashboard refreshes
    def get_cabinet_status(self, cabinet_id: str, now: float | None = None) -> dict:
        """Work out what the dashboard should show for this cabinet.

        At age >= t_com, show Communication Lost. A never-seen cabinet is Unknown.
        The dashboard calls this regularly, so no background timer is needed.
        Saved receipt times still work after restart. If the clock moves behind the
        last receipt, raise ValueError instead of showing a misleading fresh status.
        """
        at = _timestamp(time.time() if now is None else now)
        record = self._record(cabinet_id)
        last = record["last_valid_update"]
        if last is not None and at < last:
            raise ValueError("Current time precedes the last valid receipt")
        seen, fresh = self._expire(record, at)
        return {
            "cabinet_id": cabinet_id, "ward": self._cabinets[cabinet_id]["ward"],
            "status": record["state"], "reported_health": record["reported_health"],
            "communication_status": "Unknown" if not seen else
                                    ("Connected" if fresh else "Communication Lost"),
            "last_valid_update": last,
            "last_health_update": record["last_health_update"],
            "age": None if last is None else at - last, "stale": not fresh,
        }
