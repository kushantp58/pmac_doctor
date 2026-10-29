"""Terminal: send raw on-line commands, view the communication log."""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget, QCheckBox

from .. import theme
from ..context import AppContext
from ..widgets import LogView, label

QUICK = [("?", "?"), ("Global ???", "???"), ("#n?", "#{n}?"), ("#n position", "#{n}p"), ("#n foll. err", "#{n}f"),
         ("cid", "cid"), ("vers", "vers"), ("cpu", "cpu"), ("date", "date"), ("Sys.WDTFault", "Sys.WDTFault"),
         ("BrickLV status", "BrickLV.Monitor BrickLV.BusUnderVoltage BrickLV.BusOverVoltage BrickLV.OverTemp"),
         ("Motor n gains", "Motor[{n}].Servo.Kp Motor[{n}].Servo.Kvfb Motor[{n}].Servo.Kvff Motor[{n}].Servo.Kaff Motor[{n}].Servo.Ki"),
         ("Feedrate &1%", "&1%"), ("List PLC 1", "list plc 1")]


class TerminalPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.history: List[str] = []
        self.hidx = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("Terminal", "title"))
        head.addStretch(1)
        self.show_comm = QCheckBox("show background polling traffic")
        head.addWidget(self.show_comm)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.out.clear())
        head.addWidget(clear)
        root.addLayout(head)
        root.addWidget(label("On-line commands go to the direct link; with EPICS only they are written to $(P):SendCmd "
                             "(write-only, no reply). Up/Down recall history. Dangerous commands ($$$***, save) ask first.",
                             "muted", wrap=True))
        quick = QHBoxLayout()
        quick.setSpacing(6)
        for lbl, cmd in QUICK:
            b = QPushButton(lbl)
            b.setProperty("kind", "chip")
            b.clicked.connect(lambda _=False, c=cmd: self.send(c.replace("{n}", str(self.ctx.current_axis))))
            quick.addWidget(b)
        quick.addStretch(1)
        root.addLayout(quick)
        self.out = LogView()
        root.addWidget(self.out, 1)
        row = QHBoxLayout()
        prompt = QLabel(">")
        prompt.setStyleSheet("font-family:%s;color:%s;font-size:12pt;" % (theme.MONO, theme.ACCENT))
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("Motor[1].Servo.Kp    #1j+    I130    ?")
        self.inp.setStyleSheet("font-family:%s;font-size:10.5pt;" % theme.MONO)
        self.inp.returnPressed.connect(self._enter)
        self.inp.installEventFilter(self)
        send = QPushButton("Send")
        send.setProperty("kind", "primary")
        send.clicked.connect(self._enter)
        row.addWidget(prompt)
        row.addWidget(self.inp, 1)
        row.addWidget(send)
        root.addLayout(row)
        self._attach_listener()
        ctx.connection_changed.connect(self._attach_listener)

    def _attach_listener(self):
        link = self.ctx.session.direct
        if link is not None and self._on_traffic not in link.listeners:
            link.listeners.append(self._on_traffic)

    def _on_traffic(self, cmd, reply, dt):
        if self.show_comm.isChecked():
            # called from the poller thread -> use queued invocation through signal
            self.ctx.log.emit("TRAFFIC %s -> %s" % (cmd[:80], reply.replace("\n", " | ")[:120]), "traffic")

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.inp and ev.type() == ev.Type.KeyPress:
            if ev.key() == Qt.Key_Up and self.history:
                self.hidx = max(0, self.hidx - 1)
                self.inp.setText(self.history[self.hidx])
                return True
            if ev.key() == Qt.Key_Down and self.history:
                self.hidx = min(len(self.history), self.hidx + 1)
                self.inp.setText(self.history[self.hidx] if self.hidx < len(self.history) else "")
                return True
        return super().eventFilter(obj, ev)

    def _enter(self):
        cmd = self.inp.text().strip()
        if not cmd:
            return
        self.history.append(cmd)
        self.hidx = len(self.history)
        self.inp.clear()
        self.send(cmd)

    def send(self, cmd: str):
        low = cmd.lower().strip()
        if low in ("$$$***", "save", "$$$", "reboot") or low.startswith("$$$"):
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, "Confirm", "'%s' changes the controller state persistently. Send it?" % cmd,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.out.append_line("> " + cmd, theme.ACCENT)
        try:
            reply = self.ctx.session.send(cmd)
            for ln in (reply.split("\n") if reply else ["(ok)"]):
                self.out.append_line("  " + ln)
        except Exception as exc:
            self.out.append_line("  ERROR: %s" % exc, theme.CRITICAL)

    def log_traffic(self, msg: str):
        self.out.append_line(msg, theme.MUTED)
