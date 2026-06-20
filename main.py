import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from app.config import Config
from app.ui.main_window import MainWindow


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("InstaEpi Monitor")
    app.setApplicationVersion("1.0.0")
    app.setStyle("Fusion")

    config = Config.load()
    window = MainWindow(config)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
