"""Drive the GUI headless (offscreen) against the simulator, exercise every page and save
screenshots to tests/screenshots. Exits non-zero on any uncaught exception."""
import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pmacdoctor.ui import theme  # noqa: E402
from pmacdoctor.ui.context import AppContext  # noqa: E402
from pmacdoctor.ui.main_window import MainWindow  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(OUT, exist_ok=True)
errors = []


def excepthook(t, v, tb):
    errors.append("".join(traceback.format_exception(t, v, tb)))
    print("EXCEPTION:", v)


sys.excepthook = excepthook


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    theme.setup_pyqtgraph()
    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(1480, 920)
    win.show()
    scenario = sys.argv[1] if len(sys.argv) > 1 else "healthy"
    ctx.session.connect_direct("simulator", scenario=scenario)
    ctx.connection_changed.emit()
    ctx.start_polling(300)
    # make something happen on the simulator
    link = ctx.session.direct
    link.send("#1j/ #2j/ #3j/")
    link.send("#1j:2000")
    if scenario == "healthy":
        link.send("sim.scenario=i2t_trip")
        link.send("#2j+")

    steps = []

    def shot(name):
        def _f():
            win.grab().save(os.path.join(OUT, name + ".png"))
            print("saved", name)
        return _f

    def page(key):
        return lambda: win.show_page(key)

    steps.append((1500, page("dashboard")))
    steps.append((400, shot("01_dashboard")))
    steps.append((100, page("axes")))
    steps.append((2500, shot("02_axes")))
    steps.append((100, page("variables")))
    steps.append((100, lambda: win.pages["variables"].refresh()))
    steps.append((1500, shot("03_variables")))
    steps.append((100, page("diagnostics")))
    steps.append((100, lambda: win.pages["diagnostics"].run()))
    steps.append((3000, shot("04_diagnostics")))
    steps.append((100, page("tuning")))
    steps.append((100, lambda: ctx.settings.setValue("confirm_moves", "false")))
    steps.append((100, lambda: win.pages["tuning"].capture_step()))
    steps.append((3000, shot("05_tuning_step")))
    steps.append((100, lambda: win.pages["tuning"].tabs.setCurrentIndex(1)))
    steps.append((100, lambda: win.pages["tuning"].run_open_loop()))
    steps.append((2500, shot("06_tuning_openloop")))
    steps.append((100, lambda: win.pages["tuning"].tabs.setCurrentIndex(2)))
    steps.append((100, lambda: win.pages["tuning"].at_ack.setChecked(True)))
    steps.append((100, lambda: win.pages["tuning"].run_autotune()))
    steps.append((9000, shot("07_tuning_autotune")))
    steps.append((100, lambda: win.pages["tuning"].tabs.setCurrentIndex(3)))
    steps.append((100, lambda: win.pages["tuning"].calc_current()))
    steps.append((300, shot("08_tuning_current")))
    steps.append((100, page("simulator")))
    steps.append((100, lambda: win.pages["simulator"].preset.setCurrentIndex(3)))
    steps.append((100, lambda: win.pages["simulator"]._load_preset()))
    steps.append((100, lambda: win.pages["simulator"]._apply()))
    steps.append((800, shot("11_simulator")))
    scope = win.pages["scope"]
    steps.append((100, page("scope")))
    steps.append((300, lambda: scope._start_moving()))
    steps.append((3000, shot("12_scope")))
    # crank the proportional gain up and watch the overshoot appear
    steps.append((100, lambda: scope._gain_widgets["Kp"][1].setValue(scope._baseline.get("Kp", 10) * 5)))
    steps.append((3000, shot("13_scope_overshoot")))
    steps.append((100, lambda: scope._restore_baseline()))
    # now make the fatal limit impossibly tight and watch the axis trip
    steps.append((1500, lambda: scope.sp_fatal.setValue(3.0)))
    steps.append((2000, shot("14_scope_fatal")))
    steps.append((100, lambda: scope.sp_fatal.setValue(200.0)))
    steps.append((100, lambda: scope._re_enable()))
    steps.append((100, lambda: scope._stop_moving()))
    steps.append((100, page("terminal")))
    steps.append((100, lambda: win.pages["terminal"].send("Motor[1].Servo.Kp Motor[1].Servo.Kvfb #1? ?")))
    steps.append((300, shot("09_terminal")))
    steps.append((100, page("settings")))
    steps.append((300, shot("10_settings")))
    steps.append((100, lambda: ctx.settings.setValue("confirm_moves", "true")))
    steps.append((200, app.quit))

    # run the steps strictly in sequence (Windows timers are not ordered when the loop is busy)
    state = {"i": 0}

    def run_next():
        if state["i"] >= len(steps):
            return
        d, fn = steps[state["i"]]
        state["i"] += 1

        def wrap():
            try:
                fn()
            except Exception:  # noqa: BLE001
                errors.append(traceback.format_exc())
                print("STEP ERROR:", traceback.format_exc())
            run_next()
        QTimer.singleShot(d, wrap)

    run_next()
    QTimer.singleShot(sum(d for d, _ in steps) + 30000, app.quit)  # safety
    app.exec()
    win.close()
    if errors:
        print("\n%d error(s)" % len(errors))
        for e in errors:
            print(e)
        return 1
    print("UI smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
