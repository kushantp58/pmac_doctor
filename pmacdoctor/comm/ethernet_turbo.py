"""Turbo PMAC / Geobrick direct link using the Delta Tau Ethernet protocol (TCP 1025).

Packet layout (8-byte header, big-endian words) followed by up to 1492 data bytes::

    RequestType (1)  0x40 = VR_DOWNLOAD (host -> PMAC), 0xC0 = VR_UPLOAD (PMAC -> host)
    Request     (1)  see VR_* constants
    wValue      (2)
    wIndex      (2)
    wLength     (2)  number of data bytes

Replies to GETRESPONSE are terminated by ACK (0x06); multi-line replies separate lines
with CR (0x0D); errors are BELL (0x07) followed by ``ERRxxx`` when I6=1.
This is the same protocol implemented by the Diamond ``pmacAsynIPPort`` asyn driver.
"""
from __future__ import annotations

import socket
import struct
from typing import Optional

from .base import ACK, BELL, DirectLink, LinkInfo, PmacCommError, PmacError

VR_UPLOAD = 0xC0
VR_DOWNLOAD = 0x40

VR_PMAC_SENDLINE = 0xB0
VR_PMAC_GETLINE = 0xB1
VR_PMAC_FLUSH = 0xB3
VR_PMAC_GETMEM = 0xB4
VR_PMAC_SETMEM = 0xB5
VR_PMAC_SETBIT = 0xBA
VR_PMAC_SETBITS = 0xBB
VR_PMAC_PORT = 0xBE
VR_PMAC_GETRESPONSE = 0xBF
VR_PMAC_READREADY = 0xC2
VR_CTRL_RESPONSE = 0xC4
VR_PMAC_GETBUFFER = 0xC5
VR_PMAC_WRITEBUFFER = 0xC6
VR_PMAC_WRITEERROR = 0xC7
VR_FWDOWNLOAD = 0xCB
VR_IPADDRESS = 0xE0

MAX_DATA = 1400


def build_packet(request_type: int, request: int, value: int = 0, index: int = 0,
                 data: bytes = b"") -> bytes:
    return struct.pack(">BBHHH", request_type, request, value, index, len(data)) + data


class TurboEthernetLink(DirectLink):
    family = "turbo"
    name = "ethernet"

    def __init__(self, host: str, port: int = 1025, timeout: float = 2.0):
        super().__init__()
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None

    # ------------------------------------------------------------------ transport
    def _open(self):
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s
        self._flush()

    def _close(self):
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _xfer(self, pkt: bytes, expect: int) -> bytes:
        assert self._sock is not None
        self._sock.sendall(pkt)
        return self._recv(expect)

    def _recv(self, nbytes: int) -> bytes:
        assert self._sock is not None
        try:
            data = self._sock.recv(nbytes)
        except socket.timeout as exc:
            raise PmacCommError("timeout waiting for PMAC reply") from exc
        if not data:
            raise PmacCommError("connection closed by PMAC")
        return data

    def _flush(self):
        self._xfer(build_packet(VR_DOWNLOAD, VR_PMAC_FLUSH), 1)

    def _transact(self, cmd: str) -> str:
        data = cmd.encode("ascii", "replace")
        if len(data) > MAX_DATA:
            raise PmacError("command too long")
        chunk = self._xfer(build_packet(VR_DOWNLOAD, VR_PMAC_GETRESPONSE, data=data), MAX_DATA)
        buf = bytearray(chunk)
        # keep reading until ACK or BELL terminates the response
        guard = 0
        while ACK.encode() not in buf and BELL.encode() not in buf and guard < 200:
            guard += 1
            if not self._read_ready():
                break
            buf += self._xfer(build_packet(VR_UPLOAD, VR_PMAC_GETBUFFER, data=b""), MAX_DATA)
        text = buf.decode("ascii", "replace")
        # drop anything after the ACK
        if ACK in text:
            text = text[: text.index(ACK) + 1]
        return text

    def _read_ready(self) -> bool:
        r = self._xfer(build_packet(VR_UPLOAD, VR_PMAC_READREADY, data=b"\x00\x00"), 2)
        return len(r) >= 1 and r[0] != 0

    # ------------------------------------------------------------------ identify
    def _identify(self):
        info = LinkInfo(family="turbo", description="Turbo PMAC @ %s:%d" % (self.host, self.port))
        try:
            info.cid = int(float(self.send("cid")))
        except Exception:
            info.cid = 0
        try:
            info.cpu = self.send("cpu")
        except Exception:
            pass
        try:
            info.firmware = self.send("vers")
        except Exception:
            pass
        try:
            i10 = float(self.send("I10"))
            if i10 > 0:
                info.servo_period_ms = i10 / 8388608.0
        except Exception:
            pass
        info.max_motors = 32 if info.cid != 603382 else 8
        self.info = info
