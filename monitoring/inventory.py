"""Stores the latest reported stock and checks which items need refilling.

update_inventory() handles an ADC snapshot. get_inventory() builds the view
for an authorized user. Covers FR-INV-01 and FR-INV-02.
"""

import math
import time


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Time must be finite nonnegative UTC seconds")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Time must be finite nonnegative UTC seconds")
    return float(value)


def _nonnegative_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class InventoryMonitor:
    """Keeps quantity and refill-threshold rules out of the dashboard.

    Each update replaces that cabinet's whole inventory snapshot. A missing item
    is unknown, not zero. Sequence numbers must keep increasing across ADC
    restarts. Returned views are copies of last-reported stock, not live readings.
    """

    def __init__(self, store, access, cabinets: dict, thresholds: dict, health=None):
        if any(not _nonnegative_integer(value) for value in thresholds.values()):
            raise ValueError("Thresholds must be nonnegative integers")
        self._store = store
        self._access = access
        self._cabinets = {key: {"ward": value["ward"]}
                          for key, value in cabinets.items()}
        self._thresholds = dict(thresholds)
        self._health = health

    # ADC messages
    def update_inventory(self, cabinet_id: str, quantities: dict, occurred_at: float,
                         received_at: float | None = None, *, sequence: int) -> bool:
        """Save a complete inventory snapshot from one cabinet.

        Reject invalid values with ValueError before saving anything. Return False
        and log an old/duplicate sequence; return True after a newer snapshot saves.
        The gateway logs accepted events and refreshes communication separately.
        """
        if cabinet_id not in self._cabinets:
            raise ValueError("Unknown cabinet")
        if not isinstance(quantities, dict):
            raise ValueError("quantities must be a medication-to-quantity dictionary")
        if not quantities.keys() <= self._thresholds.keys():
            raise ValueError("Medication has no approved threshold")
        if any(not _nonnegative_integer(value) for value in quantities.values()):
            raise ValueError("Quantities must be nonnegative integers")
        if not _nonnegative_integer(sequence):
            raise ValueError("sequence must be a nonnegative integer")
        occurred_at = _timestamp(occurred_at)
        received_at = _timestamp(time.time() if received_at is None else received_at)
        previous = self._store.get("inventory", cabinet_id)
        if previous is not None and sequence <= previous["sequence"]:
            self._store.audit("ADC:" + cabinet_id, "inventory_update", cabinet_id,
                              "Denied", received_at, "Duplicate or out-of-order")
            return False
        self._store.save("inventory", cabinet_id, {
            "cabinet_id": cabinet_id,
            "quantities": dict(quantities),
            "occurred_at": occurred_at,
            "received_at": received_at,
            "sequence": sequence,
        })
        return True

    # Staff inventory view
    def get_inventory(self, actor: str | None, cabinet_ids: list[str],
                      now: float | None = None) -> list[dict]:
        """Build inventory views after checking access to every requested cabinet.

        Reject the whole request if any target is unknown or unauthorized.
        Unknown quantities and low-stock results are None. Without a health module,
        stale is None because this module cannot tell whether communication is fresh.
        """
        at = _timestamp(time.time() if now is None else now)
        if isinstance(cabinet_ids, str):
            raise ValueError("cabinet_ids must be a collection of cabinet IDs")
        requested = list(dict.fromkeys(cabinet_ids))
        allowed = [self._access.authorize(actor, "view_inventory", cabinet, now=at)
                   for cabinet in requested]
        # Even an empty selection needs an access check and audit entry.
        if not requested:
            allowed = [self._access.authorize(actor, "view_inventory", now=at)]
        if not all(allowed):
            raise PermissionError("Inventory access denied")
        if any(cabinet not in self._cabinets for cabinet in requested):
            raise ValueError("Unknown cabinet")
        result = []
        for cabinet in requested:
            snapshot = self._store.get("inventory", cabinet)
            quantities = {} if snapshot is None else snapshot["quantities"]
            stale = None
            if self._health is not None:
                health = self._health.get_cabinet_status(cabinet, now=at)
                stale = health["stale"]
            items = []
            for medication, threshold in sorted(self._thresholds.items()):
                quantity = quantities.get(medication)
                items.append({
                    "medication_id": medication,
                    "known": quantity is not None,
                    "quantity": quantity,
                    "threshold": threshold,
                    # Equal to the threshold does not count as low stock.
                    "low_stock": None if quantity is None else quantity < threshold,
                })
            result.append({
                "cabinet_id": cabinet,
                "ward": self._cabinets[cabinet]["ward"],
                "known": snapshot is not None,
                "occurred_at": None if snapshot is None else snapshot["occurred_at"],
                "received_at": None if snapshot is None else snapshot["received_at"],
                "stale": stale,
                "items": items,
            })
        return result
