"""Terminal version of the Hospital Monitoring System (Part B).

Run from the repo root with:  python3.12 main.py
Each menu option calls ONE module and prints the result, so you can see which module
is responsible for which behaviour.
"""

import time                                        # for the clock and time formatting

from monitoring import AccessControl, AlarmManager, HealthMonitor, InventoryMonitor
from monitoring.access import OPERATIONS           # the list of operations that can be granted
from monitoring.storage import SQLiteStore
import registry
from gateway import Gateway
from reports import ReportBuilder
from adc_simulator import Simulator

# ---------- demo decisions (agreed project values, NOT course-supplied) ----------
T_COM = 15                                         # seconds with no valid message = Communication Lost
THRESHOLDS = {"insulin": 5, "morphine": 3}         # refill thresholds: BELOW this = low stock
SEVERITIES = ("Critical", "Warning", "Info")       # severity shown as text (NFR-USE-01)

# Which operations each role may do. These are only saved the FIRST time the database
# is created. If you edit this, delete monitoring.db so the new policy is loaded.
ROLE_PERMISSIONS = {
    "nurse": ["view_alarms", "acknowledge_alarm", "view_inventory", "view_history", "view_health"],
    "manager": ["view_alarms", "view_inventory", "view_history", "view_reports",
                "view_health", "send_information"],
    "admin": ["view_alarms", "acknowledge_alarm", "view_inventory", "view_history",
              "view_reports", "view_health", "manage_permissions", "send_information"],
}

# Demo accounts. cabinets=None means all cabinets; a list limits the user to those cabinets.
USERS = {
    "nurse_a": {"role": "nurse", "cabinets": ["A"]},
    "nurse_b": {"role": "nurse", "cabinets": ["B"]},
    "manager": {"role": "manager", "cabinets": None},
    "admin": {"role": "admin", "cabinets": None},
}

# ---------- build the modules and connect them together ----------
cabinet_ids = list(registry.CABINETS)              # ["A", "B"]
store = SQLiteStore("monitoring.db")               # the database file (created in the repo root)
access = AccessControl(store, USERS, ROLE_PERMISSIONS, cabinet_ids)         # Priyal: permissions
alarms = AlarmManager(store, access, registry.CABINETS, SEVERITIES)         # Priyal: alarm rules
health = HealthMonitor(store, registry.CABINETS, T_COM)                     # Priyal: timeout/health
inventory = InventoryMonitor(store, access, registry.CABINETS, THRESHOLDS, health)  # Priyal: stock
gateway = Gateway(store, alarms, health, inventory, access, THRESHOLDS, SEVERITIES)  # Eliana: messages
reports = ReportBuilder(store, access, cabinet_ids)                         # Eliana: reports
sim = Simulator(gateway, cabinet_ids)              # Eliana: fake cabinets
gateway.set_transport(sim.deliver)                 # outbound requests go to the simulator

# The program's changing values live in one dictionary (so no "global" keyword is needed)
state = {"actor": None,                            # who is logged in (None = not logged in)
         "offset": 0}                              # seconds the clock has been fast-forwarded


# ---------- small helper functions ----------

def get_now():
    """Current demo time = real time + any fast-forward."""
    return time.time() + state["offset"]


def fmt(value):
    """Turn an epoch time into HH:MM:SS text, or 'never' if there is none."""
    if value is None:                              # no time recorded yet
        return "never"
    return time.strftime("%H:%M:%S", time.localtime(value))


def ask_cabinet(allow_blank=False):
    """Keep asking until the user types a valid cabinet ID. Blank is allowed if asked."""
    while True:                                    # repeat until we return something valid
        text = input("Cabinet " + str(cabinet_ids) + ": ").strip().upper()
        if text == "" and allow_blank:             # blank means "all cabinets"
            return None
        if registry.is_registered(text):           # a real cabinet
            return text
        print("  Not a registered cabinet, try again.")


def ask_minutes():
    """Ask how many minutes back to look. Blank or bad input means 60."""
    text = input("Minutes back (Enter = 60): ").strip()
    if text.isdigit():                             # only accept whole numbers
        return int(text)
    return 60


# ---------- menu actions (each one calls a module and prints the result) ----------

def switch_user():
    """Choose who is acting. This stands in for a login (no passwords in the demo)."""
    print("Users:", list(USERS), "  (Enter = not logged in)")
    name = input("Log in as: ").strip()
    if name == "":
        state["actor"] = None                      # unauthenticated
    elif name in USERS:
        state["actor"] = name
    else:
        print("  Unknown user, no change.")


def simulate_event():
    """Make a simulated cabinet send a message to the gateway (FR-COM-01)."""
    print("Events: 1 alarm  2 clear  3 low insulin (2)  4 insulin equal threshold (5)")
    print("        5 insulin ok (10)  6 healthy  7 issue  8 heartbeat")
    choice = input("Event: ").strip()
    cab = ask_cabinet()
    result = None                                  # what the gateway sends back
    if choice == "1":
        result = sim.alarm(cab)
    elif choice == "2":
        result = sim.clear(cab)
    elif choice == "3":
        result = sim.inventory(cab, 2, 10)         # 2 < 5, so LOW STOCK
    elif choice == "4":
        result = sim.inventory(cab, 5, 10)         # 5 == 5, so NOT low stock
    elif choice == "5":
        result = sim.inventory(cab, 10, 10)
    elif choice == "6":
        result = sim.health(cab, "healthy")
    elif choice == "7":
        result = sim.health(cab, "issue")
    elif choice == "8":
        result = sim.heartbeat(cab)
    else:
        print("  Not an option.")
        return
    print("  Gateway result:", result)


def bad_messages():
    """Show that invalid messages are rejected and do NOT refresh a cabinet's clock."""
    print("1 garbage message  2 unregistered cabinet 'Z'  3 bad severity")
    choice = input("Test: ").strip()
    if choice == "1":
        message = {"hello": "world"}               # missing every required field
    elif choice == "2":
        message = {"version": 1, "event_id": "x1", "cabinet_id": "Z", "type": "status",
                   "occurred_at": 1.0, "sequence": 1, "payload": {}}
    elif choice == "3":
        message = {"version": 1, "event_id": "x2", "cabinet_id": "A", "type": "alarm",
                   "occurred_at": 1.0, "sequence": 1,
                   "payload": {"alarm_id": "door", "severity": "Purple", "state": "raised"}}
    else:
        print("  Not an option.")
        return
    print("  Gateway result:", gateway.receive_message(message, get_now()))


def show_alarms():
    """List active alarms ward by ward (FR-ALM-01). Priyal's list_alarms works per ward."""
    for ward in registry.wards():                  # one ward at a time
        print("Ward:", ward)
        try:                                       # denied users raise PermissionError
            found = alarms.list_alarms(state["actor"], ward, get_now())
            if len(found) == 0:
                print("   No active alarms")
            for a in found:                        # print every alarm with its required fields
                line = ("   [" + a["severity"].upper() + "] cabinet " + a["cabinet_id"]
                        + " / " + a["alarm_id"] + " - " + a["state"]
                        + " - received " + fmt(a["received_at"]))
                if a["acknowledged_by"] is not None:
                    line = line + " - acked by " + a["acknowledged_by"] + " at " + fmt(a["acknowledged_at"])
                print(line)
        except PermissionError:
            print("   ACCESS DENIED (nothing disclosed)")


def acknowledge_alarm():
    """Acknowledge an alarm (FR-ALM-02). This does NOT clear it."""
    cab = ask_cabinet()
    alarm_id = input("Alarm ID (Enter = door_open): ").strip()
    if alarm_id == "":
        alarm_id = "door_open"                     # the simulator's alarm name
    try:
        done = alarms.acknowledge(state["actor"], cab, alarm_id, get_now())
        if done:
            print("  Acknowledged.")
        else:
            print("  Nothing to acknowledge (no active matching alarm).")
    except PermissionError:
        print("  ACKNOWLEDGEMENT DENIED.")


def show_status():
    """Show each cabinet's status (FR-COM-02, FR-HLT-01 to 03)."""
    now = get_now()
    for cab in cabinet_ids:
        if not access.authorize(state["actor"], "view_health", cab, now=now):
            print("Cabinet", cab + ": hidden (no permission)")
            continue                               # skip to the next cabinet
        s = health.get_cabinet_status(cab, now)
        print("Cabinet", cab + ":", s["status"], "| last valid update:", fmt(s["last_valid_update"]),
              "| reported health:", s["reported_health"])


def show_inventory():
    """Show stock levels (FR-INV-01/02). Checked one cabinet at a time."""
    for cab in cabinet_ids:
        try:
            view = inventory.get_inventory(state["actor"], [cab], get_now())[0]
            line = "Cabinet " + cab + ": "
            if view["stale"]:                      # cabinet not heard from recently
                line = line + "[STALE] "
            for item in view["items"]:
                if item["known"]:
                    line = line + item["medication_id"] + "=" + str(item["quantity"])
                else:
                    line = line + item["medication_id"] + "=Unknown"   # never received is not zero
                if item["low_stock"]:
                    line = line + " [LOW STOCK]"
                line = line + " | "
            print(line)
        except PermissionError:
            print("Cabinet", cab + ": ACCESS DENIED")


def pause_resume():
    """Simulate a cabinet losing or regaining its connection."""
    choice = input("1 pause  2 resume: ").strip()
    cab = ask_cabinet()
    if choice == "1":
        sim.pause(cab)
        print("  Cabinet", cab, "is now silent. Fast-forward past T_COM to see it go lost.")
    elif choice == "2":
        sim.resume(cab)
        print("  Cabinet", cab, "can send again. Send a heartbeat or health report.")


def send_info():
    """Send approved information to one cabinet (FR-COM-03/04)."""
    cab = ask_cabinet()
    mode = input("Simulated reply (confirm / reject / silent): ").strip().lower()
    if mode not in ["confirm", "reject", "silent"]:
        print("  Not a valid reply type.")
        return
    sim.modes[cab] = mode                          # tell the simulator how to answer.  
    try:
        print("  Outcome:", gateway.send_information(state["actor"], cab, "refill approved", get_now()))
    except Exception as error:                     # safety net so the demo never crashes
        print("  Send error:", error)


def adc_request():
    """Act as an ADC sending a request to Monitoring (FR-COM-05)."""
    print("1 supported request  2 unsupported type  3 unregistered cabinet  4 missing request_id")
    choice = input("Request: ").strip()
    if choice == "1":
        request = {"request_id": "r1", "type": "registration_check", "cabinet_id": "A"}
    elif choice == "2":
        request = {"request_id": "r2", "type": "open_door", "cabinet_id": "A"}
    elif choice == "3":
        request = {"request_id": "r3", "type": "registration_check", "cabinet_id": "Z"}
    elif choice == "4":
        request = {"type": "registration_check", "cabinet_id": "A"}
    else:
        print("  Not an option.")
        return
    print("  Reply:", gateway.handle_adc_request(request))


def show_history():
    """Search saved events (FR-HIS-01 to 03)."""
    print("Leave a filter blank to include everything.")
    cab = ask_cabinet(allow_blank=True)
    category = input("Category (status/inventory/alarm/maintenance/health/outbound): ").strip()
    if category == "":
        category = None
    now = get_now()
    result = gateway.get_history(state["actor"], cab, category, now - 60 * ask_minutes(), now + 1, now)
    print("  Status:", result["status"])
    if result["status"] == "empty":
        print("  No Records Found")
    if result["status"] == "failure":
        print("  Retrieval failure")
    if result["status"] == "denied":
        print("  Access denied")
    for row in result["rows"]:
        print("  ", row["cabinet_id"], "|", row["category"], "| occurred", fmt(row["occurred_at"]),
              "| received", fmt(row["received_at"]))


def show_report():
    """Alarm totals per cabinet (FR-RPT-01)."""
    now = get_now()
    result = reports.alarm_totals(state["actor"], now - 60 * ask_minutes(), now + 1, now)
    print("  Status:", result["status"], " Totals:", result["totals"])
    if result["status"] == "empty":
        print("  No Matching Alarm Records")
    if result["status"] == "denied":
        print("  Access denied")


def change_permission():
    """Grant or revoke a role's permission (FR-AUT-02). Admin only."""
    print("Roles:", list(ROLE_PERMISSIONS))
    role = input("Role: ").strip()
    print("Operations:", sorted(OPERATIONS))
    operation = input("Operation: ").strip()
    grant = input("1 grant  2 revoke: ").strip() == "1"   # True = grant, False = revoke
    try:
        access.change_permission(state["actor"], role, operation, grant, get_now())
        print("  Permission changed. It applies from the next check.")
    except PermissionError:
        print("  PERMISSION CHANGE DENIED (policy unchanged).")
    except ValueError:
        print("  Unknown role or operation.")


def show_audit():
    """Show the newest audit entries (NFR-AUD-01). Needs the admin permission."""
    if not access.authorize(state["actor"], "manage_permissions", now=get_now()):
        print("  ACCESS DENIED (the audit log is admin only).")
        return
    for e in store.recent_audit(15):
        print("  ", fmt(e["at"]), "|", e["actor"], "|", e["operation"], "|", e["target"],
              "|", e["decision"], "|", e["reason"])


# def fast_forward():
#     """Move the demo clock forward so timeouts can be shown without waiting."""
#     text = input("Seconds to fast-forward (T_COM is " + str(T_COM) + "): ").strip()
#     if text.isdigit():
#         state["offset"] = state["offset"] + int(text)
#         sim.offset = state["offset"]               # simulated cabinets use the same clock
#         print("  Clock moved forward", text, "seconds.")
#     else:
#         print("  Please type a whole number.")


# ---------- main loop ----------

MENU = """
================ Hospital Monitoring ================
 1 Switch user              8 Send info to cabinet
 2 Simulate cabinet event   9 ADC request to Monitoring
 3 Test invalid messages   10 History search
 4 Show alarms             11 Alarm report
 5 Acknowledge alarm       12 Change permission (admin)
 6 Cabinet status          13 Audit log (admin)
 7 Inventory               14 Pause / resume a cabinet
                           0 Quit
======================================================"""
# removed:      15 Fast-forward clock

# Connects each menu number to the function that does the work
ACTIONS = {"1": switch_user, "2": simulate_event, "3": bad_messages, "4": show_alarms,
           "5": acknowledge_alarm, "6": show_status, "7": show_inventory, "8": send_info,
           "9": adc_request, "10": show_history, "11": show_report, "12": change_permission,
           "13": show_audit, "14": pause_resume}

# removed:      , "15": fast_forward

running = True                                     # becomes False when the user quits
while running:                                     # repeat the menu until quit
    print(MENU)
    print("Logged in as:", state["actor"] or "(not logged in)", "| clock +" + str(state["offset"]) + "s")
    choice = input("Choose: ").strip()
    if choice == "0":
        running = False                            # leave the loop
    elif choice in ACTIONS:
        try:                                       # safety net: one bad input won't end the demo
            ACTIONS[choice]()                      # call the chosen function
        except Exception as error:
            print("  Unexpected error:", error)
    else:
        print("  Not a menu option.")
print("Goodbye.")