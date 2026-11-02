"""Alarm totals by cabinet for a time interval. Covers FR-RPT-01."""


class ReportBuilder:
    """Builds reports. Only 'raised' alarm events count as alarms, so clear events
    and acknowledgements do not inflate the totals."""

    def __init__(self, store, access, cabinet_ids):
        self._store = store                           # for reading saved events
        self._access = access                         # for permission checks
        self._cabinet_ids = list(cabinet_ids)         # all registered cabinets

    def alarm_totals(self, actor, start, end, now=None):
        """Return {"status": ..., "totals": {cabinet: count}}.

        status is "ok", "empty" (No Matching Alarm Records), "denied", or "failure".
        """
        allowed = []                                  # cabinets this user may report on
        for cabinet in self._cabinet_ids:
            if self._access.authorize(actor, "view_reports", cabinet, now=now):
                allowed.append(cabinet)
        if len(allowed) == 0:
            return {"status": "denied", "totals": {}}
        totals = {}
        try:                                          # allowed try/except for report failures
            for cabinet in allowed:
                events = self._store.query_events(cabinet, "alarm", start, end)
                count = 0
                for event in events:
                    if event["payload"].get("state") == "raised":   # only raised alarms count
                        count = count + 1
                totals[cabinet] = count
        except Exception:
            return {"status": "failure", "totals": {}}
        grand_total = 0                               # add up every cabinet
        for cabinet in totals:
            grand_total = grand_total + totals[cabinet]
        if grand_total == 0:
            return {"status": "empty", "totals": totals}
        return {"status": "ok", "totals": totals}