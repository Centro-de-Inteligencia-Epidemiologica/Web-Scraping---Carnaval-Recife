from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QTextEdit,
    QStatusBar, QSplitter, QMessageBox,
)

from app.config import Config
from app.llm.analyzer import AnalysisResult
from app.worker import ScrapeWorker
from app.ui.pages.sources_page import SourcesPage
from app.ui.pages.keywords_page import KeywordsPage
from app.ui.pages.results_page import ResultsPage
from app.ui.pages.settings_page import SettingsPage

_STYLE = """
QMainWindow { background: #f5f5f5; }
QTabWidget::pane { border: 1px solid #ccc; border-radius: 4px; }
QTabBar::tab {
    padding: 8px 18px;
    font-size: 13px;
}
QTabBar::tab:selected {
    background: #1976d2;
    color: white;
    border-radius: 4px 4px 0 0;
}
QPushButton {
    padding: 6px 14px;
    border-radius: 4px;
    border: 1px solid #bbb;
    background: #fff;
}
QPushButton:hover { background: #e3f2fd; }
QPushButton:disabled { color: #aaa; }
"""


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._worker: ScrapeWorker | None = None

        self.setWindowTitle("InstaEpi Monitor — Vigilância Epidemiológica")
        self.resize(1100, 720)
        self.setStyleSheet(_STYLE)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 4)
        root.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("InstaEpi Monitor")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1976d2;")
        subtitle = QLabel("Vigilância epidemiológica via Instagram")
        subtitle.setStyleSheet("color: gray; font-size: 12px;")
        hdr.addWidget(title)
        hdr.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignBottom)
        hdr.addStretch()
        root.addLayout(hdr)

        # Tabs + log splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        # -- Tabs --
        self.tabs = QTabWidget()

        self.sources_page = SourcesPage(self.config)
        self.keywords_page = KeywordsPage(self.config)
        self.results_page = ResultsPage()
        self.settings_page = SettingsPage(self.config)

        self.tabs.addTab(self.sources_page, "Fontes")
        self.tabs.addTab(self.keywords_page, "Palavras-chave")
        self.tabs.addTab(self.results_page, "Resultados")
        self.tabs.addTab(self.settings_page, "Configurações")

        splitter.addWidget(self.tabs)

        # -- Log panel --
        log_widget = QWidget()
        log_lay = QVBoxLayout(log_widget)
        log_lay.setContentsMargins(0, 0, 0, 0)
        log_label = QLabel("Log de varredura:")
        log_label.setStyleSheet("font-weight: bold;")
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(140)
        self.log_box.setStyleSheet(
            "background: #1e1e1e; color: #d4d4d4; font-family: Consolas, monospace; font-size: 11px;"
        )
        log_lay.addWidget(log_label)
        log_lay.addWidget(self.log_box)
        splitter.addWidget(log_widget)

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        # -- Bottom toolbar --
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.start_btn = QPushButton("▶  Iniciar Varredura")
        self.start_btn.setFixedHeight(36)
        self.start_btn.setStyleSheet(
            "QPushButton { background:#2e7d32; color:white; font-weight:bold; border:none; border-radius:4px; }"
            "QPushButton:hover { background:#388e3c; }"
            "QPushButton:disabled { background:#aaa; }"
        )

        self.stop_btn = QPushButton("■  Parar")
        self.stop_btn.setFixedHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background:#c62828; color:white; font-weight:bold; border:none; border-radius:4px; }"
            "QPushButton:hover { background:#d32f2f; }"
            "QPushButton:disabled { background:#aaa; }"
        )

        self.status_label = QLabel("Pronto.")
        self.status_label.setStyleSheet("color: #555;")

        toolbar.addWidget(self.start_btn)
        toolbar.addWidget(self.stop_btn)
        toolbar.addWidget(self.status_label)
        toolbar.addStretch()

        export_btn = QPushButton("Exportar CSV")
        export_btn.setFixedHeight(36)
        export_btn.clicked.connect(self._export)
        toolbar.addWidget(export_btn)

        root.addLayout(toolbar)

        # Signals
        self.start_btn.clicked.connect(self._start_scan)
        self.stop_btn.clicked.connect(self._stop_scan)

    # ------------------------------------------------------------------
    # Scan control
    # ------------------------------------------------------------------

    def _start_scan(self):
        if not self.config.accounts:
            QMessageBox.warning(
                self, "Sem contas",
                "Adicione pelo menos uma conta na aba Fontes."
            )
            return
        if not self.config.keywords:
            QMessageBox.warning(
                self, "Sem palavras-chave",
                "Adicione termos na aba Palavras-chave."
            )
            return
        if not Path(self.config.state_path).exists():
            QMessageBox.warning(
                self, "Sem sessão",
                "Faça login primeiro na aba Configurações."
            )
            return

        self.log_box.clear()
        self._log("Iniciando varredura…")
        self._set_scanning(True)

        self._worker = ScrapeWorker(self.config)
        self._worker.progress.connect(self._log)
        self._worker.result_ready.connect(self._on_result)
        self._worker.scan_finished.connect(self._on_scan_done)
        self._worker.error.connect(lambda e: self._log(f"ERRO: {e}"))
        self._worker.start()

    def _stop_scan(self):
        if self._worker:
            self._worker.stop()
            self._log("Solicitação de parada enviada…")
            self.stop_btn.setEnabled(False)

    def _on_result(self, result: AnalysisResult):
        self.results_page.add_result(result)
        self.tabs.setCurrentIndex(2)  # switch to Results tab
        self.status_label.setText(
            f"Último: @{result.username} | Prob. {result.probability}"
        )

    def _on_scan_done(self):
        self._set_scanning(False)
        self._log("Varredura finalizada.")
        self.status_label.setText("Varredura concluída.")

    def _set_scanning(self, scanning: bool):
        self.start_btn.setEnabled(not scanning)
        self.stop_btn.setEnabled(scanning)

    # ------------------------------------------------------------------

    def _log(self, msg: str):
        self.log_box.append(msg)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def _export(self):
        self.results_page._export_csv()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()
