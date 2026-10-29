"""Variable browser: every known Motor/Sys/BrickLV/Gate3/EncTable element (Power PMAC) or
I-variable (Turbo) for the selected axis, with descriptions, defaults, editing, export and a
baseline comparison."""
from __future__ import annotations

import csv
import json
import time
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ...core import knowledge as kb
from ...core.models import VariableSet
from .. import theme
from ..context import AppContext
from ..widgets import Card, hbox, label


class VariablesPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.current: Optional[VariableSet] = None
        self.gdump: Optional[VariableSet] = None
        self.baseline: Dict[str, Optional[str]] = {}
        self.ranges: Dict[str, Optional[str]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Variables & configuration", "title"))
        head.addStretch(1)
        self.axis_combo = QComboBox()
        self.axis_combo.setMinimumWidth(130)
        head.addWidget(QLabel("Axis"))
        head.addWidget(self.axis_combo)
        self.group_combo = QComboBox()
        self.group_combo.addItem("All groups", "")
        for g in kb.GROUP_ORDER:
            self.group_combo.addItem(g, g)
        head.addWidget(self.group_combo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter by name or description...")
        self.search.setMinimumWidth(220)
        head.addWidget(self.search)
        self.only_changed = QPushButton("Only non-default")
        self.only_changed.setCheckable(True)
        self.only_changed.setProperty("kind", "chip")
        head.addWidget(self.only_changed)
        self.refresh_btn = QPushButton("Read from controller")
        self.refresh_btn.setProperty("kind", "primary")
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        tools = QHBoxLayout()
        self.range_prefix = QComboBox()
        self.range_prefix.setEditable(True)
        for p in ("P", "Q", "M", "I", "M->"):
            self.range_prefix.addItem(p)
        self.range_a = QSpinBox()
        self.range_a.setRange(0, 65535)
        self.range_b = QSpinBox()
        self.range_b.setRange(0, 65535)
        self.range_b.setValue(63)
        self.range_btn = QPushButton("Read range")
        self.export_btn = QPushButton("Export...")
        self.baseline_btn = QPushButton("Save baseline...")
        self.compare_btn = QPushButton("Compare to baseline...")
        self.clear_cmp = QPushButton("Clear compare")
        tools.addWidget(QLabel("Numbered variables:"))
        tools.addWidget(self.range_prefix)
        tools.addWidget(self.range_a)
        tools.addWidget(QLabel(".."))
        tools.addWidget(self.range_b)
        tools.addWidget(self.range_btn)
        tools.addStretch(1)
        tools.addWidget(self.export_btn)
        tools.addWidget(self.baseline_btn)
        tools.addWidget(self.compare_btn)
        tools.addWidget(self.clear_cmp)
        root.addLayout(tools)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Variable", "Value", "Default", "Baseline", "Description", "Equivalent"])
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(self._edit)
        root.addWidget(self.tree, 1)
        self.status = label("Connect a direct link and press 'Read from controller'.", "muted")
        root.addWidget(self.status)

        self.refresh_btn.clicked.connect(self.refresh)
        self.range_btn.clicked.connect(self.read_range)
        self.export_btn.clicked.connect(self.export)
        self.baseline_btn.clicked.connect(self.save_baseline)
        self.compare_btn.clicked.connect(self.load_baseline)
        self.clear_cmp.clicked.connect(self._clear_baseline)
        self.search.textChanged.connect(self._rebuild)
        self.group_combo.currentIndexChanged.connect(self._rebuild)
        self.only_changed.toggled.connect(self._rebuild)
        self.axis_combo.currentIndexChanged.connect(self._axis_combo_changed)
        ctx.axis_changed.connect(self._axis_changed)
        ctx.connection_changed.connect(self._fill_axes)
        self._fill_axes()

    # ------------------------------------------------------------------ axis selection
    def _fill_axes(self):
        self.axis_combo.blockSignals(True)
        self.axis_combo.clear()
        for n in range(1, self.ctx.naxes + 1):
            self.axis_combo.addItem("Motor %d" % n, n)
        idx = self.axis_combo.findData(self.ctx.current_axis)
        self.axis_combo.setCurrentIndex(max(0, idx))
        self.axis_combo.blockSignals(False)

    def _axis_combo_changed(self, _):
        n = self.axis_combo.currentData()
        if n is not None:
            self.ctx.set_axis(int(n))

    def _axis_changed(self, n: int):
        idx = self.axis_combo.findData(n)
        if idx >= 0 and self.axis_combo.currentIndex() != idx:
            self.axis_combo.blockSignals(True)
            self.axis_combo.setCurrentIndex(idx)
            self.axis_combo.blockSignals(False)
        self.current = self.ctx.session.dumps.get(n)
        self._rebuild()

    # ------------------------------------------------------------------ reading
    def refresh(self):
        n = self.ctx.current_axis
        if not self.ctx.session.direct_ok:
            self.ctx.warn("A direct link is required to read variables (EPICS only exposes polled PVs).")
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText("Reading axis %d configuration..." % n)
        self.ctx.pause_polling(True)

        def work(progress):
            vs = self.ctx.session.dump_axis(n, include_global=True)
            g = self.ctx.session.dump_global()
            return vs, g

        def done(res):
            self.current, self.gdump = res
            self.refresh_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            n_ok = sum(1 for v in self.current.values.values() if v is not None)
            self.status.setText("Axis %d: %d variables read at %s (%d not supported by this controller/firmware)." % (
                n, n_ok, time.strftime("%H:%M:%S"), len(self.current.values) - n_ok))
            self._rebuild()

        def failed(msg):
            self.refresh_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.ctx.error("variable read failed: %s" % msg.splitlines()[0])
            self.status.setText("Read failed.")

        self.ctx.tasks.run(work, done, failed)

    def read_range(self):
        if not self.ctx.session.direct_ok:
            self.ctx.warn("A direct link is required.")
            return
        prefix = self.range_prefix.currentText().strip()
        a, b = self.range_a.value(), self.range_b.value()
        if b < a or b - a > 4096:
            self.ctx.warn("range too large (max 4096)")
            return
        pre = prefix.replace("->", "")
        arrow = prefix.endswith("->")
        self.range_btn.setEnabled(False)
        self.ctx.pause_polling(True)

        def work(progress):
            if arrow:
                out = {}
                link = self.ctx.session.direct
                for k in range(a, b + 1):
                    try:
                        out["M%d->" % k] = link.send("M%d->" % k)
                    except Exception:
                        out["M%d->" % k] = None
                return out
            return self.ctx.session.read_range(pre, a, b)

        def done(res):
            self.ranges.update(res)
            self.range_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self._rebuild()

        def failed(msg):
            self.range_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.ctx.error("range read failed: %s" % msg.splitlines()[0])

        self.ctx.tasks.run(work, done, failed)

    # ------------------------------------------------------------------ view
    def _defs_for_axis(self, n: int, fam: str) -> List[tuple]:
        out = []
        if fam == "power":
            i, j = (n - 1) // 4, (n - 1) % 4
            for v in kb.POWER_MOTOR:
                out.append((v.resolve(m=n), v))
            for v in kb.POWER_CHANNEL:
                out.append((v.resolve(i=i, j=j), v))
            for v in kb.POWER_BRICKLV:
                out.append((v.resolve(j=n - 1) if "{j}" in v.key else v.key, v))
            for v in kb.POWER_ENCTABLE:
                out.append((v.resolve(e=n), v))
            for v in kb.POWER_SYS:
                out.append((v.key, v))
            for c in (1, 2):
                for v in kb.POWER_COORD:
                    out.append((v.resolve(c=c), v))
        else:
            i, j = (n - 1) // 4, (n - 1) % 4 + 1
            for v in kb.TURBO_MOTOR:
                out.append((v.resolve(m=n), v))
            for v in kb.TURBO_GATE:
                out.append((v.resolve(i=i, j=j), v))
            for v in kb.TURBO_GLOBAL:
                out.append((v.key, v))
        return out

    def _rebuild(self):
        self.tree.clear()
        n = self.ctx.current_axis
        fam = self.ctx.family
        vs = self.current if (self.current and self.current.axis == n) else self.ctx.session.dumps.get(n)
        gd = self.gdump or self.ctx.session.global_dump
        text = self.search.text().strip().lower()
        gsel = self.group_combo.currentData()
        only_changed = self.only_changed.isChecked()
        groups: Dict[str, QTreeWidgetItem] = {}
        bold_font = None
        count = 0
        for name, d in self._defs_for_axis(n, fam):
            if gsel and d.group != gsel:
                continue
            val = None
            if vs is not None:
                val = vs.values.get(name)
            if val is None and gd is not None:
                val = gd.values.get(name)
            if text and text not in name.lower() and text not in d.desc.lower() and text not in d.label.lower():
                continue
            differs = _differs(val, d.default)
            if only_changed and not (differs and d.kind == kb.SETUP):
                continue
            grp = groups.get(d.group)
            if grp is None:
                grp = QTreeWidgetItem([d.group])
                grp.setFirstColumnSpanned(True)
                grp.setForeground(0, QBrush(QColor(theme.MUTED)))
                f = grp.font(0)
                f.setBold(True)
                grp.setFont(0, f)
                groups[d.group] = grp
                self.tree.addTopLevelItem(grp)
                grp.setExpanded(True)
            base = self.baseline.get(name)
            it = QTreeWidgetItem([name, "-" if val is None else str(val), d.default, "" if base is None else str(base),
                                  d.desc, d.equiv])
            it.setData(0, Qt.UserRole, name)
            it.setData(1, Qt.UserRole, d.kind)
            it.setToolTip(4, d.desc)
            if val is None and vs is not None:
                it.setForeground(1, QBrush(QColor(theme.MUTED)))
            if d.kind == kb.STATUS:
                it.setForeground(0, QBrush(QColor(theme.INK_2)))
            if differs and d.kind == kb.SETUP and val is not None:
                it.setForeground(1, QBrush(QColor(theme.SERIES[3])))
            if base is not None and val is not None and _differs(val, base):
                for c in range(6):
                    it.setBackground(c, QBrush(QColor("#3a2a1a")))
                it.setForeground(3, QBrush(QColor(theme.SERIOUS)))
            grp.addChild(it)
            count += 1
        if self.ranges:
            grp = QTreeWidgetItem(["Numbered variables (P/Q/M/I ranges)"])
            grp.setFirstColumnSpanned(True)
            f = grp.font(0)
            f.setBold(True)
            grp.setFont(0, f)
            grp.setForeground(0, QBrush(QColor(theme.MUTED)))
            self.tree.addTopLevelItem(grp)
            grp.setExpanded(True)
            for name, val in self.ranges.items():
                if text and text not in name.lower():
                    continue
                if only_changed and (val is None or str(val).strip() in ("0", "0.0", "*")):
                    continue
                d = kb.describe(name, fam)
                it = QTreeWidgetItem([name, "-" if val is None else str(val), d.default if d else "",
                                      "" if self.baseline.get(name) is None else str(self.baseline.get(name)),
                                      d.desc if d else "", d.equiv if d else ""])
                it.setData(0, Qt.UserRole, name)
                it.setData(1, Qt.UserRole, kb.SETUP)
                grp.addChild(it)
                count += 1
        if vs is None:
            self.status.setText("No data read yet for axis %d - showing the knowledge base only. "
                                "Press 'Read from controller'." % n)

    # ------------------------------------------------------------------ edit
    def _edit(self, item: QTreeWidgetItem, col: int):
        name = item.data(0, Qt.UserRole)
        if not name:
            return
        if item.data(1, Qt.UserRole) == kb.STATUS:
            QMessageBox.information(self, "Read-only", "%s is a status element." % name)
            return
        if not self.ctx.session.direct_ok:
            self.ctx.warn("A direct link is required to write variables.")
            return
        cur = item.text(1)
        val, ok = QInputDialog.getText(self, "Write variable", "%s =" % name, text="" if cur == "-" else cur)
        if not ok or not val.strip():
            return
        try:
            self.ctx.session.direct.send("%s=%s" % (name, val.strip()))
            new = self.ctx.session.direct.send(name)
            item.setText(1, self.ctx.session.direct._strip_name(new))
            if self.current is not None:
                self.current.values[name] = item.text(1)
            self.ctx.ok("%s=%s written (value now %s). Remember: not saved to flash until you 'save'." % (
                name, val.strip(), item.text(1)))
        except Exception as exc:
            self.ctx.error("write failed: %s" % exc)

    # ------------------------------------------------------------------ files
    def _all_values(self) -> Dict[str, Optional[str]]:
        out: Dict[str, Optional[str]] = {}
        if self.gdump:
            out.update(self.gdump.values)
        if self.current:
            out.update(self.current.values)
        out.update(self.ranges)
        return out

    def export(self):
        vals = self._all_values()
        if not vals:
            self.ctx.warn("nothing to export - read the controller first")
            return
        path, flt = QFileDialog.getSaveFileName(self, "Export variables", "pmac_axis%d_%s.csv" % (
            self.ctx.current_axis, time.strftime("%Y%m%d_%H%M")), "CSV (*.csv);;JSON (*.json);;PMAC script (*.pmc *.txt)")
        if not path:
            return
        fam = self.ctx.family
        try:
            if path.lower().endswith(".json"):
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"family": fam, "axis": self.ctx.current_axis, "timestamp": time.time(), "values": vals},
                              fh, indent=2)
            elif path.lower().endswith((".pmc", ".txt")):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("// PMAC Doctor export %s axis %d\n" % (time.ctime(), self.ctx.current_axis))
                    for k, v in vals.items():
                        d = kb.describe(k, fam)
                        if v is not None and (d is None or d.kind == kb.SETUP):
                            fh.write("%s=%s%s\n" % (k, v, ("  // " + d.desc) if d else ""))
            else:
                with open(path, "w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(["variable", "value", "default", "description", "equivalent", "kind"])
                    for k, v in vals.items():
                        d = kb.describe(k, fam)
                        w.writerow([k, "" if v is None else v, d.default if d else "", d.desc if d else "",
                                    d.equiv if d else "", d.kind if d else ""])
            self.ctx.ok("exported %d variables to %s" % (len(vals), path))
        except OSError as exc:
            self.ctx.error("export failed: %s" % exc)

    def save_baseline(self):
        vals = self._all_values()
        if not vals:
            self.ctx.warn("read the controller first")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save baseline", "baseline_axis%d.json" % self.ctx.current_axis,
                                              "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"family": self.ctx.family, "axis": self.ctx.current_axis, "values": vals}, fh, indent=2)
        self.ctx.ok("baseline saved: %s" % path)

    def load_baseline(self):
        path, _ = QFileDialog.getOpenFileName(self, "Compare to baseline", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.baseline = data.get("values", data)
            n_diff = sum(1 for k, v in self.baseline.items() if _differs(self._all_values().get(k), v))
            self.ctx.ok("baseline loaded (%d variables, %d differ from the current values - highlighted)" % (
                len(self.baseline), n_diff))
            self._rebuild()
        except (OSError, ValueError) as exc:
            self.ctx.error("could not load baseline: %s" % exc)

    def _clear_baseline(self):
        self.baseline = {}
        self._rebuild()


def _differs(a, b) -> bool:
    if a is None or b in (None, ""):
        return False
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return False
    try:
        fa = float(int(sa[1:], 16)) if sa.startswith("$") else float(sa)
        fb = float(int(sb[1:], 16)) if sb.startswith("$") else float(sb.split()[0])
        return abs(fa - fb) > 1e-9 * max(1.0, abs(fb))
    except (ValueError, IndexError):
        return sa.lower() != sb.lower()
