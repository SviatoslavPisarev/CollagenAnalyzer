"""Look of the app: a day and a night theme (Fusion style + palette + style sheet), same on Mac and Windows.

The theme can be switched while the app runs (sidebar → Night / Day); the choice is remembered.
UI colours below are module globals that change with the theme; read them when you draw (style.TEXT),
not once at import. The plot colours (PLOT_*) are fixed: figures are always drawn on white "paper"
so that what you see is what you save.
"""
import os
import string
import tempfile

from PySide6.QtCore import QByteArray, QRectF, QSettings, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------- fixed colours for figures (white background)
PLOT_BEFORE, PLOT_AFTER, PLOT_SHAM, PLOT_AMF_ONLY = "#3b6fb6", "#d9622b", "#7f8c8d", "#8e44ad"
PLOT_ACCENT = "#127386"
BEFORE, AFTER, SHAM = PLOT_BEFORE, PLOT_AFTER, PLOT_SHAM          # names used by the figure code

THEMES = {
    "day": dict(
        ACCENT="#127386", ACCENT_HOVER="#0c5463", ACCENT_SOFT="#e3f1f4", ACCENT_TEXT="#0c5463", ON_ACCENT="#ffffff",
        ACCENT_DISABLED="#9cbfc7",
        BG="#f5f7f8", CARD="#ffffff", SUNKEN="#f1f5f7", BORDER="#e1e6ea", INPUT_BORDER="#c5d0d8",
        TEXT="#17212b", TEXT2="#33404c", MUTED="#5e6b78", FAINT="#a3adb6", DISABLED="#a0a8b0", DISABLED_BG="#f0f2f4",
        SIDEBAR="#ffffff", NAV_TEXT="#33404c", NAV_HOVER="#f1f5f7", NAV_SEL_BG="#e3f1f4", NAV_SEL_TEXT="#0c5463",
        NAV_HEAD="#5e6b78", HEADER="#f6f8f9", ALT_ROW="#f9fbfb", GRID="#edf0f3", SEG_BG="#eef2f4",
        WARN="#7a5200", WARN_BG="#fff8e6", WARN_BORDER="#f0d9a0", OK="#2e7d32", OK_BG="#e9f5ec", OK_BORDER="#b9dcc0",
        ERROR="#b3261e", ERROR_BG="#fbeaea",
        C_BEFORE="#2f64b0", C_AFTER="#b4511c", C_SHAM="#5e6b78", C_AMF_ONLY="#7b3fa0",
        BEFORE_BG="#e6eef9", BEFORE_FG="#1f4e8f", AFTER_BG="#fcebe1", AFTER_FG="#9a3f10",
        SHAM_BG="#eceff1", SHAM_FG="#4a5560", AMFONLY_BG="#f1e8f7", AMFONLY_FG="#5f2d80",
        EDITED_BG="#fff3c4", GEL_BAND="#f7fafb",
        LOG_BG="#0f1d23", LOG_TEXT="#d6e2e6", TIP_BG="#17323d", TIP_TEXT="#ffffff", SCROLL="#c5d0d8",
        PAPER_BORDER="#e1e6ea"),
    "night": dict(
        ACCENT="#3fb0c3", ACCENT_HOVER="#5cc6d6", ACCENT_SOFT="#173a41", ACCENT_TEXT="#8ad9e5", ON_ACCENT="#0b1f24",
        ACCENT_DISABLED="#2a5a63",
        BG="#121619", CARD="#1a1f24", SUNKEN="#20262c", BORDER="#2b333a", INPUT_BORDER="#3a444d",
        TEXT="#e6eaed", TEXT2="#c8d0d6", MUTED="#9aa5af", FAINT="#5f6b75", DISABLED="#5f6b75", DISABLED_BG="#1d2227",
        SIDEBAR="#161a1e", NAV_TEXT="#c8d0d6", NAV_HOVER="#1f252b", NAV_SEL_BG="#173a41", NAV_SEL_TEXT="#8ad9e5",
        NAV_HEAD="#9aa5af", HEADER="#20262c", ALT_ROW="#1d2227", GRID="#252c32", SEG_BG="#20262c",
        WARN="#f2c46b", WARN_BG="#2b2416", WARN_BORDER="#5a4a22", OK="#8fd19e", OK_BG="#1a2e20", OK_BORDER="#2f5a3a",
        ERROR="#ff8a80", ERROR_BG="#3a1f1f",
        C_BEFORE="#9cc0f2", C_AFTER="#f6b48a", C_SHAM="#b4bec7", C_AMF_ONLY="#cfb0ef",
        BEFORE_BG="#1d2c44", BEFORE_FG="#9cc0f2", AFTER_BG="#3d2617", AFTER_FG="#f6b48a",
        SHAM_BG="#262c32", SHAM_FG="#c0c8cf", AMFONLY_BG="#2e2240", AMFONLY_FG="#cfb0ef",
        EDITED_BG="#4a3d12", GEL_BAND="#1d2328",
        LOG_BG="#0c0f11", LOG_TEXT="#d6e2e6", TIP_BG="#e6eaed", TIP_TEXT="#121619", SCROLL="#3a444d",
        PAPER_BORDER="#2b333a"),
}
MODE = "day"
globals().update(THEMES["day"])
_listeners = []

# ---------------------------------------------------------------- icons (24 px grid, stroked with the given colour)
ICONS = {
    "logo": '<path d="M5 19 L11 6"/><path d="M9 20 L19 7"/><path d="M14 21 L21 11"/>',
    "folder": '<path d="M3 6.5h6l2 2h10v10H3z"/>',
    "chart": '<path d="M5 20v-7M12 20V5M19 20v-10"/>',
    "grid": '<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/>'
            '<rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
    "target": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    "shield": '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>',
    "brush": '<path d="M4 20l10-10 3 3-10 10H4z"/><path d="M13 6l5 5"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.3a2.6 2.6 0 1 1 3.6 2.4c-.8.3-1.1.9-1.1 1.6M12 16.6v.3"/>',
    "moon": '<path d="M19 14.5A7.5 7.5 0 1 1 9.5 5a6 6 0 0 0 9.5 9.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4'
           'M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "lines": '<path d="M4 18l5-6 4 3 7-9"/>',
    "bars": '<path d="M5 20V9M10 20V4M15 20v-8M20 20v-5"/>',
    "delta": '<path d="M12 4l8 15H4z"/>',
    "groups": '<path d="M6 20v-9M12 20V6M18 20v-4"/><circle cx="6" cy="8" r="1.5"/><circle cx="18" cy="13" r="1.5"/>',
    "scatter": '<circle cx="7" cy="16" r="1.6"/><circle cx="11" cy="11" r="1.6"/><circle cx="16" cy="13" r="1.6"/>'
               '<circle cx="18" cy="6" r="1.6"/><path d="M4 4v16h16"/>',
    "dist": '<path d="M3 19c3 0 4-12 9-12s6 12 9 12"/>',
    "expand": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    "shrink": '<path d="M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5"/>',
    "save": '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5"/><path d="M5 19h14"/>',
    "copy": '<rect x="8" y="8" width="11" height="12" rx="1.5"/><path d="M5 16V5.5A1.5 1.5 0 0 1 6.5 4H15"/>',
}


def svg(name, color, size=24, width=1.7):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


def icon(name, color=None, size=20):
    """QIcon drawn from the line icons above (sharp on high-DPI screens)."""
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(QByteArray(svg(name, color or NAV_TEXT).encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    r.render(p, QRectF(0, 0, size * 2, size * 2))
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def _arrow_files(color, check_color="#ffffff"):
    """Small images for combo boxes, spin boxes and check boxes (style sheets need files)."""
    d = os.path.join(tempfile.gettempdir(), "collagen_analyzer_ui")
    os.makedirs(d, exist_ok=True)
    out = {}
    f = os.path.join(d, f"check_{check_color.lstrip('#')}.svg")
    if not os.path.exists(f):
        with open(f, "w") as fh:
            fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" '
                     f'stroke="{check_color}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">'
                     f'<path d="M5 12.5l4.5 4.5L19 7.5"/></svg>')
    out["check"] = f.replace("\\", "/")
    f = os.path.join(d, f"dot_{check_color.lstrip('#')}.svg")
    if not os.path.exists(f):
        with open(f, "w") as fh:
            fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24">'
                     f'<circle cx="12" cy="12" r="5.5" fill="{check_color}"/></svg>')
    out["dot"] = f.replace("\\", "/")
    for k, path in (("down", "M6 9l6 6 6-6"), ("up", "M6 15l6-6 6 6")):
        f = os.path.join(d, f"{k}_{color.lstrip('#')}.svg")
        if not os.path.exists(f):
            with open(f, "w") as fh:
                fh.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" '
                         f'fill="none" stroke="{color}" stroke-width="2.6" stroke-linecap="round" '
                         f'stroke-linejoin="round"><path d="{path}"/></svg>')
        out[k] = f.replace("\\", "/")
    return out


QSS = string.Template("""
* { color: $TEXT; }
QMainWindow, QWidget#page, QStackedWidget { background: $BG; }
QWidget#sidebar { background: $SIDEBAR; border-right: 1px solid $BORDER; }
QLabel#logoMark { background: $ACCENT; border-radius: 9px; }
QLabel#appTitle { font-size: 15px; font-weight: 700; }
QLabel#appSub { color: $MUTED; font-size: 11px; }
QListWidget#nav { background: transparent; border: none; outline: 0; padding: 0 10px; }
QListWidget#nav::item { color: $NAV_TEXT; padding: 8px 10px; margin: 1px 0; border-radius: 8px; }
QListWidget#nav::item:hover { background: $NAV_HOVER; }
QListWidget#nav::item:selected { background: $NAV_SEL_BG; color: $NAV_SEL_TEXT; font-weight: 600; }
QLabel#navFoot { color: $MUTED; font-size: 11px; }
QPushButton#themeToggle { background: transparent; border: 1px solid $BORDER; border-radius: 8px; padding: 6px 10px;
    color: $TEXT2; text-align: left; }
QPushButton#themeToggle:hover { background: $NAV_HOVER; }

QLabel#pageKicker { color: $MUTED; font-size: 12px; }
QLabel#pageTitle { font-size: 22px; font-weight: 700; }
QLabel#pageSub { color: $MUTED; font-size: 12px; }
QLabel#muted, QLabel#hint, QLabel#cardSub { color: $MUTED; }
QLabel#hint { font-size: 11px; }
QLabel#cardSub { font-size: 11px; background: transparent; }
QLabel#cardTitle { font-weight: 700; font-size: 14px; }
QLabel#banner { background: $WARN_BG; border: 1px solid $WARN_BORDER; border-radius: 8px; padding: 8px 10px; color: $WARN; }
QLabel#bannerOk { background: $OK_BG; border: 1px solid $OK_BORDER; border-radius: 8px; padding: 8px 10px; color: $OK; }
QFrame#updateBar { background: $OK_BG; border-bottom: 1px solid $OK_BORDER; }
QFrame#updateBar QLabel { color: $OK; background: transparent; }
QLabel#bigNumber { font-size: 24px; font-weight: 700; color: $TEXT; }
QLabel#groupHead { font-weight: 700; color: $ACCENT_TEXT; }

QFrame#card, QGroupBox { background: $CARD; border: 1px solid $BORDER; border-radius: 12px; }
QFrame#sunken { background: $SUNKEN; border: none; border-radius: 10px; }
QFrame#paper { background: #ffffff; border: 1px solid $PAPER_BORDER; border-radius: 12px; }
QGroupBox { margin-top: 14px; padding: 16px 12px 10px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; top: 2px; padding: 0 4px; color: $ACCENT_TEXT; }
QGroupBox QLabel { font-weight: 400; }

QPushButton { background: $CARD; border: 1px solid $INPUT_BORDER; border-radius: 8px; padding: 6px 14px; color: $TEXT; }
QPushButton:hover { border-color: $ACCENT; background: $ACCENT_SOFT; }
QPushButton:pressed { background: $ACCENT_SOFT; }
QPushButton:disabled { color: $DISABLED; background: $DISABLED_BG; border-color: $BORDER; }
QPushButton#primary, QPushButton#big { background: $ACCENT; color: $ON_ACCENT; border: none; font-weight: 600;
    padding: 8px 18px; }
QPushButton#primary:hover, QPushButton#big:hover { background: $ACCENT_HOVER; }
QPushButton#primary:disabled, QPushButton#big:disabled { background: $ACCENT_DISABLED; color: $ON_ACCENT; }
QPushButton#big { font-size: 15px; font-weight: 700; padding: 12px 28px; border-radius: 10px; }
QPushButton#danger { color: $ERROR; }
QPushButton#danger:hover { background: $ERROR_BG; border-color: $ERROR; }
QPushButton#link { border: none; background: transparent; color: $ACCENT_TEXT; padding: 2px 4px; }
QPushButton#link:hover { text-decoration: underline; }
QPushButton#modeCard { text-align: left; padding: 12px 14px; border-radius: 12px; border: 1px solid $BORDER;
    background: $CARD; font-size: 13px; }
QPushButton#modeCard:checked { border: 2px solid $ACCENT; background: $ACCENT_SOFT; }
QPushButton#modeCard:hover { border-color: $ACCENT; }
QFrame#segGroup { background: $SEG_BG; border-radius: 9px; }
QPushButton#seg { border: none; background: transparent; border-radius: 7px; padding: 6px 12px; font-size: 12px;
    color: $TEXT2; }
QPushButton#seg:hover { background: $CARD; }
QPushButton#seg:checked { background: $CARD; color: $ACCENT_TEXT; font-weight: 600; }
QPushButton#chip { border-radius: 13px; padding: 4px 12px; font-size: 12px; background: $CARD; border: 1px solid $BORDER; }
QPushButton#chip:checked { background: $ACCENT_SOFT; border: 1px solid $ACCENT; color: $ACCENT_TEXT; font-weight: 600; }
QPushButton#chipWarn { border-radius: 13px; padding: 4px 12px; font-size: 12px; background: $WARN_BG;
    border: 1px solid $WARN_BORDER; color: $WARN; }
QPushButton#chipWarn:checked { border: 1px solid $WARN; font-weight: 600; }
QPushButton#tile { text-align: left; padding: 9px 10px; border-radius: 10px; border: 1px solid $BORDER;
    background: $CARD; font-size: 12px; }
QPushButton#tile:checked { border: 2px solid $ACCENT; background: $ACCENT_SOFT; color: $ACCENT_TEXT; font-weight: 600; }
QPushButton#step { border-radius: 15px; padding: 5px 14px 5px 6px; font-size: 12px; text-align: left; }
QPushButton#step[state="active"] { background: $ACCENT; color: $ON_ACCENT; border: none; font-weight: 600; }
QPushButton#step[state="done"] { background: $CARD; color: $OK; border: 1px solid $OK_BORDER; }
QPushButton#step[state="wait"] { background: $CARD; color: $TEXT2; border: 1px solid $BORDER; }
QLabel#stepSep { color: $FAINT; }
QLabel#stepChip { border-radius: 12px; padding: 5px 12px; }
QLabel#stepChip[state="wait"] { background: $SUNKEN; color: $MUTED; }
QLabel#stepChip[state="active"] { background: $ACCENT; color: $ON_ACCENT; font-weight: 600; }
QLabel#stepChip[state="done"] { background: $OK_BG; color: $OK; }

QLabel#pill { border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 600; }
QLabel#pill[kind="before"] { background: $BEFORE_BG; color: $BEFORE_FG; }
QLabel#pill[kind="after"] { background: $AFTER_BG; color: $AFTER_FG; }
QLabel#pill[kind="sham"] { background: $SHAM_BG; color: $SHAM_FG; }
QLabel#pill[kind="amfonly"] { background: $AMFONLY_BG; color: $AMFONLY_FG; }
QLabel#pill[kind="warn"] { background: $WARN_BG; color: $WARN; }
QLabel#dot { border-radius: 5px; min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px; }
QLabel#checkOk { color: $OK; font-weight: 700; }
QLabel#checkWarn { color: $WARN; font-weight: 700; }
QLabel#checkTextWarn { color: $WARN; }
QFrame#statCard { background: $CARD; border: 1px solid $BORDER; border-radius: 12px; }
QFrame#statCardWarn { background: $WARN_BG; border: 1px solid $WARN_BORDER; border-radius: 12px; }
QLabel#statLabel { color: $MUTED; font-size: 12px; background: transparent; }
QLabel#statValue { font-size: 22px; font-weight: 700; background: transparent; }
QLabel#statSub { color: $TEXT2; font-size: 12px; background: transparent; }
QLabel#statWarn { color: $WARN; font-size: 12px; background: transparent; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: $CARD; border: 1px solid $INPUT_BORDER; border-radius: 7px; padding: 5px 7px; color: $TEXT;
    selection-background-color: $ACCENT; selection-color: $ON_ACCENT; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: $ACCENT; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background: $DISABLED_BG; color: $DISABLED; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url($ARROW_DOWN); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: $CARD; border: 1px solid $BORDER; selection-background-color: $ACCENT_SOFT;
    selection-color: $TEXT; outline: 0; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url($ARROW_UP); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url($ARROW_DOWN); width: 9px; height: 9px; }

QTableWidget, QTreeWidget, QListWidget#plain, QTableView, QTreeView {
    background: $CARD; border: 1px solid $BORDER; border-radius: 10px; gridline-color: $GRID;
    alternate-background-color: $ALT_ROW; selection-background-color: $ACCENT_SOFT; selection-color: $TEXT; }
QHeaderView { background: $HEADER; border: none; }
QHeaderView::section { background: $HEADER; border: none; border-bottom: 1px solid $BORDER; padding: 7px 6px;
    font-weight: 600; color: $MUTED; }
QTableCornerButton::section { background: $HEADER; border: none; }

QProgressBar { background: $SUNKEN; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }
QProgressBar::chunk { background: $ACCENT; border-radius: 6px; }
QPlainTextEdit#log { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 11px; background: $LOG_BG;
    color: $LOG_TEXT; border-radius: 10px; padding: 8px; border: 1px solid $BORDER; }
QTextBrowser#manual { background: #ffffff; color: #1d2733; border: 1px solid $BORDER; border-radius: 12px;
    padding: 14px 22px; }

QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: $SCROLL; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: $SCROLL; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QTabWidget::pane { border: 1px solid $BORDER; border-radius: 10px; background: $CARD; top: -1px; }
QTabBar::tab { background: $SUNKEN; border: 1px solid $BORDER; padding: 6px 14px; margin-right: 2px; color: $TEXT2;
    border-top-left-radius: 8px; border-top-right-radius: 8px; }
QTabBar::tab:selected { background: $CARD; border-bottom-color: $CARD; font-weight: 600; color: $TEXT; }
QCheckBox, QRadioButton { spacing: 7px; background: transparent; }
QCheckBox::indicator, QTableView::indicator, QTreeView::indicator, QListView::indicator, QGroupBox::indicator {
    width: 15px; height: 15px; border: 1px solid $INPUT_BORDER; border-radius: 4px; background: $CARD; }
QCheckBox::indicator:checked, QTableView::indicator:checked, QTreeView::indicator:checked,
QListView::indicator:checked, QGroupBox::indicator:checked { background: $ACCENT; border-color: $ACCENT; image: url($CHECK); }
QTreeView::indicator:indeterminate { background: $ACCENT_SOFT; border-color: $ACCENT; }
QCheckBox::indicator:disabled { background: $DISABLED_BG; border-color: $BORDER; }
QCheckBox::indicator:checked:disabled { background: $ACCENT_DISABLED; }
QRadioButton::indicator { width: 15px; height: 15px; border: 1px solid $INPUT_BORDER; border-radius: 8px; background: $CARD; }
QRadioButton::indicator:checked { background: $ACCENT; border-color: $ACCENT; image: url($RADIO_DOT); }
QRadioButton::indicator:disabled { background: $DISABLED_BG; border-color: $BORDER; }
QToolTip { background: $TIP_BG; color: $TIP_TEXT; border: none; padding: 6px; border-radius: 6px; }
QMenu { background: $CARD; border: 1px solid $BORDER; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 5px; }
QMenu::item:selected { background: $ACCENT_SOFT; color: $TEXT; }
QSlider::groove:horizontal { height: 6px; background: $BORDER; border-radius: 3px; }
QSlider::handle:horizontal { background: $ACCENT; width: 16px; margin: -6px 0; border-radius: 8px; }
QSplitter::handle { background: transparent; }
QDialog, QMessageBox { background: $BG; }
""")


def qss(t):
    arrows = _arrow_files(t["MUTED"], t["ON_ACCENT"])
    return QSS.substitute(t, ARROW_DOWN=arrows["down"], ARROW_UP=arrows["up"], CHECK=arrows["check"],
                          RADIO_DOT=arrows["dot"])


def load_fonts():
    """Register the bundled Inter font. Returns the family name, or None."""
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    fam = None
    d = os.path.join(base, "assets", "fonts")
    for f in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
        p = os.path.join(d, f)
        if os.path.exists(p):
            i = QFontDatabase.addApplicationFont(p)
            fams = QFontDatabase.applicationFontFamilies(i) if i >= 0 else []
            if fams and fam is None:
                fam = fams[0]
    return fam


def saved_mode():
    m = QSettings().value("ui/theme", "day")
    return m if m in THEMES else "day"


def set_mode(mode, app=None, save=True):
    """Switch the theme now (palette + style sheet) and tell the pages to recolour what they drew."""
    global MODE
    app = app or QApplication.instance()
    MODE = mode if mode in THEMES else "day"
    t = THEMES[MODE]
    globals().update(t)
    pal = QPalette()
    for role, col in [(QPalette.Window, t["BG"]), (QPalette.WindowText, t["TEXT"]), (QPalette.Base, t["CARD"]),
                      (QPalette.AlternateBase, t["ALT_ROW"]), (QPalette.Text, t["TEXT"]), (QPalette.Button, t["CARD"]),
                      (QPalette.ButtonText, t["TEXT"]), (QPalette.Highlight, t["ACCENT"]),
                      (QPalette.HighlightedText, t["ON_ACCENT"]), (QPalette.ToolTipBase, t["TIP_BG"]),
                      (QPalette.ToolTipText, t["TIP_TEXT"]), (QPalette.PlaceholderText, t["FAINT"]),
                      (QPalette.Link, t["ACCENT_TEXT"]), (QPalette.Mid, t["BORDER"]), (QPalette.Midlight, t["BORDER"]),
                      (QPalette.Dark, t["INPUT_BORDER"]), (QPalette.Light, t["CARD"]), (QPalette.Shadow, t["BORDER"])]:
        pal.setColor(role, QColor(col))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(t["DISABLED"]))
    if app is not None:
        app.setPalette(pal)
        app.setStyleSheet(qss(t))
    if save:
        QSettings().setValue("ui/theme", MODE)
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def on_theme_change(fn):
    """Call fn() after every theme switch (to recolour table cells, icons, …)."""
    _listeners.append(fn)


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    fam = load_fonts()
    f = app.font()
    if fam:
        f = QFont(fam)
    f.setPointSize(13 if app.font().pointSize() >= 13 else max(app.font().pointSize(), 10))
    f.setHintingPreference(QFont.PreferNoHinting)
    app.setFont(f)
    set_mode(saved_mode(), app, save=False)


def pill_kind(condition, arm=""):
    """Pill style for a file's condition (and its group)."""
    c = str(condition)
    if c.lower().startswith("after sham"):
        return "sham"
    if c.startswith("After"):
        return "amfonly" if str(arm).startswith("AMF only") else "after"
    return "before"


def arm_color(arm):
    """UI colour (themed) of a group."""
    a = str(arm)
    if a.startswith("NC + AMF"):
        return C_AFTER
    if a.startswith("AMF only"):
        return C_AMF_ONLY
    if a.startswith("sham"):
        return C_SHAM
    return C_BEFORE


# ---------------------------------------------------------------- matplotlib figures on screen
def theme_figure(fig, night=None):
    """Recolour a figure's background, axes, ticks and plain (dark) text for the current theme.
    Coloured lines, bars and coloured text are left alone. night=False gives the paper look used for
    saving figures (white background, dark text) whatever the theme."""
    from matplotlib.colors import to_rgb
    from matplotlib.text import Text
    night = (MODE == "night") if night is None else night
    t = THEMES["night" if night else "day"]
    bg = t["CARD"] if night else "#ffffff"
    fg = t["TEXT"] if night else "#1d2733"
    fg2 = t["MUTED"] if night else "#4a5560"
    edge = t["INPUT_BORDER"] if night else "#33404c"
    grid = t["GRID"] if night else "#b0b8bf"

    def lum(c):
        try:
            r, g, b = to_rgb(c)
        except ValueError:
            return None
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def recolour_face(obj, get, set_):
        c = get()
        L = lum(c)
        if getattr(obj, "_ca_face", False) or (L is not None and L > 0.92):
            set_(bg)
            obj._ca_face = True

    recolour_face(fig, fig.get_facecolor, fig.set_facecolor)
    for ax in fig.axes:
        recolour_face(ax, ax.get_facecolor, ax.set_facecolor)
        ax.tick_params(colors=fg2, which="both", labelcolor=fg)
        for sp in ax.spines.values():
            sp.set_edgecolor(edge)
        for gl in ax.get_xgridlines() + ax.get_ygridlines():
            gl.set_color(grid)
        leg = ax.get_legend()
        if leg is not None:
            leg.get_frame().set_facecolor(bg)
            leg.get_frame().set_edgecolor(t["BORDER"] if night else "#dddddd")
    for txt in fig.findobj(Text):
        L = lum(txt.get_color())
        if getattr(txt, "_ca_text", False) or (L is not None and L < 0.35):
            txt.set_color(fg)
            txt._ca_text = True
