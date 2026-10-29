"""PMAC Doctor - diagnostic and tuning assistant for Delta Tau / Omron PMAC motion controllers.

Targets Power PMAC based drives (Power Brick LV, Power Brick AC, CK3E, UMAC) and
Turbo PMAC / Geobrick controllers, attaching to an EPICS IOC running the
DiamondLightSource `pmac` driver and, optionally, directly to the controller
(SSH/gpascii for Power PMAC, Delta Tau Ethernet protocol for Turbo).
"""

__version__ = "1.0.0"
__app_name__ = "PMAC Doctor"
__org__ = "NSLS-II"
