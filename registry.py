"""Registered cabinets. Supports FR-COM-01 and FR-COM-03.

Adding a third cabinet only means adding one line to CABINETS.
"""

# Each cabinet has a ward and a communication destination (simulated here)
CABINETS = {
    "A": {"ward": "Ward 1", "destination": "sim://A"},
    "B": {"ward": "Ward 2", "destination": "sim://B"},
}


def is_registered(cabinet_id):
    """Return True only if cabinet_id is a known cabinet."""
    if not isinstance(cabinet_id, str):    # reject numbers, lists, None, etc.
        return False
    return cabinet_id in CABINETS          # True if the ID is one of our keys


def ward_of(cabinet_id):
    """Return the ward name for a registered cabinet."""
    return CABINETS[cabinet_id]["ward"]


def wards():
    """Return a list of the distinct ward names."""
    result = []                            # start with an empty list
    for cabinet_id in CABINETS:            # look at every cabinet
        ward = CABINETS[cabinet_id]["ward"]
        if ward not in result:             # only add each ward once
            result.append(ward)
    return result