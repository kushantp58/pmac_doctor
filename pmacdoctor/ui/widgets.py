"""Small reusable widgets: cards, KPI tiles, status badges, bit grids, log view."""
from __future__ import annotations

from typing import Iterable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from ..core.status_bits import DecodedBit


def label(text: str = "", role: str = "", wrap: bool = False, align=None) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if align is not None:
        lb.setAlignment(align)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


class Card(QFrame):
    """A rounded surface with an optional small-caps title."""

    clicked = Signal()

    def __init__(self, title: str = "", parent=None, clickable: bool = False):
        super().__init__(parent)
        self.setProperty("kind", "card")
        self.setProperty("tone", "ok")
        self._clickable = clickable
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(14, 12, 14, 12)
        self.outer.setSpacing(8)
        self.title_label: Optional[QLabel] = None
        if title:
            self.title_label = label(title.upper(), "cardtitle")
            self.outer.addWidget(self.title_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        self.outer.addLayout(self.body)
        self.outer.addStretch(1)  # keep content top-aligned when the card is stretched by its layout
        if clickable:
            self.setCursor(Qt.PointingHandCursor)

    def set_tone(self, tone: str):
        self.setProperty("tone", tone)
        repolish(self)

    def set_selected(self, sel: bool):
        self.setProperty("selected", "true" if sel else "false")
        repolish(self)

    def mousePressEvent(self, ev):  # noqa: N802
        if self._clickable and ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class Badge(QLabel):
    def __init__(self, text: str = "", severity: str = "neutral", parent=None):
        super().__init__(text, parent)
        self.setProperty("role", "badge")
        self.setAlignment(Qt.AlignCenter)
        self.set_severity(severity)

    def set_severity(self, severity: str, text: Optional[str] = None):
        color = theme.SEVERITY_COLOR.get(severity, theme.NEUTRAL)
        fg = "#0b0b0b" if severity == "warning" else "white"
        self.setStyleSheet("QLabel{background:%s;color:%s;border-radius:9px;padding:2px 8px;font-size:8.5pt;"
                           "font-weight:700;}" % (color, fg))
        if text is not None:
            self.setText(text)


class StatusDot(QWidget):
    """A coloured dot (status LED) with optional label."""

    def __init__(self, text: str = "", color: str = theme.NEUTRAL, parent=None, size: int = 10):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.dot = _Dot(self._color, size)
        self.text = QLabel(text)
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        lay.addStretch(1)

    def set_state(self, color: str, text: Optional[str] = None):
        self.dot.set_color(QColor(color))
        if text is not None:
            self.text.setText(text)


class _Dot(QWidget):
    def __init__(self, color: QColor, size: int):
        super().__init__()
        self._c = color
        self._s = size
        self.setFixedSize(size + 2, size + 2)

    def set_color(self, c: QColor):
        self._c = c
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(self._c)
        p.drawEllipse(1, 1, self._s, self._s)


class Kpi(QWidget):
    """Stat tile: small caption on top, big value, optional unit/sub text."""

    def __init__(self, caption: str, value: str = "-", unit: str = "", small: bool = False, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.caption = label(caption.upper(), "cardtitle")
        row = QHBoxLayout()
        row.setSpacing(4)
        self.value = label(value, "kpi-small" if small else "kpi")
        self.unit = label(unit, "muted")
        row.addWidget(self.value)
        row.addWidget(self.unit, 0, Qt.AlignBottom)
        row.addStretch(1)
        lay.addWidget(self.caption)
        lay.addLayout(row)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def set(self, value, unit: Optional[str] = None, fmt: str = "{:.4g}"):
        if value is None:
            self.value.setText("-")
        elif isinstance(value, float):
            self.value.setText(fmt.format(value))
        else:
            self.value.setText(str(value))
        if unit is not None:
            self.unit.setText(unit)


class BitGrid(QWidget):
    """Shows decoded status bits as coloured pills."""

    def __init__(self, columns: int = 4, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(4)
        self._items: List[QLabel] = []

    def set_bits(self, bits: Iterable[DecodedBit], hide_reserved: bool = True, only_set: bool = False):
        for w in self._items:
            self.grid.removeWidget(w)
            w.deleteLater()
        self._items.clear()
        k = 0
        for b in bits:
            if hide_reserved and b.name.lower().startswith("reserved"):
                continue
            if only_set and not b.value:
                continue
            lb = QLabel("%s" % b.name)
            lb.setToolTip("bit %d: %s" % (b.bit, b.description))
            if b.value:
                color = theme.SEVERITY_COLOR.get(b.severity, theme.ACCENT)
                fg = "#0b0b0b" if b.severity == "warning" else "white"
                if b.severity in ("ok",):
                    color = theme.GOOD
                lb.setStyleSheet("QLabel{background:%s;color:%s;border-radius:4px;padding:2px 6px;font-size:8.5pt;}"
                                 % (color, fg))
            else:
                dim = theme.SEVERITY_COLOR.get(b.severity, None) if b.severity in ("warning", "fault") else None
                border = dim or theme.BORDER
                lb.setStyleSheet("QLabel{background:transparent;color:%s;border:1px solid %s;border-radius:4px;"
                                 "padding:2px 6px;font-size:8.5pt;}" % (theme.MUTED, border))
            self.grid.addWidget(lb, k // self.columns, k % self.columns)
            self._items.append(lb)
            k += 1


class LogView(QPlainTextEdit):
    def __init__(self, parent=None, max_lines: int = 3000):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setStyleSheet("QPlainTextEdit{font-family:%s;font-size:9pt;background:%s;}" % (theme.MONO, theme.PAGE))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def append_line(self, text: str, color: Optional[str] = None):
        if color:
            self.appendHtml('<span style="color:%s">%s</span>' % (color, _esc(text)))
        else:
            self.appendPlainText(text)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class HLine(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.HLine)
        self.setStyleSheet("color:%s;background:%s;max-height:1px;" % (theme.BORDER, theme.BORDER))


def hbox(*widgets, spacing: int = 8, stretch_last: bool = False, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w == "stretch":
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for w in widgets:
        if w == "stretch":
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w
