"""The app's look: one dark theme for both apps, applied once to the
QApplication (apply_theme) - a palette for what Qt draws itself and a style
sheet for the controls. Settings sit in rounded cards on a near-black page
(grouped into tinted blocks where a page has a lot on it, widgets/block.py),
blue accents mark what's active, explanatory notes are muted, and section
titles are small, spaced capitals (see widgets/section.py).

The colour tokens here are also what the custom-painted widgets (the
toggle switch, the section chevron, the info panel's timeline) draw with,
so everything shares one set.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# -- tokens ---------------------------------------------------------------------------------
PAGE = "#0d1016"  # the window background
CARD = "#151a22"  # settings cards, panels
CARD_ALT = "#1a2029"  # a card inside a card (nested sections)
INPUT = "#0f131a"  # text fields, combos, tables
BORDER = "#262d39"
BORDER_SOFT = "#1f252f"
TEXT = "#d9dde5"
MUTED = "#8c95a4"  # notes and explanations
DIM = "#5d6675"  # disabled text
ACCENT = "#4e9af5"
ACCENT_HOVER = "#6cabf7"
ACCENT_DIM = "#2b4f7e"
TITLE = "#57b5ff"  # section and card titles
OK = "#3ecf5e"
WARN = "#e0a84a"
ERR = "#e05a4f"

# The tinted blocks a page groups its sections in (widgets/block.py) - the
# Global Effects tab's three: name -> (background, border, title, the cards
# in it, the cards nested in those).
BLOCK_TINTS = {
    "clock": ("#0f1d1e", "#24484a", "#4fd1c0", "#132527", "#182d2f"),  # teal: the beat clock
    "overlays": ("#0e1729", "#263d68", "#6aa6ff", "#131e33", "#18253c"),  # blue: Chase and Group Switch
    # violet, as the dark pulses on the info panel's timeline
    "pulses": ("#17132a", "#3d3268", "#a390ff", "#1c1731", "#221c3a"),
}

FONT_FAMILY = "Segoe UI"
FONT_SIZE_PT = 10

_ASSETS = Path(__file__).resolve().parent / "assets"


def asset_url(name: str) -> str:
    """A style sheet url() for one of the theme's icon files."""
    return f"url({(_ASSETS / name).as_posix()})"


def title_font(base: QFont | None = None) -> QFont:
    """The font of section and card titles: small, semi-bold, spaced capitals."""
    font = QFont(base) if base is not None else QFont(FONT_FAMILY)
    font.setPointSizeF(FONT_SIZE_PT - 1.0)
    font.setWeight(QFont.DemiBold)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
    font.setCapitalization(QFont.AllUppercase)
    return font


def _palette() -> QPalette:
    p = QPalette()
    for group in (QPalette.Active, QPalette.Inactive):
        p.setColor(group, QPalette.Window, QColor(PAGE))
        p.setColor(group, QPalette.WindowText, QColor(TEXT))
        p.setColor(group, QPalette.Base, QColor(INPUT))
        p.setColor(group, QPalette.AlternateBase, QColor(CARD))
        p.setColor(group, QPalette.Text, QColor(TEXT))
        p.setColor(group, QPalette.Button, QColor("#1c232e"))
        p.setColor(group, QPalette.ButtonText, QColor(TEXT))
        p.setColor(group, QPalette.ToolTipBase, QColor("#1c222c"))
        p.setColor(group, QPalette.ToolTipText, QColor(TEXT))
        p.setColor(group, QPalette.Highlight, QColor(ACCENT))
        p.setColor(group, QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(group, QPalette.Link, QColor(ACCENT))
        p.setColor(group, QPalette.PlaceholderText, QColor(DIM))
        p.setColor(group, QPalette.Mid, QColor(BORDER))
        p.setColor(group, QPalette.Dark, QColor(PAGE))
        p.setColor(group, QPalette.Light, QColor("#2c3441"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(DIM))
    p.setColor(QPalette.Disabled, QPalette.Window, QColor(PAGE))
    p.setColor(QPalette.Disabled, QPalette.Base, QColor(CARD))
    p.setColor(QPalette.Disabled, QPalette.Button, QColor(CARD))
    p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(ACCENT_DIM))
    return p


def _block_styles() -> str:
    """Each block tint: its background and border, its title, and its section cards."""
    rules = []
    for tint, (background, border, title, card, nested) in BLOCK_TINTS.items():
        block = f'QFrame[role="block"][tint="{tint}"]'
        rules.append(f"{block} {{ background-color: {background}; border: 1px solid {border}; }}")
        rules.append(f'{block} QFrame[role="section"] {{ background-color: {card}; border-color: {border}; }}')
        rules.append(f'{block} QFrame[role="section"][nested="true"] {{ background-color: {nested}; }}')
        rules.append(f'QLabel[role="blockTitle"][tint="{tint}"] {{ color: {title}; }}')
    return "\n".join(rules)


def stylesheet() -> str:
    check, down, up = asset_url("check.svg"), asset_url("arrow-down.svg"), asset_url("arrow-up.svg")
    return f"""
QWidget {{
    color: {TEXT};
    font-family: "{FONT_FAMILY}", "Noto Sans", sans-serif;
    font-size: {FONT_SIZE_PT}pt;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QToolTip {{
    background-color: #1c222c; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 4px; padding: 6px 8px;
}}
QLabel {{ background: transparent; }}
QLabel[wordWrap="true"] {{ color: {MUTED}; }}
QLabel:disabled {{ color: {DIM}; }}

/* -- cards -- */
QGroupBox {{
    background-color: {CARD};
    border: 1px solid {BORDER}; border-radius: 8px;
    margin-top: 16px; padding: 12px 8px 8px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; top: 3px; padding: 0 6px;
    color: {TITLE}; font-weight: 600;
}}
QFrame[role="section"] {{
    background-color: {CARD};
    border: 1px solid {BORDER}; border-radius: 8px;
}}
QFrame[role="section"][nested="true"] {{ background-color: {CARD_ALT}; border-color: {BORDER_SOFT}; }}
QFrame[role="sectionHeader"] {{ background: transparent; border: none; }}
QWidget[role="sectionContent"] {{ background: transparent; }}
QLabel[role="sectionTitle"] {{ color: {TITLE}; }}
QLabel[role="sectionTitle"][off="true"] {{ color: {MUTED}; }}
QLabel[role="sectionTitle"]:disabled {{ color: {DIM}; }}
QLabel[role="note"] {{ color: {WARN}; font-style: italic; }}
QFrame[role="block"] {{ border-radius: 10px; }}
{_block_styles()}
QFrame[role="panel"], QFrame[frameShape="6"] {{
    background-color: {CARD}; border: 1px solid {BORDER_SOFT}; border-radius: 6px;
}}

/* -- tabs -- */
QTabWidget::pane {{
    border: 1px solid {BORDER_SOFT}; border-radius: 8px; background: transparent; top: -1px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {MUTED};
    padding: 8px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:disabled {{ color: {DIM}; }}

/* -- buttons -- */
QPushButton {{
    background-color: #1c232e; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: #ffffff; }}
QPushButton:pressed {{ background-color: {ACCENT_DIM}; }}
QPushButton:checked {{ background-color: {ACCENT_DIM}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton:disabled {{ color: {DIM}; border-color: {BORDER_SOFT}; background-color: {CARD}; }}
QPushButton:focus {{ border-color: {ACCENT}; }}

/* -- inputs -- */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {INPUT}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 6px;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {{ border-color: #3a4352; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus,
QTextEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{
    color: {DIM}; background-color: {CARD}; border-color: {BORDER_SOFT};
}}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 22px; border: none;
}}
QComboBox::down-arrow {{ image: {down}; width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background-color: #1a2029; color: {TEXT};
    border: 1px solid {BORDER}; outline: 0;
    selection-background-color: {ACCENT_DIM}; selection-color: #ffffff;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border: none; border-top-right-radius: 6px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border: none; border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background-color: #1c232e; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: {up}; width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: {down}; width: 9px; height: 9px; }}

/* -- progress / level bars -- */
QProgressBar {{
    background-color: {INPUT}; border: 1px solid {BORDER}; border-radius: 4px;
    text-align: center; color: {MUTED};
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}

/* -- sliders -- */
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: #262d39; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: #262d39; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: #e6eaf0; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::sub-page:horizontal:disabled {{ background: #343c4a; }}
QSlider::handle:horizontal:disabled {{ background: #4b5361; }}

/* -- check boxes -- */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid #3a4352; border-radius: 4px; background: {INPUT};
}}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; image: {check}; }}
QRadioButton::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {BORDER_SOFT}; background: {CARD};
}}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background-color: #343c4a; border-color: #343c4a;
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {DIM}; }}

/* -- scrolling -- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c3441; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4454; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2c3441; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #3a4454; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- tables, lists, text -- */
QTableWidget, QTableView, QListWidget, QListView, QTreeView {{
    background-color: {INPUT}; alternate-background-color: {CARD};
    border: 1px solid {BORDER}; border-radius: 6px; gridline-color: {BORDER_SOFT};
}}
QTableView::item:selected, QListView::item:selected {{ background: {ACCENT_DIM}; color: #ffffff; }}
QHeaderView::section {{
    background-color: {CARD}; color: {MUTED};
    border: none; border-bottom: 1px solid {BORDER}; padding: 4px 6px;
}}
QTableCornerButton::section {{ background-color: {CARD}; border: none; }}
QTextEdit, QPlainTextEdit {{ background-color: {INPUT}; }}

/* -- menus -- */
QMenu {{ background-color: #1a2029; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT_DIM}; }}
"""


def apply_theme(app: QApplication) -> None:
    """Applies the theme to the whole app: Fusion as the base style (it
    takes a palette and a style sheet evenly on every platform), the dark
    palette, the default font and the style sheet."""
    app.setStyle("Fusion")
    app.setPalette(_palette())
    font = QFont(FONT_FAMILY, FONT_SIZE_PT)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    app.setStyleSheet(stylesheet())
