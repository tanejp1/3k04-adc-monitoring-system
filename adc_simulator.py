"""Simulated ADC cabinets. They send messages through the gateway's receive_message,
the same entry point Part A would use."""

import time
import uuid


class Simulator:
    def __init__(self, gateway, cabinet_ids):
        self._gateway = gateway          # where messages are sent
        self.paused = []                 # cabinets currently "offline"
        self.modes = {}                  # outbound behaviour per cabinet
        for cabinet in cabinet_ids:
            self.modes[cabinet] = "confirm"   # confirm, reject, or silent

    def _send(self, cabinet, msg_type, payload):
        """Build a version 1 message and send it to the gateway."""
        if cabinet in self.paused:       # a paused cabinet sends nothing
            return {"accepted": False, "error": "Cabinet is paused (simulated outage)"}
        now = time.time()
        message = {
            "version": 1,
            "event_id": str(uuid.uuid4()),        # unique ID for this event
            "cabinet_id": cabinet,
            "type": msg_type,
            "occurred_at": now,
            "sequence": int(now * 1000),          # milliseconds: always increases, even after a restart
            "payload": payload,
        }
        return self._gateway.receive_message(message, now)

    def alarm(self, cabinet):
        return self._send(cabinet, "alarm", {"alarm_id": "door_open",
                                             "severity": "Critical", "state": "raised"})

    def clear(self, cabinet):
        return self._send(cabinet, "alarm", {"alarm_id": "door_open",
                                             "severity": "Critical", "state": "cleared"})

    def inventory(self, cabinet, insulin, morphine):
        return self._send(cabinet, "inventory",
                          {"quantities": {"insulin": insulin, "morphine": morphine}})

    def health(self, cabinet, value):
        return self._send(cabinet, "health", {"health": value})

    def heartbeat(self, cabinet):
        return self._send(cabinet, "status", {})

    def pause(self, cabinet):
        if cabinet not in self.paused:
            self.paused.append(cabinet)

    def resume(self, cabinet):
        if cabinet in self.paused:
            self.paused.remove(cabinet)

    def deliver(self, cabinet, request):
        """Handle an outbound request from Monitoring. Returns None for 'no reply'."""
        if cabinet in self.paused or self.modes[cabinet] == "silent":
            return None                           # no response at all
        if self.modes[cabinet] == "reject":
            return {"request_id": request["request_id"], "result": "rejected"}
        return {"request_id": request["request_id"], "result": "confirmed"}