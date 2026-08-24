"""NIGP commodity classes used to carve an "IT" slice out of DC contracting.

DC tags contracts and purchase orders with NIGP codes. Search results carry the
full seven-digit code; the first three digits are the *class*, which is the
level the portal lets you filter on and the level we group at here.

The groupings below are a judgment call, so they are spelled out rather than
buried in a query. The distinction that matters for most questions is:

* :data:`SOFTWARE_SERVICES` -- class 920 is the one class that actually means
  "someone was paid to build, integrate, or run software." Custom software
  buildouts live here. If you only look at one class, look at this one.
* :data:`IT_GOODS` -- hardware, shrink-wrapped software licenses, leases.
  Buying Microsoft licenses is not the same activity as building a system.
* :data:`IT_ADJACENT` -- classes that contain a lot of IT work but are not
  IT-specific. Anything counted here needs a second look at the contract title
  before it is called technology spend.
"""

from __future__ import annotations

# Class 920: DATA PROCESSING, COMPUTER, PROGRAMMING, AND SOFTWARE SERVICES.
SOFTWARE_SERVICES: dict[str, str] = {
    "920": "Data processing, computer, programming, and software services",
}

IT_GOODS: dict[str, str] = {
    "204": "Computer hardware and peripherals for microcomputers",
    "206": "Computer hardware and peripherals for mini and mainframe computers",
    "207": "Computer accessories and supplies",
    "208": "Computer software for microcomputers (preprogrammed)",
    "209": "Computer software for mini and mainframe computers (preprogrammed)",
    "939": "Equipment maintenance and repair services for computers and office machines",
    "984": "Rental or lease services of computers and data processing equipment",
}

TELECOM: dict[str, str] = {
    "725": "Radio communication, telephone, and telecommunication equipment",
    "730": "Radio communication and telecommunication testing equipment",
    "838": "Telecommunication equipment, accessories and supplies",
    "883": "Voice response systems",
}

# Not IT classes. Included because a large share of DC's staff-augmentation and
# systems-integration work is booked here, but they sweep in unrelated work too.
# District-wide these are dominated by Medicaid managed care, food service, and
# healthcare staffing; for a technology agency like OCTO the same class 918 is
# largely IT labor. Filter by agency or vendor, not by class alone.
IT_ADJACENT: dict[str, str] = {
    "918": "Consulting services",
    "958": "Management services",
}

# The catch-all classes. DC books its largest IT staff-augmentation vehicles
# here -- including OCTO STaR II ($120M) and an explicitly titled "Information
# Technology Staff Augmentation Services" contract ($65M) -- so an IT total
# built from IT commodity classes alone misses them entirely. Worse, the coding
# is not even self-consistent: Computer Aid, Inc.'s *contracts* sit in 962 while
# the *purchase orders* against them are coded 918. Nothing here can be summed
# blindly; it has to be filtered on contract title.
MISCELLANEOUS: dict[str, str] = {
    "961": "Miscellaneous services, no. 1 (not otherwise classified)",
    "962": "Miscellaneous services, no. 2 (not otherwise classified)",
}

#: Everything unambiguously technology.
IT_CORE: dict[str, str] = {**SOFTWARE_SERVICES, **IT_GOODS, **TELECOM}

#: Every class we pull, including the ones needing a title-level second look.
IT_ALL: dict[str, str] = {**IT_CORE, **IT_ADJACENT, **MISCELLANEOUS}


def group_for(class_code: str) -> str:
    """Name the grouping a three-digit NIGP class belongs to."""
    if class_code in SOFTWARE_SERVICES:
        return "software_services"
    if class_code in IT_GOODS:
        return "it_goods"
    if class_code in TELECOM:
        return "telecom"
    if class_code in IT_ADJACENT:
        return "it_adjacent"
    if class_code in MISCELLANEOUS:
        return "miscellaneous"
    return "other"


def class_of(nigp_code: str) -> str:
    """Reduce a full NIGP code to its three-digit class.

    >>> class_of("9204680")
    '920'
    """
    return str(nigp_code)[:3]
