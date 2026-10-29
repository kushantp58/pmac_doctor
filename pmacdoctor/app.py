"""Application entry point."""
from __future__ import annotations

import os
import sys
import warnings


def main(argv=None) -> int:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=".*Python 3.8.*")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPalette, QColor
    from PySide6.QtWidgets import QApplication

    from . import __app_name__, __org__
    from .ui import theme
    from .ui.context import AppContext
    from .ui.main_window import MainWindow

    app = QApplication([sys.argv[0]])
    app.setApplicationName(__app_name__)
    app.setOrganizationName(__org__)
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(theme.PAGE))
    pal.setColor(QPalette.WindowText, QColor(theme.INK))
    pal.setColor(QPalette.Base, QColor(theme.SURFACE_2))
    pal.setColor(QPalette.AlternateBase, QColor(theme.SURFACE))
    pal.setColor(QPalette.Text, QColor(theme.INK))
    pal.setColor(QPalette.Button, QColor(theme.SURFACE_2))
    pal.setColor(QPalette.ButtonText, QColor(theme.INK))
    pal.setColor(QPalette.Highlight, QColor(theme.ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor(theme.SURFACE_3))
    pal.setColor(QPalette.ToolTipText, QColor(theme.INK))
    pal.setColor(QPalette.PlaceholderText, QColor(theme.MUTED))
    app.setPalette(pal)
    app.setStyleSheet(theme.QSS)
    theme.setup_pyqtgraph()
    icon_path = os.path.join(os.path.dirname(__file__), "ui", "resources", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NSLS2.PmacDoctor")
        except Exception:
            pass

    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()

    # command line conveniences:  --simulate [scenario]   --epics <prefix>   --ssh <host>
    args = list(argv if argv is not None else sys.argv[1:])
    settings_page = win.pages["settings"]
    if "--simulate" in args:
        i = args.index("--simulate")
        scen = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else "healthy"
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: settings_page.connect_simulator(scen))
    if "--epics" in args:
        i = args.index("--epics")
        if i + 1 < len(args):
            settings_page.ep_prefix.setText(args[i + 1])
            from PySide6.QtCore import QTimer
            QTimer.singleShot(600, settings_page.connect_epics)
    if "--ssh" in args:
        i = args.index("--ssh")
        if i + 1 < len(args):
            from PySide6.QtCore import QTimer
            settings_page.d_host.setText(args[i + 1])
            settings_page.d_kind.setCurrentIndex(settings_page.d_kind.findData("ssh"))
            QTimer.singleShot(900, settings_page.connect_direct)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
