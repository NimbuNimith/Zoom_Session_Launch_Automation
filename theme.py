"""
theme.py
---------
Design system for the PySide6 rebuild. One place for every color, font, and
icon — nothing below is a Qt default; every value here is a deliberate
choice, explained below.

DESIGN RATIONALE
-----------------
Subject: this is a broadcast/mission-control style tool — a session
manager watches live Zoom/Prism sessions the way a broadcast engineer
watches a router or a NOC watches a fleet. That vernacular (tally lights,
rack-mount labeling, hairline dividers between channels) is where the
choices below come from, not a generic "dark mode SaaS" template.

What carried over from the old Tkinter app: red as the brand/alert color —
that's your existing identity (Zoom red, "Command Center", both reference
screenshots use it), and changing it would throw away brand continuity
for no reason. It stays the same red in BOTH themes below.

Signature element: a painted tally dot (not just colored text) in front
of every status word — in the grid's Status/Prism Sync columns, the
header's live-session counter, and every Event Log line. Three states,
same as an actual broadcast tally light: red = live/error, green =
synced/good, amber = pending/warning. Implemented as a real
QStyledItemDelegate paint job, not a font color.

Type: Segoe UI for interface text, Cascadia Mono for anything data-dense
(timestamps, session IDs, meeting codes, the event log). Fallbacks are
listed for macOS/Linux dev machines.

LIGHT / DARK THEME SUPPORT
----------------------------
Two full palettes below (_DARK / _LIGHT). The module-level names
(BG, SURFACE, INK, ...) are deliberately mutable, reassigned in bulk by
set_theme() — every place in this codebase that reads them does so via
either a bare name INSIDE this module (TallyDotDelegate.paint(),
_build_stylesheet()) or `theme.XXX` attribute access from other modules
(main_window.py's pattern throughout). Both forms re-read the current
value on every access in Python — that's what makes a live toggle
possible without restarting the app. The one thing that does NOT stay
live automatically is anything captured via `from theme import XXX` in
another module (that copies the value at import time) — models.py's
dot-color logic was updated to use `theme.QC_RED` etc. for exactly this
reason. Baked artifacts like QIcon pixmaps (rendered once, not
re-rendered on palette change) also need an explicit rebuild on toggle —
see main_window.py's _apply_theme().
"""

from PySide6.QtCore import Qt, QRectF, QByteArray
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter, QFont, QFontDatabase, QGuiApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QStyledItemDelegate, QStyle

# ── Palettes ─────────────────────────────────────────────────────────────
# Real monitoring tools (Grafana, Datadog, a Bloomberg terminal) reserve
# color strictly for MEANING — status, alerts — and keep the canvas
# itself neutral. That's true in both themes: red still means exactly
# what it means in either one (live/error/stop), it just doesn't tint
# the walls.
_DARK = {
    "BG":             "#121316",   # app canvas — neutral graphite
    "SURFACE":        "#1A1C21",   # toolbar / header / table background
    "SURFACE_RAISED": "#23262D",   # hover states, dialogs, popovers
    "LINE":           "rgba(255,255,255,0.07)",   # hairline dividers
    "LINE_STRONG":    "rgba(255,255,255,0.14)",
    "INK":            "#EDEEF2",   # primary text
    "MUTED":          "#8A8F9C",   # secondary text, labels, timestamps
    "RED":            "#E5484D",   # brand / LIVE / error / stop
    "RED_DIM":        "#7A2A2E",   # red at rest (borders, subtle fills)
    "GREEN":          "#35D48C",   # synced / success
    "AMBER":          "#F5A623",   # pending / warning
    "AUTOPILOT_HOVER":"#F0605F",
    "AUTOPILOT_OFF_HOVER": "#45E09C",
}

_LIGHT = {
    "BG":             "#F4F5F7",   # app canvas — neutral light gray, not stark white
    "SURFACE":        "#FFFFFF",   # toolbar / header / table background
    "SURFACE_RAISED": "#EAECF0",   # hover states, dialogs, popovers
    "LINE":           "rgba(0,0,0,0.08)",         # hairline dividers
    "LINE_STRONG":    "rgba(0,0,0,0.16)",
    "INK":            "#14161A",   # primary text
    "MUTED":          "#6B7280",   # secondary text, labels, timestamps
    "RED":            "#E5484D",   # same brand red — deliberate continuity, see module docstring
    "RED_DIM":        "#FBDBDC",   # red at rest — light tint instead of a dark box
    "GREEN":          "#12875D",   # deepened vs dark mode's mint, for contrast on a white canvas
    "AMBER":          "#B45B09",   # deepened vs dark mode's bright amber, same contrast reasoning
    "AUTOPILOT_HOVER":"#F0605F",
    "AUTOPILOT_OFF_HOVER": "#0EA36E",
}

_PALETTES = {"dark": _DARK, "light": _LIGHT}
CURRENT_THEME = "dark"   # overwritten by set_theme() at app startup — this default only
                         # matters for code paths that (unusually) run before that happens


def detect_system_theme() -> str:
    """Reads the OS-level light/dark preference via Qt's own API
    (QStyleHints.colorScheme(), Qt 6.5+) — no extra dependency needed.
    Falls back to 'dark' (this app's original/only look) if the running
    Qt version is older or the OS reports 'Unknown'."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    return "dark"


def set_theme(mode: str):
    """Applies a palette by name ('dark' or 'light'), reassigning every
    module-level color constant plus the QColor objects and the
    generated STYLESHEET string. Falls back to 'dark' for any
    unrecognized value rather than raising — a corrupted/old settings
    file should never be the reason the app won't start.

    Does NOT touch any widgets — main_window.py's _apply_theme() is
    responsible for re-applying the new STYLESHEET to the window and
    rebuilding anything baked at render time (icons)."""
    global CURRENT_THEME, BG, SURFACE, SURFACE_RAISED, LINE, LINE_STRONG
    global INK, MUTED, RED, RED_DIM, GREEN, AMBER, AUTOPILOT_HOVER, AUTOPILOT_OFF_HOVER
    global QC_RED, QC_GREEN, QC_AMBER, QC_MUTED, STYLESHEET

    palette = _PALETTES.get(mode, _DARK)
    mode = mode if mode in _PALETTES else "dark"

    BG              = palette["BG"]
    SURFACE         = palette["SURFACE"]
    SURFACE_RAISED  = palette["SURFACE_RAISED"]
    LINE            = palette["LINE"]
    LINE_STRONG     = palette["LINE_STRONG"]
    INK             = palette["INK"]
    MUTED           = palette["MUTED"]
    RED             = palette["RED"]
    RED_DIM         = palette["RED_DIM"]
    GREEN           = palette["GREEN"]
    AMBER           = palette["AMBER"]
    AUTOPILOT_HOVER = palette["AUTOPILOT_HOVER"]
    AUTOPILOT_OFF_HOVER = palette["AUTOPILOT_OFF_HOVER"]

    QC_RED   = QColor(RED)
    QC_GREEN = QColor(GREEN)
    QC_AMBER = QColor(AMBER)
    QC_MUTED = QColor(MUTED)

    CURRENT_THEME = mode
    STYLESHEET = _build_stylesheet()


# ── Typography ───────────────────────────────────────────────────────────
FONT_UI    = '"Segoe UI", "Inter", -apple-system, "Helvetica Neue", sans-serif'
FONT_MONO  = '"Cascadia Mono", "Consolas", "JetBrains Mono", "SF Mono", monospace'

def font_ui(size=10, weight=QFont.Normal):
    f = QFont("Segoe UI", size)
    f.setStyleHint(QFont.SansSerif)
    f.setWeight(weight)
    return f

def font_mono(size=9):
    f = QFont("Cascadia Mono", size)
    f.setStyleHint(QFont.Monospace)
    return f


# ── Icons ────────────────────────────────────────────────────────────────
# Hand-drawn, single-weight line icons (stroke=1.8, 24x24 viewBox) so there's
# no external asset dependency and no font-icon bundling. "{c}" is swapped
# for the requested color at render time.
_ICONS = {
    "upload":  '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
    "add":     '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    "refresh": '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15.3-6.4L21 8M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.3 6.4L3 16M3 21v-5h5"/></svg>',
    "trash":   '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
    "play":    '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><path d="M7 5l12 7-12 7z"/></svg>',
    "stop":    '<svg viewBox="0 0 24 24" fill="{c}" stroke="none"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
    "retry":   '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M5.5 15a8 8 0 0 0 14-4M18.5 9a8 8 0 0 0-14 4"/></svg>',
    "download":'<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M6 10l6 6 6-6"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
    "close":   '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    "chevron": '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
    "clock":   '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/></svg>',
    "sun":     '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.3"/><path d="M12 2.5v2.6M12 18.9v2.6M4.1 4.1l1.85 1.85M18.05 18.05l1.85 1.85M2.5 12h2.6M18.9 12h2.6M4.1 19.9l1.85-1.85M18.05 5.95l1.85-1.85"/></svg>',
    "moon":    '<svg viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.2 14.6A8.6 8.6 0 1 1 9.4 3.8a7 7 0 0 0 10.8 10.8z"/></svg>',
}

def icon(name: str, color: str = None, size: int = 18) -> QIcon:
    color = color or INK
    svg = _ICONS[name].format(c=color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


# ── Tally-dot delegate ───────────────────────────────────────────────────
# Custom role used by SessionTableModel to say "paint a dot in front of
# this cell's text, in this color." Falls back to plain text if the role
# returns None — used for Status / Prism Sync / Type columns.
DOT_COLOR_ROLE = Qt.UserRole + 10

class TallyDotDelegate(QStyledItemDelegate):
    """Paints a small filled circle + label — the app's one signature
    visual motif, echoing an actual broadcast tally light.

    References INK / SURFACE_RAISED as bare module globals (not copied
    into the class at definition time), so a theme toggle is reflected
    on the very next repaint with no need to recreate this delegate —
    see theme.py's module docstring for why that works."""

    def paint(self, painter, option, index):
        dot_color = index.data(DOT_COLOR_ROLE)
        text = index.data(Qt.DisplayRole) or ""

        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor(SURFACE_RAISED))

        rect = option.rect
        x = rect.x() + 10

        if dot_color is not None:
            d = 7
            cy = rect.center().y()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(dot_color))
            painter.drawEllipse(QRectF(x, cy - d / 2, d, d))
            x += d + 8

        painter.setPen(QColor(INK))
        painter.setFont(option.font)
        text_rect = QRectF(x, rect.y(), rect.right() - x, rect.height())
        painter.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignLeft), str(text))
        painter.restore()


# ── Stylesheet ───────────────────────────────────────────────────────────
def _build_stylesheet() -> str:
    """Rebuilt from the CURRENT module-level color constants — called
    once at import time (dark, the pre-theme-toggle default) and again
    every time set_theme() runs."""
    return f"""
* {{
    font-family: {FONT_UI};
    color: {INK};
    outline: none;
}}
QWidget {{ background: transparent; }}
QMainWindow {{ background: {BG}; }}

#HeaderBar, #ToolBar, #LogPanel {{
    background: {SURFACE};
}}

/* ── Header ─────────────────────────────────────────────────────────── */
#HeaderBar {{ border-bottom: 1px solid {LINE}; }}
#WordmarkDot {{ background: {RED}; border-radius: 4px; }}
#Wordmark {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }}
#WordmarkSub {{ color: {MUTED}; font-size: 12px; font-weight: 600; letter-spacing: 1px; }}

QLabel#Pill {{
    background: {RED_DIM};
    color: {RED};
    border: 1px solid {RED};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#ScanLabel, QLabel#ClockLabel {{
    color: {MUTED};
    font-family: {FONT_MONO};
    font-size: 11px;
}}

QPushButton#AutoPilotBtn {{
    background: {RED};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.5px;
}}
QPushButton#AutoPilotBtn:hover {{ background: {AUTOPILOT_HOVER}; }}
QPushButton#AutoPilotBtn[running="false"] {{ background: {GREEN}; }}
QPushButton#AutoPilotBtn[running="false"]:hover {{ background: {AUTOPILOT_OFF_HOVER}; }}

QPushButton#ThemeToggleBtn {{
    background: transparent;
    border: 1px solid {LINE_STRONG};
    border-radius: 15px;
    padding: 5px;
}}
QPushButton#ThemeToggleBtn:hover {{ background: {SURFACE_RAISED}; border-color: {MUTED}; }}

/* ── Toolbar ────────────────────────────────────────────────────────── */
#ToolBar {{ border-bottom: 1px solid {LINE}; }}
QPushButton#ToolBtn {{
    background: transparent;
    border: 1px solid {LINE_STRONG};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 11px;
    font-weight: 600;
    color: {INK};
    text-align: left;
}}
QPushButton#ToolBtn:hover {{ background: {SURFACE_RAISED}; border-color: {MUTED}; }}
QPushButton#ToolBtn:pressed {{ background: {BG}; }}
QPushButton#ToolBtn:disabled {{ color: {MUTED}; border-color: {LINE}; }}

QLabel#CountBadge {{
    color: {MUTED};
    font-family: {FONT_MONO};
    font-size: 11px;
    padding: 4px 10px;
    border: 1px solid {LINE_STRONG};
    border-radius: 10px;
}}
QLabel#LiveBadge {{
    font-family: {FONT_MONO};
    font-size: 11px;
    padding: 4px 10px;
    border-radius: 10px;
}}
QLabel#LiveBadge[live="true"] {{ color: {RED}; border: 1px solid {RED}; background: {RED_DIM}; }}
QLabel#LiveBadge[live="false"] {{ color: {MUTED}; border: 1px solid {LINE_STRONG}; }}

/* ── Table ──────────────────────────────────────────────────────────── */
QTableView {{
    background: {BG};
    alternate-background-color: {SURFACE};
    gridline-color: transparent;
    border: none;
    selection-background-color: {SURFACE_RAISED};
    selection-color: {INK};
}}
QTableView::item {{ border-bottom: 1px solid {LINE}; padding: 2px 4px; }}
QHeaderView::section {{
    background: {SURFACE};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {LINE_STRONG};
    padding: 8px 10px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QTableView::item:selected {{ background: {SURFACE_RAISED}; }}

/* ── Event log ──────────────────────────────────────────────────────── */
#LogHeader {{ border-top: 1px solid {LINE}; border-bottom: 1px solid {LINE}; background: {SURFACE}; }}
#LogHeaderLabel {{ color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
QPlainTextEdit#LogText {{
    background: {BG};
    color: {INK};
    border: none;
    font-family: {FONT_MONO};
    font-size: 11px;
    padding: 8px 12px;
}}

/* ── Generic ────────────────────────────────────────────────────────── */
QPushButton#IconBtn {{ background: transparent; border: none; border-radius: 4px; padding: 4px; }}
QPushButton#IconBtn:hover {{ background: {SURFACE_RAISED}; }}

QScrollBar:vertical {{ background: {SURFACE}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {LINE_STRONG}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: {SURFACE}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {LINE_STRONG}; border-radius: 5px; min-width: 24px; }}

QDialog {{ background: {SURFACE}; }}

QTabWidget::pane {{
    border: 1px solid {LINE_STRONG};
    border-radius: 6px;
    top: -1px;
    background: {SURFACE};
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 20px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 11px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {INK};
    border-bottom: 2px solid {RED};
}}
QTabBar::tab:hover:!selected {{
    color: {INK};
}}
QLineEdit, QComboBox {{
    background: {BG};
    border: 1px solid {LINE_STRONG};
    border-radius: 5px;
    padding: 6px 8px;
    color: {INK};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {RED}; }}
QLabel#FieldLabel {{ color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; }}
QRadioButton {{ color: {INK}; font-size: 11px; }}

QToolTip {{
    background: {SURFACE_RAISED};
    color: {INK};
    border: 1px solid {LINE_STRONG};
    padding: 4px 6px;
}}
"""


# Apply the default palette immediately at import time — main_window.py
# calls set_theme() again very early in MainWindow.__init__ with the
# real saved/detected preference, but every module-level name below
# must exist and be valid from the moment this module is first imported
# (e.g. models.py references theme.QC_RED at class-definition-adjacent
# import time), so dark is applied here as a safe, working default.
set_theme("dark")
