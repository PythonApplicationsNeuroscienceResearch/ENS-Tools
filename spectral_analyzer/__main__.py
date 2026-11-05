"""Entry point: ``python -m spectral_analyzer [stack.tif ...]``."""
import os
import sys


def main() -> int:
    """Start the Qt application and open the Spectral Analyzer main window (optionally with stacks
    given on the command line).
    """
    os.environ.setdefault("QT_API", "pyside6")   # matplotlib Qt binding
    from PySide6.QtWidgets import QApplication

    from .gui.style import apply_style
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Spectral Analyzer")
    app.setOrganizationName("SPPIN")
    apply_style(app)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon = os.path.join(base, "assets", "spectral_analyzer.png")
    if os.path.isfile(icon):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon))
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    win = MainWindow(files)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
