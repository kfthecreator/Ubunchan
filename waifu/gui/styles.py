"""Qt stylesheets for the dark and light themes."""

RISK_COLORS = {"READ_ONLY": "#4ade80", "NORMAL": "#60a5fa",
               "DANGEROUS": "#fbbf24", "BLOCKED": "#f87171"}

_PALETTES = {
    "dark": dict(bg="#0e0f16", panel="#161824", field="#1f2233", border="#2c3047",
                 fg="#e6e6f0", muted="#8e90a8", accent="#8b7cff", accent_fg="#ffffff",
                 term="#0b0c12"),
    "light": dict(bg="#ffffff", panel="#ffffff", field="#f4f4f8", border="#e1e1ea",
                  fg="#1d1b2e", muted="#6b6d85", accent="#6d5cf0", accent_fg="#ffffff",
                  term="#1b1d2a"),
}


def stylesheet(theme: str) -> str:
    p = _PALETTES.get(theme, _PALETTES["dark"])
    return f"""
    QMainWindow, QDialog {{ background: {p['bg']}; }}
    QWidget {{ color: {p['fg']}; font-size: 13px; }}
    QLabel#muted, QLabel#statusLabel {{ color: {p['muted']}; }}
    QLabel#dialogTitle {{ font-size: 15px; font-weight: 600; }}

    QFrame#bottomBar {{ background: {p['panel']}; border-top: 1px solid {p['border']}; }}
    QFrame#drawer {{ background: {p['panel']}; border-top: 1px solid {p['border']}; }}

    QLineEdit {{ background: {p['field']}; border: 1px solid {p['border']};
                 border-radius: 12px; padding: 0 14px; selection-background-color: {p['accent']}; }}
    QLineEdit#promptInput {{ border-radius: 23px; font-size: 14px; }}
    QLineEdit:focus {{ border: 1px solid {p['accent']}; }}

    QToolButton#micButton, QToolButton#sendButton {{
        background: {p['field']}; border: 1px solid {p['border']}; border-radius: 23px;
        font-size: 19px; }}
    QToolButton#micButton:hover, QToolButton#sendButton:hover {{ border-color: {p['accent']}; }}
    QToolButton#micButton:checked {{ background: #f472b6; color: white; border-color: #f472b6; }}
    QToolButton#micButton:disabled {{ color: {p['muted']}; }}
    QToolButton#sendButton {{ background: {p['accent']}; color: {p['accent_fg']};
                              border-color: {p['accent']}; }}

    QLabel#chip, QToolButton#chipButton {{ background: {p['field']}; border: 1px solid {p['border']};
        border-radius: 10px; padding: 3px 10px; color: {p['muted']}; font-size: 12px; }}
    QToolButton#chipButton:checked {{ color: {p['fg']}; border-color: {p['accent']}; }}

    QComboBox {{ background: {p['field']}; border: 1px solid {p['border']}; border-radius: 10px;
                 padding: 3px 10px; font-size: 12px; color: {p['muted']}; }}
    QComboBox QAbstractItemView {{ background: {p['field']}; color: {p['fg']};
                                   selection-background-color: {p['accent']}; }}

    QPlainTextEdit#terminal {{ background: {p['term']}; color: #d4d4e0; border: 1px solid {p['border']};
                               border-radius: 8px; padding: 6px; }}
    QTextEdit#commandBox {{ background: {p['term']}; color: #e6e6f0; border: 1px solid {p['border']};
                            border-radius: 8px; padding: 6px;
                            font-family: "Ubuntu Mono", monospace; font-size: 14px; }}

    QPushButton {{ background: {p['field']}; border: 1px solid {p['border']}; border-radius: 10px;
                   padding: 7px 16px; }}
    QPushButton:hover {{ border-color: {p['accent']}; }}
    QPushButton#primary {{ background: {p['accent']}; color: {p['accent_fg']}; border-color: {p['accent']}; }}
    QPushButton#danger {{ background: #b91c1c; color: white; border-color: #b91c1c; }}
    QPushButton:disabled {{ background: {p['field']}; color: {p['muted']}; border-color: {p['border']}; }}
    """
