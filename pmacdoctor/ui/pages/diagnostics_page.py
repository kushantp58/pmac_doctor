"""Diagnostics page: run the rule engine over a fresh snapshot (+ full dumps) and present
ranked findings with probable causes and actions; export a report."""
from __future__ import annotations

import html
import time
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core import rules
from ...core.models import Finding
from .. import theme
from ..context import AppContext
from ..widgets import Badge, label


class DiagnosticsPage(QWidget):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("page")
        self.findings: List[Finding] = []
        self.ran_at: Optional[float] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Diagnostics", "title"))
        head.addStretch(1)
        self.scope = QComboBox()
        self.scope.addItem("All axes", 0)
        head.addWidget(QLabel("Scope"))
        head.addWidget(self.scope)
        self.deep = QCheckBox("read full configuration (direct link)")
        self.deep.setChecked(True)
        head.addWidget(self.deep)
        self.run_btn = QPushButton("Run diagnosis")
        self.run_btn.setProperty("kind", "primary")
        head.addWidget(self.run_btn)
        self.export_btn = QPushButton("Export report...")
        head.addWidget(self.export_btn)
        root.addLayout(head)

        sub = QHBoxLayout()
        self.summary_badge = Badge("not run", "neutral")
        self.summary = label("Press 'Run diagnosis'. With a direct link the full motor/amplifier configuration is read "
                             "and checked against the manual's recommendations; with EPICS only, status words and motor "
                             "records are analysed.", "muted", wrap=True)
        sub.addWidget(self.summary_badge)
        sub.addWidget(self.summary, 1)
        self.f_fault = QCheckBox("faults")
        self.f_warn = QCheckBox("warnings")
        self.f_info = QCheckBox("notes")
        for cb in (self.f_fault, self.f_warn, self.f_info):
            cb.setChecked(True)
            cb.toggled.connect(self._rebuild)
            sub.addWidget(cb)
        root.addLayout(sub)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["", "Axis", "Finding"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(self._show)
        split.addWidget(self.tree)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.detail.setStyleSheet("QTextBrowser{background:%s;border:1px solid %s;border-radius:8px;padding:10px;}" % (
            theme.SURFACE, theme.BORDER))
        split.addWidget(self.detail)
        split.setSizes([520, 560])
        root.addWidget(split, 1)

        self.run_btn.clicked.connect(self.run)
        self.export_btn.clicked.connect(self.export)
        ctx.connection_changed.connect(self._fill_scope)
        self._fill_scope()

    def _fill_scope(self):
        cur = self.scope.currentData()
        self.scope.blockSignals(True)
        self.scope.clear()
        self.scope.addItem("All axes", 0)
        for n in range(1, self.ctx.naxes + 1):
            self.scope.addItem("Motor %d" % n, n)
        idx = self.scope.findData(cur or 0)
        self.scope.setCurrentIndex(max(0, idx))
        self.scope.blockSignals(False)

    # ------------------------------------------------------------------
    def run(self):
        s = self.ctx.session
        if not s.direct_ok and s.epics is None:
            self.ctx.warn("connect to the IOC or the controller first (Settings)")
            return
        scope = self.scope.currentData() or 0
        axes = None if scope == 0 else [scope]
        deep = self.deep.isChecked() and s.direct_ok
        self.run_btn.setEnabled(False)
        self.summary_badge.set_severity("neutral", "running...")
        self.ctx.pause_polling(True)

        def work(progress):
            if deep:
                targets = axes or list(range(1, s.naxes + 1))
                for n in targets:
                    progress("reading configuration of axis %d..." % n)
                    s.dump_axis(n, include_global=(n == targets[0]))
                progress("reading global configuration...")
                s.dump_global()
            progress("polling status...")
            snap = s.poll()
            if s.epics is not None and not s.direct_ok:
                time.sleep(0.5)
                snap = s.poll()
            return rules.evaluate(snap, s.dumps, s.global_dump, self.ctx.motor_specs, axes)

        def done(findings):
            self.findings = findings
            self.ran_at = time.time()
            self.run_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            c = rules.summarize(findings)
            if c["fault"]:
                self.summary_badge.set_severity("fault", "%d fault%s" % (c["fault"], "s" if c["fault"] != 1 else ""))
            elif c["warning"]:
                self.summary_badge.set_severity("warning", "%d warning%s" % (c["warning"], "s" if c["warning"] != 1 else ""))
            else:
                self.summary_badge.set_severity("ok", "healthy")
            self.summary.setText("%s · %d faults, %d warnings, %d notes%s" % (
                time.strftime("%H:%M:%S"), c["fault"], c["warning"], c["info"],
                " · full configuration analysed" if deep else " · status-only analysis (no direct link)"))
            self._rebuild()
            self.ctx.ok("diagnosis complete: %d findings" % len(findings))

        def failed(msg):
            self.run_btn.setEnabled(True)
            self.ctx.pause_polling(False)
            self.summary_badge.set_severity("fault", "error")
            self.ctx.error("diagnosis failed: %s" % msg.splitlines()[0])

        self.ctx.tasks.run(work, done, failed, lambda m: self.summary.setText(m))

    def _rebuild(self):
        self.tree.clear()
        show = {"fault": self.f_fault.isChecked(), "warning": self.f_warn.isChecked(), "info": self.f_info.isChecked(), "ok": True}
        for f in self.findings:
            if not show.get(f.severity, True):
                continue
            it = QTreeWidgetItem([theme.SEVERITY_ICON.get(f.severity, ""), "-" if f.axis is None else str(f.axis), f.title])
            it.setForeground(0, QBrush(QColor(theme.SEVERITY_COLOR.get(f.severity, theme.INK))))
            it.setForeground(2, QBrush(QColor(theme.INK if f.severity != "info" else theme.INK_2)))
            it.setData(0, Qt.UserRole, f)
            it.setToolTip(2, f.detail)
            self.tree.addTopLevelItem(it)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self.detail.setHtml("<p style='color:%s'>No findings in this category.</p>" % theme.INK_2)

    def _show(self, cur, _prev):
        if cur is None:
            return
        f: Finding = cur.data(0, Qt.UserRole)
        self.detail.setHtml(finding_html(f))

    def export(self):
        if not self.findings:
            self.ctx.warn("run a diagnosis first")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export report", "pmac_diagnosis_%s.html" % time.strftime("%Y%m%d_%H%M"),
                                              "HTML (*.html);;Markdown (*.md)")
        if not path:
            return
        snap = self.ctx.last_snapshot
        title = "PMAC Doctor report - %s - %s" % (snap.description if snap else "", time.ctime(self.ran_at or time.time()))
        try:
            with open(path, "w", encoding="utf-8") as fh:
                if path.lower().endswith(".md"):
                    fh.write("# %s\n\n" % title)
                    for f in self.findings:
                        fh.write("## [%s] %s%s\n\n%s\n\n" % (f.severity.upper(), f.title,
                                                               (" (axis %d)" % f.axis) if f.axis else "", f.detail))
                        if f.causes:
                            fh.write("**Possible causes**\n\n" + "".join("- %s\n" % c for c in f.causes) + "\n")
                        if f.actions:
                            fh.write("**Suggested actions**\n\n" + "".join("- %s\n" % a for a in f.actions) + "\n")
                        if f.evidence:
                            fh.write("Evidence: " + ", ".join("%s=%s" % kv for kv in f.evidence.items()) + "\n\n")
                        if f.reference:
                            fh.write("Reference: %s\n\n" % f.reference)
                else:
                    fh.write("<html><head><meta charset='utf-8'><title>%s</title><style>body{font-family:Segoe UI,sans-serif;"
                             "max-width:960px;margin:24px auto;color:#222} .f{border-left:5px solid #ccc;padding:8px 14px;"
                             "margin:12px 0;background:#fafafa} .fault{border-color:#d03b3b} .warning{border-color:#fab219}"
                             " .info{border-color:#2a78d6} h3{margin:0 0 6px 0}</style></head><body>" % html.escape(title))
                    fh.write("<h1>%s</h1>" % html.escape(title))
                    for f in self.findings:
                        fh.write("<div class='f %s'>%s</div>" % (f.severity, finding_html(f, light=True)))
                    fh.write("</body></html>")
            self.ctx.ok("report written to %s" % path)
        except OSError as exc:
            self.ctx.error("export failed: %s" % exc)


def finding_html(f: Finding, light: bool = False) -> str:
    color = theme.SEVERITY_COLOR.get(f.severity, theme.INK)
    ink = "#222" if light else theme.INK
    ink2 = "#555" if light else theme.INK_2
    parts = ["<h3 style='color:%s;margin:0 0 4px 0'>%s %s</h3>" % (color, theme.SEVERITY_ICON.get(f.severity, ""), html.escape(f.title))]
    if f.axis:
        parts.append("<p style='color:%s;margin:0 0 8px 0'>Axis %d · severity: %s · rule <code>%s</code></p>" % (
            ink2, f.axis, f.severity, html.escape(f.rule_id)))
    else:
        parts.append("<p style='color:%s;margin:0 0 8px 0'>Controller · severity: %s · rule <code>%s</code></p>" % (
            ink2, f.severity, html.escape(f.rule_id)))
    if f.detail:
        parts.append("<p style='color:%s'>%s</p>" % (ink, html.escape(f.detail)))
    if f.causes:
        parts.append("<p style='color:%s;margin-bottom:2px'><b>Possible causes</b></p><ul style='margin-top:2px'>%s</ul>" % (
            ink, "".join("<li style='color:%s'>%s</li>" % (ink, html.escape(c)) for c in f.causes)))
    if f.actions:
        parts.append("<p style='color:%s;margin-bottom:2px'><b>Suggested actions</b></p><ul style='margin-top:2px'>%s</ul>" % (
            ink, "".join("<li style='color:%s'>%s</li>" % (ink, html.escape(a)) for a in f.actions)))
    if f.evidence:
        parts.append("<p style='color:%s'><b>Evidence:</b> %s</p>" % (
            ink2, ", ".join("%s = %s" % (html.escape(str(k)), html.escape(_fmt(v))) for k, v in f.evidence.items())))
    if f.reference:
        parts.append("<p style='color:%s'><i>Reference: %s</i></p>" % (ink2, html.escape(f.reference)))
    return "".join(parts)


def _fmt(v) -> str:
    if isinstance(v, float):
        return "%.6g" % v
    return str(v)
