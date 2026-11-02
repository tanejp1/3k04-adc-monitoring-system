"""Gateway: receives, validates, saves, and routes messages. Covers FR-COM-01, 03, 04, 05.

Message format (version 1), a dictionary with these fields:
  version, event_id, cabinet_id, type, occurred_at, sequence, payload
Payload by type:
  alarm       {"alarm_id": str, "severity": str, "state": "raised" or "cleared"}
  inventory   {"quantities": {medication: whole number}}
  health      {"health": "healthy" or "issue"}
  maintenance {"note": str}
  status      {}
"""

import math
import time
import uuid

import registry

# Demo decision (not course-supplied): how long we wait for a cabinet's reply.
# A missing or inconclusive reply becomes Uncertain when this deadline passes.
T_RESP = 3

MESSAGE_TYPES = ["status", "inventory", "alarm", "maintenance", "health"]
REQUIRED_FIELDS = ["version", "event_id", "cabinet_id", "type",
                   "occurred_at", "sequence", "payload"]


def _is_time(value):
    """True if value is a finite, non-negative number (not True/False)."""
    if isinstance(value, bool):                      # True/False count as ints in Python, so exclude them
        return False
    if not isinstance(value, (int, float)):          # must be a number
        return False
    return math.isfinite(value) and value >= 0       # no NaN, infinity, or negatives


def _is_count(value):
    """True if value is a whole number that is 0 or more."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) and value >= 0


class Gateway:
    """Single entry point for incoming messages. This is the ONLY place accepted
    events are saved to history, so nothing is ever saved twice."""

    def __init__(self, store, alarm_manager, health_monitor, inventory_monitor,
                 access, thresholds, severities):
        self._store = store                          # Priyal's SQLiteStore (with my extra methods)
        self._alarms = alarm_manager                 # Priyal's alarm rules
        self._health = health_monitor                # Priyal's timeout/health rules
        self._inventory = inventory_monitor          # Priyal's stock rules
        self._access = access                        # Priyal's permission checks
        self._thresholds = thresholds                # approved medications (used in validation)
        self._severities = severities                # allowed severity labels
        self._transport = None                       # function used to send to cabinets

    def set_transport(self, function):
        """Set a delivery function taking (cabinet_id, request, timeout=seconds).

        The transport must return or raise TimeoutError within the given timeout.
        A future network transport must apply that timeout to its I/O operations.
        """
        self._transport = function

    # ---------------- validation ----------------

    def _validate(self, msg):
        """Return "" if the message is valid, otherwise a text reason it is not."""
        if not isinstance(msg, dict):                # must be a dictionary
            return "Message is not a dictionary"
        for field in REQUIRED_FIELDS:                # every required field must exist
            if field not in msg:
                return "Missing field: " + field
        if msg["version"] != 1:                      # only version 1 is supported
            return "Unsupported version"
        if not isinstance(msg["event_id"], str) or msg["event_id"] == "":
            return "event_id must be a nonempty string"
        if not registry.is_registered(msg["cabinet_id"]):
            return "Unregistered source"             # FR-COM-01: reject unknown cabinets
        if msg["type"] not in MESSAGE_TYPES:
            return "Unknown message type"
        if not _is_time(msg["occurred_at"]):
            return "Bad occurred_at time"
        if not _is_count(msg["sequence"]):
            return "Bad sequence number"
        payload = msg["payload"]
        if not isinstance(payload, dict):
            return "Payload must be a dictionary"

        # Check the payload for each message type
        if msg["type"] == "alarm":
            if not isinstance(payload.get("alarm_id"), str) or payload.get("alarm_id") == "":
                return "Bad alarm_id"
            if payload.get("severity") not in self._severities:
                return "Bad severity"
            if payload.get("state") not in ["raised", "cleared"]:
                return "Bad alarm state"
        if msg["type"] == "inventory":
            quantities = payload.get("quantities")
            if not isinstance(quantities, dict):
                return "quantities must be a dictionary"
            for medication in quantities:            # check every medication in the snapshot
                if medication not in self._thresholds:
                    return "Unknown medication"
                if not _is_count(quantities[medication]):
                    return "Quantity must be a whole number, 0 or more"
        if msg["type"] == "health":
            if payload.get("health") not in ["healthy", "issue"]:
                return "Bad health value"
        if msg["type"] == "maintenance":
            if not isinstance(payload.get("note"), str):
                return "Maintenance note must be text"
        return ""                                    # empty text = valid

    # ---------------- inbound messages (FR-COM-01) ----------------

    def receive_message(self, msg, now=None):
        """Validate, save once, and route a message. Returns a result dictionary."""
        if now is None:                              # receipt time is added here by Monitoring
            now = time.time()
        problem = self._validate(msg)
        if problem != "":                            # invalid: not saved, does NOT refresh freshness
            return {"accepted": False, "error": problem}

        # Save the event. save_event returns False if this event_id was already stored.
        is_new = self._store.save_event(msg["event_id"], msg["cabinet_id"], msg["type"],
                                        msg["occurred_at"], now, msg["payload"])
        if not is_new:                               # a replayed message is not a new update
            return {"accepted": True, "duplicate": True}

        cabinet = msg["cabinet_id"]
        payload = msg["payload"]

        # Every valid message refreshes the cabinet's last-valid-update time (FR-COM-02)
        health_value = None
        if msg["type"] == "health":
            health_value = payload["health"]         # "healthy" or "issue"
        self._health.record_valid_update(cabinet, now, health_value)

        # Route to the right rule module
        domain_result = True
        if msg["type"] == "alarm":
            cleared = (payload["state"] == "cleared")
            domain_result = self._alarms.apply_alarm(
                cabinet, payload["alarm_id"], payload["severity"],
                msg["occurred_at"], now, sequence=msg["sequence"], cleared=cleared)
        if msg["type"] == "inventory":
            domain_result = self._inventory.update_inventory(
                cabinet, payload["quantities"], msg["occurred_at"], now,
                sequence=msg["sequence"])
        # domain_result False means Priyal's rules ignored it (old duplicate, or clear with no alarm)
        return {"accepted": True, "duplicate": False, "applied": domain_result}

    # ---------------- history (FR-HIS-01 to 03) ----------------

    def get_history(self, actor, cabinet_id=None, category=None, start=None, end=None, now=None):
        """Return {"status": ..., "rows": [...]}.

        status is "ok", "empty" (No Records Found), "denied", "invalid", or "failure".
        Permission is checked BEFORE any records are read.
        """
        if cabinet_id is None:                       # no cabinet chosen: try them all
            targets = list(registry.CABINETS)
        elif registry.is_registered(cabinet_id):     # one valid cabinet chosen
            targets = [cabinet_id]
        else:
            return {"status": "invalid", "rows": []}
        allowed = []                                 # cabinets this user may see
        for cabinet in targets:
            if self._access.authorize(actor, "view_history", cabinet, now=now):
                allowed.append(cabinet)
        if len(allowed) == 0:                        # nothing permitted: disclose nothing
            return {"status": "denied", "rows": []}
        rows = []
        try:                                         # allowed try/except: separates failure from empty
            for cabinet in allowed:
                found = self._store.query_events(cabinet, category, start, end)
                for row in found:
                    rows.append(row)
        except Exception:
            return {"status": "failure", "rows": []} # retrieval failure, NOT "no records"
        if len(rows) == 0:
            return {"status": "empty", "rows": []}
        return {"status": "ok", "rows": rows}

    # ---------------- outbound (FR-COM-03 and FR-COM-04) ----------------

    def send_information(self, actor, cabinet_id, info, now=None):
        """Send approved info to ONE registered cabinet. Returns Confirmed, Rejected,
        Uncertain, Denied, or a Rejected message for unregistered targets."""
        if now is None:
            now = time.time()
        if not registry.is_registered(cabinet_id):   # never send to unknown targets
            return "Rejected: unregistered cabinet"
        if not self._access.authorize(actor, "send_information", cabinet_id, now=now):
            return "Denied"                          # authorization BEFORE sending
        request_id = str(uuid.uuid4())               # unique ID to match the reply
        request = {"request_id": request_id, "info": info}
        started = time.monotonic()
        deadline = started + T_RESP
        reply = None
        if self._transport is not None:
            try:
                reply = self._transport(cabinet_id, request, timeout=T_RESP)
            except (TimeoutError, OSError):
                reply = None                       # delivery may have happened; success is unknown
        outcome = "Uncertain"                        # default: a timeout is NEVER shown as success
        if (time.monotonic() < deadline and isinstance(reply, dict)
                and reply.get("request_id") == request_id):
            if reply.get("result") == "confirmed":
                outcome = "Confirmed"
            elif reply.get("result") == "rejected":
                outcome = "Rejected"
        if outcome == "Uncertain":
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)              # do not announce a timeout before its deadline
        completed_at = now + (time.monotonic() - started)
        # Keep a record of the outcome (request_id is unique, so this saves once)
        self._store.save_event(request_id, cabinet_id, "outbound", now, completed_at,
                               {"outcome": outcome, "info": info})
        return outcome

    # ---------------- requests FROM an ADC (FR-COM-05) ----------------

    def handle_adc_request(self, request):
        """Supported request type: "registration_check". Others are rejected."""
        if not isinstance(request, dict):
            return {"request_id": None, "result": "rejected", "reason": "Invalid request"}
        request_id = request.get("request_id")       # preserved in every reply
        if not isinstance(request_id, str) or request_id == "":
            return {"request_id": None, "result": "rejected", "reason": "Missing request_id"}
        if request.get("type") != "registration_check":
            return {"request_id": request_id, "result": "rejected", "reason": "Unsupported request type"}
        if not registry.is_registered(request.get("cabinet_id")):
            return {"request_id": request_id, "result": "rejected", "reason": "Unregistered cabinet"}
        return {"request_id": request_id, "result": "ok",
                "ward": registry.ward_of(request["cabinet_id"])}
