"""Dark, sober colour theme shared by the ENS tools.

The same file exists in both programs (ens_database/gui/theme.py and
spectral_analyzer/gui/style.py) so that each program can be distributed alone.
Every section (group box) and every button is framed.
"""
import os
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QStyleFactory

# Palette (slate blue).
BG = "#161c26"          # window background
SURFACE = "#1f2735"     # panels, group boxes
SURFACE_2 = "#273142"   # inputs, tables, lists
SURFACE_3 = "#2f3b4f"   # buttons
HEADER = "#2c4466"      # section title bands, table headers
BORDER = "#3e4f68"
BORDER_STRONG = "#5a7194"
TEXT = "#e4e9f1"
MUTED = "#9aa7ba"
ACCENT = "#3f87d4"
ACCENT_DARK = "#2f6fb0"
WARN = "#f0b45a"
ERROR = "#ef6f78"

STYLE_SHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:hover {{ background: {BORDER}; }}

/* ---- framed sections with a coloured title band ---- */
QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 7px;
    margin-top: 16px; padding: 12px 8px 7px 8px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 10px;
    padding: 2px 10px; color: #d6e6fb; background: {HEADER};
    border: 1px solid {BORDER_STRONG}; border-radius: 4px;
}}
QFrame#card {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; }}

/* ---- bars ---- */
QMenuBar {{ background: #121821; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {HEADER}; border-radius: 3px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QToolBar {{ background: #1a2230; border-bottom: 1px solid {BORDER}; spacing: 5px; padding: 4px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 3px 4px; }}
QStatusBar {{ background: #121821; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; }}

/* ---- framed buttons ---- */
QPushButton, QToolButton {{
    background: {SURFACE_3}; border: 1px solid {BORDER_STRONG}; border-radius: 5px;
    padding: 4px 11px; color: {TEXT};
}}
QPushButton:hover, QToolButton:hover {{ background: #3a4961; border-color: {ACCENT}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {ACCENT_DARK}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #6b778a; border-color: {BORDER}; background: {SURFACE_2}; }}
QToolButton:checked {{ background: {ACCENT_DARK}; border-color: #7fb2ea; font-weight: 600; }}
QToolButton::menu-indicator {{ subcontrol-position: right center; right: 4px; }}
QPushButton#primary {{ background: {ACCENT_DARK}; border: 1px solid #7fb2ea; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT}; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser, QTextEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 5px;
    selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER_STRONG};
                               selection-background-color: {ACCENT_DARK}; }}
QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}

/* ---- tables and lists ---- */
QListView, QListWidget, QTableView, QTableWidget {{
    background: {SURFACE_2}; alternate-background-color: #2b3546; border: 1px solid {BORDER};
    border-radius: 5px; gridline-color: {BORDER}; selection-background-color: {ACCENT_DARK};
}}
QHeaderView::section {{
    background: {HEADER}; color: #d6e6fb; padding: 4px 6px; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; font-weight: 600;
}}
QTableCornerButton::section {{ background: {HEADER}; border: none; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 5px; top: -1px; background: {SURFACE}; }}
QTabBar::tab {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-bottom: none;
               padding: 6px 16px; margin-right: 3px; border-top-left-radius: 5px;
               border-top-right-radius: 5px; color: {MUTED}; }}
QTabBar::tab:selected {{ background: {HEADER}; color: {TEXT}; border-color: {BORDER_STRONG}; }}

QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QLabel#title {{ font-size: 18px; font-weight: 700; color: #f2f5fa; background: transparent; }}
QLabel#muted {{ color: {MUTED}; background: transparent; }}
"""


INDICATORS = """
QCheckBox::indicator, QTableView::indicator, QListView::indicator, QGroupBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {border}; border-radius: 3px; background: {base};
}}
QCheckBox::indicator:hover, QTableView::indicator:hover, QListView::indicator:hover {{
    border-color: {accent};
}}
QCheckBox::indicator:checked, QTableView::indicator:checked, QListView::indicator:checked {{
    background: {accent_dark}; border-color: {accent_light}; image: url({check});
}}
QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid {border}; border-radius: 8px; background: {base};
}}
QRadioButton::indicator:checked {{ background: {accent_dark}; border-color: {accent_light};
                                   image: url({dot}); }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 16px; background: {button}; border-left: 1px solid {border};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right;
    border-top-right-radius: 4px; border-bottom: 1px solid {border}; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right;
    border-bottom-right-radius: 4px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {accent_dark}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up}); width: 8px; height: 8px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down}); width: 8px; height: 8px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 18px;
    border-left: 1px solid {border}; background: {button};
    border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}
QComboBox::down-arrow {{ image: url({down}); width: 9px; height: 9px; }}
"""


def _icon_files() -> dict:
    """Small PNG glyphs (check mark, dot, arrows) drawn at start-up.

    PNG is used rather than SVG so that no extra Qt image plug-in is needed in the
    frozen executables.
    """
    folder = os.path.join(tempfile.gettempdir(), "ens_tools_theme_v1")
    os.makedirs(folder, exist_ok=True)
    out = {}

    def draw(name, painter_fn, size=28):
        """Draw a small icon with painter_fn and save it as a PNG once (used by the style sheet)."""
        path = os.path.join(folder, name + ".png")
        if not os.path.isfile(path):
            img = QImage(size, size, QImage.Format_ARGB32)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            painter_fn(p, size)
            p.end()
            img.save(path, "PNG")
        out[name] = path.replace("\\", "/")

    def check(p, n):
        """Check mark of the check boxes."""
        p.setPen(QPen(QColor("white"), n * 0.14, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath(QPointF(n * 0.2, n * 0.52))
        path.lineTo(n * 0.42, n * 0.74)
        path.lineTo(n * 0.8, n * 0.28)
        p.drawPath(path)

    def dot(p, n):
        """Dot of the radio buttons."""
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(n / 2, n / 2), n * 0.2, n * 0.2)

    def arrow(up):
        """Return a function that draws an up or down arrow (spin boxes, combo boxes)."""
        def f(p, n):
            """Draw the arrow triangle."""
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TEXT))
            path = QPainterPath()
            if up:
                path.moveTo(n * 0.15, n * 0.7)
                path.lineTo(n * 0.85, n * 0.7)
                path.lineTo(n * 0.5, n * 0.25)
            else:
                path.moveTo(n * 0.15, n * 0.3)
                path.lineTo(n * 0.85, n * 0.3)
                path.lineTo(n * 0.5, n * 0.75)
            path.closeSubpath()
            p.drawPath(path)
        return f

    draw("check", check)
    draw("dot", dot)
    draw("up", arrow(True))
    draw("down", arrow(False))
    return out


def apply_style(app: QApplication) -> None:
    """Apply the dark theme to the whole application: Fusion style, colour palette and style sheet.
    """
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: SURFACE_2,
        QPalette.AlternateBase: "#2b3546", QPalette.Text: TEXT, QPalette.Button: SURFACE_3,
        QPalette.ButtonText: TEXT, QPalette.BrightText: "#ffffff", QPalette.Highlight: ACCENT_DARK,
        QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: SURFACE,
        QPalette.ToolTipText: TEXT, QPalette.PlaceholderText: "#7f8ca0", QPalette.Link: "#7fb2ea",
        QPalette.Light: "#3a4961", QPalette.Midlight: BORDER, QPalette.Mid: BORDER,
        QPalette.Dark: "#10151d", QPalette.Shadow: "#0a0d12",
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#6b778a"))
    app.setPalette(pal)
    try:
        icons = _icon_files()
        extra = INDICATORS.format(border=BORDER_STRONG, base=SURFACE_2, accent=ACCENT,
                                  accent_dark=ACCENT_DARK, accent_light="#7fb2ea",
                                  button=SURFACE_3, **icons)
    except OSError:          # no writable temp folder: keep the default indicators
        extra = ""
    app.setStyleSheet(STYLE_SHEET + extra)


def fit_to_screen(win, width: int, height: int, fraction: float = 0.92) -> None:
    """Resize ``win`` to (width, height), shrunk to fit the available screen area, and centre it.

    Windows displays laptops at 125-150 % scaling, which leaves e.g. 1280 x 680
    usable logical pixels on a 1920 x 1080 screen: a fixed default size would
    put part of the window off-screen.
    """
    from PySide6.QtGui import QGuiApplication
    screen = win.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        win.resize(width, height)
        return
    avail = screen.availableGeometry()
    w = min(width, int(avail.width() * fraction))
    h = min(height, int(avail.height() * fraction))
    win.resize(w, h)
    win.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)
