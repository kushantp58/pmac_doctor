"""Dark theme: colours, Qt style sheet and pyqtgraph defaults.

Palette follows the validated dark-mode data-viz palette (chart surface #1a1a19, page
#0d0d0d, ink #f0efec) with the fixed status colours good/warning/serious/critical.
"""
from __future__ import annotations

# surfaces & ink
PAGE = "#0d0d0d"
SURFACE = "#1a1a19"
SURFACE_2 = "#232322"
SURFACE_3 = "#2c2c2a"
BORDER = "#383835"
GRID = "#2c2c2a"
INK = "#f0efec"
INK_2 = "#c3c2b7"
MUTED = "#898781"

# categorical series (dark mode steps)
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
ACCENT = SERIES[0]

# status (fixed)
GOOD = "#0ca30c"
WARNING = "#fab219"
SERIOUS = "#ec835a"
CRITICAL = "#d03b3b"
NEUTRAL = "#898781"

SEVERITY_COLOR = {"ok": GOOD, "info": ACCENT, "warning": WARNING, "fault": CRITICAL, "serious": SERIOUS,
                  "neutral": NEUTRAL}
SEVERITY_LABEL = {"ok": "OK", "info": "Info", "warning": "Warning", "fault": "Fault"}
SEVERITY_ICON = {"ok": "✓", "info": "i", "warning": "!", "fault": "✖"}

FONT = 'Segoe UI, "Segoe UI Variable", system-ui, sans-serif'
MONO = 'Cascadia Mono, Consolas, "Courier New", monospace'

QSS = f"""
* {{ font-family: {FONT}; font-size: 10pt; color: {INK}; }}
QMainWindow, QDialog, QWidget#page {{ background: {PAGE}; }}
QWidget#sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QToolTip {{ background: {SURFACE_3}; color: {INK}; border: 1px solid {BORDER}; padding: 6px; }}

QPushButton {{
  background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 14px; color: {INK};
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {MUTED}; }}
QPushButton:pressed {{ background: {PAGE}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {SURFACE_3}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: #4a93ea; }}
QPushButton[kind="danger"] {{ background: {SURFACE_2}; border-color: {CRITICAL}; color: #ff9a9a; }}
QPushButton[kind="danger"]:hover {{ background: #3a1d1d; }}
QPushButton[kind="nav"] {{
  background: transparent; border: none; border-radius: 8px; text-align: left; padding: 10px 14px; font-size: 10.5pt;
  color: {INK_2};
}}
QPushButton[kind="nav"]:hover {{ background: {SURFACE_2}; color: {INK}; }}
QPushButton[kind="nav"]:checked {{ background: {SURFACE_3}; color: {INK}; font-weight: 600;
  border-left: 3px solid {ACCENT}; }}
QPushButton[kind="chip"] {{ border-radius: 12px; padding: 3px 10px; font-size: 9pt; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
  background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; color: {INK};
  selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
  border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_3}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; border: none; }}

QTableView, QTreeView, QListView {{
  background: {SURFACE}; alternate-background-color: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
  gridline-color: {GRID}; selection-background-color: #28446e; selection-color: {INK}; outline: 0;
}}
QHeaderView::section {{
  background: {SURFACE_2}; color: {INK_2}; border: none; border-bottom: 1px solid {BORDER}; border-right: 1px solid {GRID};
  padding: 6px 8px; font-weight: 600;
}}
QTableView::item, QTreeView::item {{ padding: 3px 6px; }}
QTreeView::branch {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QFrame[kind="card"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame[kind="card"][tone="fault"] {{ border-color: {CRITICAL}; }}
QFrame[kind="card"][tone="warning"] {{ border-color: {WARNING}; }}
QFrame[kind="card"][tone="ok"] {{ border-color: {BORDER}; }}
QFrame[kind="card"][selected="true"] {{ border: 2px solid {ACCENT}; }}
QLabel[role="title"] {{ font-size: 15pt; font-weight: 600; color: {INK}; }}
QLabel[role="subtitle"] {{ font-size: 10pt; color: {INK_2}; }}
QLabel[role="cardtitle"] {{ font-size: 9pt; font-weight: 600; color: {MUTED}; letter-spacing: 1px; }}
QLabel[role="kpi"] {{ font-size: 20pt; font-weight: 600; color: {INK}; }}
QLabel[role="kpi-small"] {{ font-size: 14pt; font-weight: 600; color: {INK}; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="mono"] {{ font-family: {MONO}; }}
QLabel[role="badge"] {{ border-radius: 9px; padding: 2px 8px; font-size: 8.5pt; font-weight: 700; color: white; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 12px; padding-top: 8px; color: {INK_2}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-bottom: none; padding: 6px 14px;
  border-top-left-radius: 6px; border-top-right-radius: 6px; color: {INK_2}; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {INK}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {MUTED}; border-radius: 4px; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QProgressBar {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 5px; text-align: center; height: 12px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {INK_2}; }}
QSplitter::handle {{ background: {BORDER}; width: 1px; height: 1px; }}
QMenuBar {{ background: {SURFACE}; }}
QMenuBar::item:selected {{ background: {SURFACE_3}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; background: {ACCENT}; border-radius: 7px; }}
"""


def setup_pyqtgraph():
    import pyqtgraph as pg
    pg.setConfigOptions(antialias=True, background=SURFACE, foreground=INK_2)


def style_plot(plot, title: str = "", xlabel: str = "", ylabel: str = ""):
    """Apply recessive grid/axes to a pyqtgraph PlotItem."""
    import pyqtgraph as pg
    plot.showGrid(x=True, y=True, alpha=0.18)
    for ax in ("left", "bottom"):
        a = plot.getAxis(ax)
        a.setPen(pg.mkPen(BORDER))
        a.setTextPen(pg.mkPen(MUTED))
    plot.getAxis("right").hide() if hasattr(plot.getAxis("right"), "hide") else None
    plot.getAxis("top").hide() if hasattr(plot.getAxis("top"), "hide") else None
    if title:
        plot.setTitle(title, color=INK_2, size="10pt")
    if xlabel:
        plot.setLabel("bottom", xlabel, color=MUTED)
    if ylabel:
        plot.setLabel("left", ylabel, color=MUTED)
