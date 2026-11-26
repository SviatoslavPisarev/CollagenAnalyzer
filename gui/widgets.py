"""Small reusable widgets."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget, QPushButton,
                               QSizePolicy)


class Card(QFrame):
    """White rounded panel with an optional title."""

    def __init__(self, title=None, subtitle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)
        if title:
            t = QLabel(title)
            t.setStyleSheet("font-weight: 700; font-size: 14px;")
            self.lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("hint")
            s.setWordWrap(True)
            self.lay.addWidget(s)


class Page(QWidget):
    """Page with a title, a subtitle and a scrollable body."""

    def __init__(self, title, subtitle="", scroll=True, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 20, 26, 16)
        outer.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("pageTitle")
        outer.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("pageSub")
            s.setWordWrap(True)
            outer.addWidget(s)
        outer.addSpacing(8)
        self.body = QWidget()
        self.body.setObjectName("page")
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 6, 0)
        self.lay.setSpacing(12)
        if scroll:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            sa.setWidget(self.body)
            outer.addWidget(sa, 1)
        else:
            outer.addWidget(self.body, 1)
        self.footer = QHBoxLayout()
        outer.addLayout(self.footer)


def hint(text):
    l = QLabel(text)
    l.setObjectName("hint")
    l.setWordWrap(True)
    l.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return l


def banner(text, ok=False):
    l = QLabel(text)
    l.setObjectName("bannerOk" if ok else "banner")
    l.setWordWrap(True)
    return l


def hbox(*widgets, stretch_end=True, spacing=8):
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 0, 0, 0)
    l.setSpacing(spacing)
    for x in widgets:
        if x == "stretch":
            l.addStretch(1)
        elif isinstance(x, int):
            l.addSpacing(x)
        else:
            l.addWidget(x)
    if stretch_end:
        l.addStretch(1)
    return w


def button(text, kind=None, tip=None, slot=None):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class ModeCard(QPushButton):
    """Large checkable button with a title line and a description line."""

    def __init__(self, title, text, parent=None):
        super().__init__(parent)
        self.setObjectName("modeCard")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        a = QLabel(title)
        a.setStyleSheet("font-weight: 700; font-size: 14px; background: transparent;")
        b = QLabel(text)
        b.setStyleSheet("color: #6b7785; font-size: 11px; background: transparent;")
        b.setWordWrap(True)
        for w in (a, b):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)


class StepChip(QLabel):
    """Phase indicator: waiting / active / done."""
    STYLES = {
        "wait": "background:#e9eef2; color:#6b7785; border-radius:12px; padding:5px 12px;",
        "active": "background:#1f7a8c; color:white; border-radius:12px; padding:5px 12px; font-weight:600;",
        "done": "background:#e9f5ec; color:#2e7d32; border-radius:12px; padding:5px 12px;",
    }

    def __init__(self, text):
        super().__init__(text)
        self.base = text
        self.set_state("wait")

    def set_state(self, st):
        self.setStyleSheet(self.STYLES[st])
        self.setText(("✓ " if st == "done" else "") + self.base)
