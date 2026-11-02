"""Checks staff permissions and records access attempts.

authorize() checks an action. change_permission() lets an authorized admin
grant or revoke a role's permission. Covers FR-AUT-01/02 and NFR-SEC/AUD-01.
"""

import math
import time


OPERATIONS = frozenset({
    "view_alarms", "acknowledge_alarm", "view_inventory", "view_history",
    "view_reports", "view_health", "manage_permissions", "send_information",
})


def _timestamp(now):
    value = time.time() if now is None else now
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Time must be finite nonnegative UTC seconds")
    if not math.isfinite(value) or value < 0:
        raise ValueError("Time must be finite nonnegative UTC seconds")
    return float(value)


class AccessControl:
    """Keeps role permissions and cabinet restrictions out of the other modules.

    actor is the user ID already checked by Flask login, not a role sent by a form.
    Every check saves an audit entry, including denied attempts. If that save
    fails, the error stops the operation. Permission changes take effect on the
    next check. A user limited to certain cabinets cannot do global operations.
    """

    def __init__(self, store, users: dict, role_permissions: dict, cabinet_ids):
        self._store = store
        self._cabinets = frozenset(cabinet_ids)
        self._roles = frozenset(role_permissions)
        self._users = {}
        for user_id, record in users.items():
            role = record["role"]
            scopes = record["cabinets"]
            if role not in self._roles:
                raise ValueError("User has an unknown role")
            if scopes is not None and not set(scopes) <= self._cabinets:
                raise ValueError("User scope contains an unknown cabinet")
            self._users[user_id] = {
                "role": role,
                "cabinets": None if scopes is None else frozenset(scopes),
            }
        # Seed a new database, but keep permission edits saved from earlier runs.
        for role, operations in role_permissions.items():
            operations = set(operations)
            if not operations <= OPERATIONS:
                raise ValueError("Role policy contains an unknown operation")
            if store.get("permissions", role) is None:
                store.save("permissions", role, {
                    "role": role, "operations": sorted(operations),
                })

    # Used by the other modules before protected actions
    def authorize(self, actor: str | None, operation: str, target: str | None = None,
                  now: float | None = None, *, audit_target: str | None = None) -> bool:
        """Return True if the user can do this action; otherwise return False.

        target is a cabinet ID, or None for a global action. Permission management
        is global. audit_target adds a specific alarm/role label to the log only;
        it never changes which cabinet the user can access. The audit entry records
        permission for the attempt, not whether the rest of the action succeeded.
        """
        at = _timestamp(now)
        user = self._users.get(actor) if isinstance(actor, str) else None
        allowed, reason = False, "Unknown or unauthenticated user"
        if user is not None:
            if not isinstance(operation, str) or operation not in OPERATIONS:
                reason = "Unknown operation"
            elif target is not None and (
                not isinstance(target, str) or target not in self._cabinets
            ):
                reason = "Unknown cabinet"
            elif operation == "manage_permissions" and target is not None:
                reason = "Permission management is a global operation"
            elif user["cabinets"] is not None and (
                target is None or target not in user["cabinets"]
            ):
                reason = "Cabinet scope does not permit this operation"
            else:
                policy = self._store.get("permissions", user["role"])
                allowed = policy is not None and operation in policy["operations"]
                reason = "" if allowed else "Role does not permit this operation"
        self._store.audit(actor, operation, target if audit_target is None else audit_target,
                          "Allowed" if allowed else "Denied", at, reason)
        return allowed

    # Used by the admin permission screen
    def change_permission(self, actor: str | None, role: str, operation: str,
                          granted: bool, now: float | None = None) -> bool:
        """Grant or revoke one role permission and save the change.

        Only an authorized admin can do this; otherwise raise PermissionError.
        Unknown roles/operations or a non-boolean granted value raise ValueError.
        This method does not create users or change their assigned cabinets.
        """
        at = _timestamp(now)
        detail = "role=" + str(role) + "; permission=" + str(operation) + "; grant=" + str(granted)
        if not self.authorize(actor, "manage_permissions", now=at, audit_target=detail):
            raise PermissionError("Permission management denied")
        if (not isinstance(role, str) or role not in self._roles or
                not isinstance(operation, str) or operation not in OPERATIONS):
            raise ValueError("Unknown role or operation")
        if not isinstance(granted, bool):
            raise ValueError("granted must be True or False")
        policy = self._store.get("permissions", role)
        operations = set(policy["operations"]) if policy is not None else set()
        if granted:
            operations.add(operation)
        else:
            operations.discard(operation)
        self._store.save("permissions", role, {
            "role": role, "operations": sorted(operations),
        })
        return True
