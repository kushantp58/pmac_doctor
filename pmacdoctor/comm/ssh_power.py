"""Power PMAC direct link over SSH running ``gpascii -2`` (the same mechanism the Diamond
EPICS driver and the Power PMAC IDE use).

* login: root / deltatau (factory default), port 22
* after ``gpascii -2`` starts it prints ``STDIN Open for ASCII Input``; every command is
  terminated by LF and every reply by ACK (0x06). Errors are BELL + text.
* we send ``echo 7`` so that queries return bare values (``40`` instead of
  ``Motor[1].Servo.Kp=40``).

Data gathering upload uses a second exec channel running the on-board ``gather`` utility.
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Sequence

from .base import ACK, BELL, DirectLink, LinkInfo, PmacCommError, PmacError

try:
    import paramiko  # type: ignore
except Exception:  # pragma: no cover
    paramiko = None

GATHER_FILE = "/var/ftp/gather/pmacdoctor_gather.txt"


class PowerSshLink(DirectLink):
    family = "power"
    name = "ssh"

    def __init__(self, host: str, port: int = 22, username: str = "root",
                 password: str = "deltatau", timeout: float = 5.0):
        super().__init__()
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.timeout = timeout
        self._client = None
        self._chan = None
        self._buf = ""

    # ------------------------------------------------------------------ transport
    def _open(self):
        if paramiko is None:
            raise PmacCommError("paramiko is not installed")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self.host, port=self.port, username=self.username,
                       password=self.password, look_for_keys=False, allow_agent=False,
                       timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout)
        transport = client.get_transport()
        transport.set_keepalive(15)
        chan = transport.open_session()
        chan.settimeout(self.timeout)
        chan.exec_command("gpascii -2")
        self._client, self._chan = client, chan
        self._buf = ""
        self._read_until("STDIN Open for ASCII Input", timeout=self.timeout)
        self._buf = ""
        # bare values in replies
        self._transact("echo 7")

    def _close(self):
        for obj in (self._chan, self._client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._chan = self._client = None

    def _read_until(self, token: str, timeout: float) -> str:
        assert self._chan is not None
        deadline = time.time() + timeout
        while token not in self._buf:
            if time.time() > deadline:
                raise PmacCommError("timeout waiting for %r" % token)
            if self._chan.recv_ready():
                data = self._chan.recv(65536)
                if not data:
                    raise PmacCommError("gpascii closed the connection")
                self._buf += data.decode("latin-1")
            else:
                if self._chan.exit_status_ready():
                    raise PmacCommError("gpascii exited")
                time.sleep(0.002)
        idx = self._buf.index(token) + len(token)
        out, self._buf = self._buf[:idx], self._buf[idx:]
        return out

    def _transact(self, cmd: str) -> str:
        assert self._chan is not None
        self._chan.sendall((cmd + "\n").encode("latin-1"))
        out = self._read_until(ACK, timeout=self.timeout)
        return out

    # ------------------------------------------------------------------ identify
    def _identify(self):
        info = LinkInfo(family="power", description="Power PMAC @ %s" % self.host)
        for key, cmd in (("cid", "cid"), ("cpu", "cpu"), ("firmware", "vers")):
            try:
                v = self.send(cmd)
                setattr(info, key, int(float(v)) if key == "cid" else v)
            except Exception:
                pass
        try:
            info.servo_period_ms = float(self.send("Sys.ServoPeriod"))
        except Exception:
            pass
        try:
            info.phase_over_servo = float(self.send("Sys.PhaseOverServoPeriod"))
        except Exception:
            pass
        try:
            info.max_motors = int(float(self.send("Sys.MaxMotors")))
        except Exception:
            info.max_motors = 8
        try:
            info.extras["type"] = self.send("type")
        except Exception:
            pass
        self.info = info

    # ------------------------------------------------------------------ gather
    def supports_gather(self) -> bool:
        return True

    def gather(self, axis: int, items: Sequence[str], period: int, samples: int,
               trigger) -> Optional[Dict[str, List[float]]]:
        """Configure Gather.*, run ``trigger()`` (which starts the move), wait, then upload.

        items are structure elements such as ``Motor[1].ActPos``. Returns dict name -> list.
        Falls back to None if the on-board ``gather`` upload utility is not available; the
        caller then uses ASCII polling.
        """
        n = len(items)
        try:
            self.send("Gather.Enable=0")
            for k, it in enumerate(items):
                self.send("Gather.Addr[%d]=%s.a" % (k, it))
            self.send("Gather.Items=%d" % n)
            self.send("Gather.Period=%d" % max(1, int(period)))
            self.send("Gather.MaxSamples=%d" % int(samples))
            self.send("Gather.MaxLines=%d" % int(samples))
            self.send("Gather.Enable=2")
            trigger()
            # wait for completion
            servo_ms = self.info.servo_period_ms or 0.5
            total = samples * period * servo_ms / 1000.0
            deadline = time.time() + total + 2.0
            while time.time() < deadline:
                try:
                    if float(self.send("Gather.Samples")) >= samples:
                        break
                except Exception:
                    break
                time.sleep(0.05)
            self.send("Gather.Enable=0")
            text = self._upload_gather()
            if not text:
                return None
            return parse_gather_text(text, list(items))
        except PmacError:
            return None

    def _upload_gather(self) -> str:
        assert self._client is not None
        cmd = "gather %s -u 2>/dev/null || /opt/ppmac/gather/gather %s -u; cat %s" % (
            GATHER_FILE, GATHER_FILE, GATHER_FILE)
        try:
            _in, out, _err = self._client.exec_command(cmd, timeout=self.timeout * 4)
            data = out.read().decode("latin-1", "replace")
            return data
        except Exception:
            return ""


def parse_gather_text(text: str, names: List[str]) -> Optional[Dict[str, List[float]]]:
    """Parse the whitespace separated numeric table written by the ``gather -u`` utility."""
    cols: List[List[float]] = [[] for _ in names]
    for ln in text.splitlines():
        parts = ln.replace(",", " ").split()
        if len(parts) < len(names):
            continue
        try:
            vals = [float(p) for p in parts[: len(names)]]
        except ValueError:
            continue
        for c, v in zip(cols, vals):
            c.append(v)
    if not cols[0]:
        return None
    return {n: c for n, c in zip(names, cols)}
