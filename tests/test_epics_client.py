"""The EPICS client must construct, start monitors and stop cleanly even without an IOC
(this also proves the Channel Access shared library bundled with pyepics loads)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from pmacdoctor.comm import epics_client  # noqa: E402


@pytest.mark.skipif(not epics_client.HAVE_EPICS, reason="pyepics not installed")
def test_epics_client_lifecycle():
    os.environ.setdefault("EPICS_CA_AUTO_ADDR_LIST", "NO")
    os.environ.setdefault("EPICS_CA_ADDR_LIST", "127.0.0.1")
    cfg = epics_client.EpicsConfig(prefix="PMACDOCTOR-TEST-NOIOC", naxes=2)
    assert cfg.motor_pv(1) == "PMACDOCTOR-TEST-NOIOC:M1"
    cfg2 = epics_client.EpicsConfig(prefix="XF:31ID", naxes=1, motor_pv_template="XF:31ID{{Mtr:{nn}}}Mtr")
    assert cfg2.motor_pv(3) == "XF:31ID{Mtr:03}Mtr"
    c = epics_client.EpicsClient(cfg)
    c.start()
    time.sleep(0.5)
    assert c.controller_connected() is False
    assert c.family() is None
    assert c.ctrl("PMACTYPE") is None
    c.stop()
