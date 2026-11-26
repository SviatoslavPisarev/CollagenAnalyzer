"""Look of the app: one light theme (Fusion style + palette + style sheet), same on Mac and Windows."""
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#1f7a8c"        # teal
ACCENT_DARK = "#155e6c"
ACCENT_LIGHT = "#e3f1f4"
BG = "#f4f6f8"
CARD = "#ffffff"
BORDER = "#dde3e8"
TEXT = "#1d2733"
MUTED = "#6b7785"
WARN = "#b36b00"
ERROR = "#b3261e"
OK = "#2e7d32"
BEFORE = "#3b6fb6"
AFTER = "#d9622b"

QSS = f"""
* {{ color: {TEXT}; }}
QMainWindow, QWidget#page {{ background: {BG}; }}
QWidget#sidebar {{ background: #17323d; }}
QLabel#appTitle {{ color: white; font-size: 17px; font-weight: 700; padding: 18px 16px 2px 18px; }}
QLabel#appSub {{ color: #9fc3cc; font-size: 11px; padding: 0 16px 14px 18px; }}
QListWidget#nav {{ background: transparent; border: none; outline: 0; padding: 4px 8px; }}
QListWidget#nav::item {{ color: #d6e6ea; padding: 11px 12px; margin: 2px 0; border-radius: 8px; }}
QListWidget#nav::item:hover {{ background: #21434f; }}
QListWidget#nav::item:selected {{ background: {ACCENT}; color: white; font-weight: 600; }}
QLabel#navFoot {{ color: #7fa3ad; font-size: 10px; padding: 10px 18px; }}

QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#pageSub {{ color: {MUTED}; font-size: 12px; }}
QLabel.muted, QLabel#muted {{ color: {MUTED}; }}
QLabel#hint {{ color: {MUTED}; font-size: 11px; }}
QLabel#banner {{ background: #fff6e5; border: 1px solid #f3d19c; border-radius: 8px; padding: 8px 10px; color: #6b4a00; }}
QLabel#bannerOk {{ background: #e9f5ec; border: 1px solid #b9dcc0; border-radius: 8px; padding: 8px 10px; color: #1f5a28; }}
QLabel#bigNumber {{ font-size: 26px; font-weight: 700; color: {ACCENT_DARK}; }}

QFrame#card, QGroupBox {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QGroupBox {{ margin-top: 14px; padding: 14px 12px 10px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; top: 2px; padding: 0 4px; color: {ACCENT_DARK}; }}
QGroupBox QLabel {{ font-weight: 400; }}

QPushButton {{ background: {CARD}; border: 1px solid #c9d2da; border-radius: 7px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: {ACCENT_LIGHT}; }}
QPushButton:pressed {{ background: #cfe6eb; }}
QPushButton:disabled {{ color: #a0a8b0; background: #f0f2f4; border-color: #e0e4e8; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; padding: 8px 18px; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#primary:disabled {{ background: #9cbfc7; color: #eef5f7; }}
QPushButton#big {{ background: {ACCENT}; color: white; border: none; font-size: 15px; font-weight: 700;
                   padding: 12px 28px; border-radius: 9px; }}
QPushButton#big:hover {{ background: {ACCENT_DARK}; }}
QPushButton#big:disabled {{ background: #9cbfc7; }}
QPushButton#danger {{ color: {ERROR}; }}
QPushButton#danger:hover {{ background: #fbeaea; border-color: {ERROR}; }}
QPushButton#link {{ border: none; background: transparent; color: {ACCENT}; padding: 2px 4px; }}
QPushButton#link:hover {{ text-decoration: underline; }}
QPushButton#modeCard {{ text-align: left; padding: 12px 14px; border-radius: 10px; border: 2px solid {BORDER};
                        background: {CARD}; font-size: 13px; }}
QPushButton#modeCard:checked {{ border-color: {ACCENT}; background: {ACCENT_LIGHT}; }}
QPushButton#modeCard:hover {{ border-color: #8fc1cb; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: white; border: 1px solid #c9d2da; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT}; selection-color: white; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background: #f0f2f4; color: #9aa3ab; }}
QComboBox QAbstractItemView {{ background: white; selection-background-color: {ACCENT_LIGHT}; selection-color: {TEXT}; }}

QTableWidget, QTreeWidget, QListWidget#plain {{ background: white; border: 1px solid {BORDER}; border-radius: 8px;
    gridline-color: #edf0f3; alternate-background-color: #f8fafb; selection-background-color: {ACCENT_LIGHT};
    selection-color: {TEXT}; }}
QHeaderView::section {{ background: #eef2f5; border: none; border-bottom: 1px solid {BORDER}; padding: 6px 6px;
    font-weight: 600; color: #3d4a57; }}

QProgressBar {{ background: #e5eaee; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

QPlainTextEdit#log {{ font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 11px; background: #0f1d23;
    color: #d6e2e6; border-radius: 8px; padding: 8px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; background: {CARD}; top: -1px; }}
QTabBar::tab {{ background: #e9eef2; border: 1px solid {BORDER}; padding: 6px 14px; margin-right: 2px;
    border-top-left-radius: 7px; border-top-right-radius: 7px; }}
QTabBar::tab:selected {{ background: {CARD}; border-bottom-color: {CARD}; font-weight: 600; }}
QCheckBox, QRadioButton {{ spacing: 7px; }}
QToolTip {{ background: #17323d; color: white; border: none; padding: 6px; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 6px; background: #dde3e8; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QSplitter::handle {{ background: transparent; }}
"""


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in [(QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, "#ffffff"),
                      (QPalette.AlternateBase, "#f8fafb"), (QPalette.Text, TEXT), (QPalette.Button, CARD),
                      (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
                      (QPalette.ToolTipBase, "#17323d"), (QPalette.ToolTipText, "#ffffff"),
                      (QPalette.PlaceholderText, "#9aa3ab"), (QPalette.Link, ACCENT)]:
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#9aa3ab"))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#9aa3ab"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#9aa3ab"))
    app.setPalette(pal)
    f = app.font()
    f.setPointSize(13 if f.pointSize() >= 13 else max(f.pointSize(), 10))
    app.setFont(f)
    app.setStyleSheet(QSS)
