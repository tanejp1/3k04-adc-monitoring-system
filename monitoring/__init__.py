"""Hospital monitoring domain modules assigned to Priyal."""

from .access import AccessControl
from .alarms import AlarmManager
from .health import HealthMonitor
from .inventory import InventoryMonitor

__all__ = ["AccessControl", "AlarmManager", "HealthMonitor", "InventoryMonitor"]
