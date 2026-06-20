"""First-run setup dialog.

Shown automatically when required dependencies (Chromium / Ollama / models)
are missing. Runs the provisioning steps in a background thread and streams
progress to a log box. If everything is already present, the dialog is never
constructed and startup stays instant.
"""
from typing import List

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QProgressBar,
)

from app import bootstrap


class _BootstrapWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(bool)

    def __init__(self, endpoint: str, model_names: List[str], plan: dict):
        super().__init__()
        self.endpoint = endpoint
        self.model_names = model_names
        self.plan = plan

    def run(self):
        try:
            ok = bootstrap.run_plan(
                self.endpoint, self.model_names, self.plan,
                log=lambda m: self.progress.emit(m),
            )
            self.finished_ok.emit(ok)
        except Exception as exc:
            self.progress.emit(f"✘ Erro inesperado: {exc}")
            self.finished_ok.emit(False)


class BootstrapDialog(QDialog):
    def __init__(self, endpoint: str, model_names: List[str], plan: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuração inicial — InstaEpi Monitor")
        self.setModal(True)
        self.resize(640, 460)
        self._worker: _BootstrapWorker | None = None
        self._endpoint = endpoint
        self._models = model_names
        self._plan = plan
        self._done = False
        self._build_ui()
        self._start()

    def _build_ui(self):
        root = QVBoxLayout(self)

        title = QLabel("Preparando dependências para a primeira execução")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        root.addWidget(title)

        summary = QLabel(self._summary_text())
        summary.setWordWrap(True)
        summary.setStyleSheet("color: #444;")
        root.addWidget(summary)

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # indeterminate while working
        root.addWidget(self.bar)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:Consolas,monospace; font-size:11px;"
        )
        root.addWidget(self.log)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.close_btn = QPushButton("Continuar")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        self.skip_btn = QPushButton("Pular por agora")
        self.skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.skip_btn)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    def _summary_text(self) -> str:
        parts = []
        if self._plan.get("need_chromium"):
            parts.append("• Navegador Chromium (~150 MB)")
        if self._plan.get("need_ollama"):
            parts.append("• Ollama (instalador oficial ~700 MB, pedirá UAC)")
        if self._plan.get("need_server"):
            parts.append("• Iniciar o servidor Ollama")
        miss = self._plan.get("missing_models") or []
        if miss:
            parts.append("• Modelos LLM: " + ", ".join(miss))
        if not parts:
            return "Nada a fazer."
        return "Itens a instalar:\n" + "\n".join(parts)

    def _start(self):
        self._log("Iniciando provisionamento…")
        self._worker = _BootstrapWorker(self._endpoint, self._models, self._plan)
        self._worker.progress.connect(self._log)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.start()

    def _log(self, msg: str):
        self.log.append(msg)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _on_done(self, ok: bool):
        self._done = True
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.skip_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self.close_btn.setDefault(True)
        self._log("\n" + ("✔ Tudo pronto!" if ok else "⚠ Concluído com avisos — você pode continuar."))

    def closeEvent(self, event):
        # Don't kill the worker mid-install; just hide if still running.
        if self._worker and self._worker.isRunning() and not self._done:
            self._log("Instalação continua em segundo plano…")
        event.accept()


def ensure_dependencies(config, parent=None) -> bool:
    """Check deps; if any are missing, show the setup dialog (blocking).

    Returns True if nothing was needed or setup completed; False if the user
    skipped. Never raises — a failure just lets the app open with reduced
    functionality.
    """
    try:
        model_names = [config.text_model, config.vision_model]
        plan = bootstrap.compute_plan(config.ollama_endpoint, model_names)
        if not bootstrap.plan_has_work(plan):
            return True
        dlg = BootstrapDialog(config.ollama_endpoint, model_names, plan, parent)
        result = dlg.exec()
        return result == QDialog.DialogCode.Accepted
    except Exception:
        return True
