import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtCore import Qt
from app.config import Config
from app.ui.main_window import MainWindow
from app.ui.bootstrap_dialog import ensure_dependencies


# Global high-contrast dark theme: pure-black backgrounds, clear white text.
_DARK_QSS = """
* {
    background-color: #000000;
    color: #ffffff;
    selection-background-color: #1976d2;
    selection-color: #ffffff;
}
QToolTip {
    background-color: #000000;
    color: #ffffff;
    border: 1px solid #555555;
}
QGroupBox {
    border: 1px solid #555555;
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: #ffffff;
}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background-color: #1a1a1a;
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 3px;
}
QComboBox QAbstractItemView {
    background-color: #1a1a1a;
    color: #ffffff;
    selection-background-color: #1976d2;
}
QTabWidget::pane { border: 1px solid #555555; border-radius: 4px; }
QTabBar::tab {
    background: #1a1a1a;
    color: #ffffff;
    padding: 8px 18px;
    font-size: 13px;
}
QTabBar::tab:selected {
    background: #1976d2;
    color: #ffffff;
    border-radius: 4px 4px 0 0;
}
QPushButton {
    padding: 6px 14px;
    border-radius: 4px;
    border: 1px solid #777777;
    background: #1a1a1a;
    color: #ffffff;
}
QPushButton:hover { background: #2a2a2a; }
QPushButton:disabled { color: #888888; background: #111111; }
QHeaderView::section {
    background-color: #1a1a1a;
    color: #ffffff;
    border: 1px solid #555555;
    padding: 4px;
}
QTableWidget {
    background-color: #000000;
    color: #ffffff;
    gridline-color: #555555;
    alternate-background-color: #141414;
}
QTableWidget::item:selected { background-color: #1976d2; color: #ffffff; }
QScrollBar:vertical, QScrollBar:horizontal { background: #1a1a1a; }
"""


def _apply_dark_theme(app: QApplication):
    """Force a black background / white text palette on every widget."""
    pal = QPalette()
    black = QColor("#000000")
    white = QColor("#ffffff")
    field = QColor("#1a1a1a")
    accent = QColor("#1976d2")
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                 QPalette.ColorRole.ToolTipBase):
        pal.setColor(role, black)
    pal.setColor(QPalette.ColorRole.AlternateBase, field)
    pal.setColor(QPalette.ColorRole.Button, field)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText, QPalette.ColorRole.ToolTipText,
                 QPalette.ColorRole.BrightText):
        pal.setColor(role, white)
    pal.setColor(QPalette.ColorRole.Highlight, accent)
    pal.setColor(QPalette.ColorRole.HighlightedText, white)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                 QColor("#888888"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                 QColor("#888888"))
    app.setPalette(pal)
    app.setStyleSheet(_DARK_QSS)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("InstaEpi Monitor")
    app.setApplicationVersion("1.0.0")
    app.setStyle("Fusion")
    _apply_dark_theme(app)

    config = Config.load()

    # First-run provisioning: install Chromium / Ollama / models if missing.
    # No-op (instant) when everything is already present.
    ensure_dependencies(config)

    window = MainWindow(config)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
