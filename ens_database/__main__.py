"""Entry point: ``python -m ens_database``."""
import os
import sys


def main() -> int:
    """Start the Qt application and open the main window of the ENS Image Database."""
    from PySide6.QtWidgets import QApplication

    from .gui.common import apply_style
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ENS Image Database")
    app.setOrganizationName("SPPIN")
    apply_style(app)
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    icon = os.path.join(base, "assets", "ens_database.png")
    if os.path.isfile(icon):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
