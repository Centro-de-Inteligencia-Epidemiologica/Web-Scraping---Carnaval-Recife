from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QComboBox, QSpinBox, QCheckBox, QTextEdit, QMessageBox,
)

from app.config import Config
from app.worker import LoginWorker
from app import bootstrap
from app.ui.bootstrap_dialog import BootstrapDialog


class SettingsPage(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._login_worker: LoginWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(16)

        # ── Instagram ────────────────────────────────────────────
        ig_box = QGroupBox("Conta Instagram")
        ig_form = QFormLayout(ig_box)

        self.user_edit = QLineEdit(self.config.ig_username)
        self.pass_edit = QLineEdit(self.config.ig_password)
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)

        ig_form.addRow("Usuário:", self.user_edit)
        ig_form.addRow("Senha:", self.pass_edit)

        btn_row = QHBoxLayout()
        self.login_headless_btn = QPushButton("Login Automático (headless)")
        self.login_browser_btn = QPushButton("Login com Navegador Visível")
        self.session_label = QLabel()
        btn_row.addWidget(self.login_headless_btn)
        btn_row.addWidget(self.login_browser_btn)
        btn_row.addStretch()

        ig_form.addRow(btn_row)
        ig_form.addRow("Sessão:", self.session_label)
        root.addWidget(ig_box)

        self._refresh_session_label()

        # ── Ollama ────────────────────────────────────────────────
        llm_box = QGroupBox("LLM Local (Ollama)")
        llm_form = QFormLayout(llm_box)

        self.endpoint_edit = QLineEdit(self.config.ollama_endpoint)
        self.text_model_edit = QLineEdit(self.config.text_model)
        self.vision_model_edit = QLineEdit(self.config.vision_model)

        llm_form.addRow("Endpoint:", self.endpoint_edit)
        llm_form.addRow("Modelo de texto:", self.text_model_edit)
        llm_form.addRow("Modelo de visão:", self.vision_model_edit)

        test_btn = QPushButton("Testar Conexão")
        llm_form.addRow(test_btn)

        self.conn_label = QLabel("")
        llm_form.addRow(self.conn_label)
        root.addWidget(llm_box)

        # ── Scraping options ──────────────────────────────────────
        opt_box = QGroupBox("Opções de Varredura")
        opt_form = QFormLayout(opt_box)

        self.max_spin = QSpinBox()
        self.max_spin.setRange(1, 500)
        self.max_spin.setValue(self.config.max_posts_per_account)

        self.min_prob_combo = QComboBox()
        for v, label in [
            (1, "1 – Qualquer resultado"),
            (2, "2 – Possível relevância (padrão)"),
            (3, "3 – Provável relevância"),
            (4, "4 – Alta relevância apenas"),
        ]:
            self.min_prob_combo.addItem(label, v)
        self.min_prob_combo.setCurrentIndex(self.config.min_probability - 1)

        self.analyze_all_cb = QCheckBox(
            "Analisar com LLM mesmo sem palavras-chave no texto"
        )
        self.analyze_all_cb.setChecked(self.config.analyze_all)

        opt_form.addRow("Máx. itens por conta:", self.max_spin)
        opt_form.addRow("Probabilidade mínima:", self.min_prob_combo)
        opt_form.addRow(self.analyze_all_cb)
        root.addWidget(opt_box)

        # ── Dependencies ─────────────────────────────────────────
        dep_box = QGroupBox("Dependências")
        dep_lay = QVBoxLayout(dep_box)

        self.dep_status = QLabel()
        self.dep_status.setTextFormat(Qt.TextFormat.RichText)
        dep_lay.addWidget(self.dep_status)

        dep_btn_row = QHBoxLayout()
        self.install_deps_btn = QPushButton("Instalar dependências")
        self.recheck_deps_btn = QPushButton("Verificar novamente")
        dep_btn_row.addWidget(self.install_deps_btn)
        dep_btn_row.addWidget(self.recheck_deps_btn)
        dep_btn_row.addStretch()
        dep_lay.addLayout(dep_btn_row)
        root.addWidget(dep_box)

        self._refresh_dep_status()

        # ── Save ──────────────────────────────────────────────────
        save_btn = QPushButton("Salvar Configurações")
        save_btn.setFixedHeight(36)
        root.addWidget(save_btn)

        # ── Log ───────────────────────────────────────────────────
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(120)
        self.log_box.setPlaceholderText("Log de login…")
        root.addWidget(self.log_box)

        root.addStretch()

        # Signals
        self.login_headless_btn.clicked.connect(lambda: self._start_login(headless=True))
        self.login_browser_btn.clicked.connect(lambda: self._start_login(headless=False))
        test_btn.clicked.connect(self._test_llm)
        save_btn.clicked.connect(self._save)
        self.install_deps_btn.clicked.connect(self._install_dependencies)
        self.recheck_deps_btn.clicked.connect(self._refresh_dep_status)

    # ------------------------------------------------------------------

    def _refresh_dep_status(self):
        """Probe Chromium / Ollama / models and render a colored summary."""
        def mark(ok: bool, label: str) -> str:
            color = "#4caf50" if ok else "#ef5350"
            glyph = "✔" if ok else "✘"
            return f'<span style="color:{color};">{glyph} {label}</span>'

        endpoint = self.endpoint_edit.text().strip() or self.config.ollama_endpoint
        models = [self.text_model_edit.text().strip(), self.vision_model_edit.text().strip()]

        has_chromium = bootstrap.chromium_installed()
        has_ollama = bootstrap.ollama_exe_path() is not None
        server_up = bootstrap.ollama_server_up(endpoint)
        missing = bootstrap.missing_models(endpoint, models) if server_up else models

        rows = [
            mark(has_chromium, "Navegador Chromium"),
            mark(has_ollama, "Ollama instalado"),
            mark(server_up, "Servidor Ollama ativo"),
            mark(not missing, "Modelos: " + (", ".join(models) if not missing
                                             else "faltando " + ", ".join(missing))),
        ]
        self.dep_status.setText("<br>".join(rows))

    def _install_dependencies(self):
        """Manual entry point in case the first-run prompt was skipped."""
        self._save()
        endpoint = self.config.ollama_endpoint
        models = [self.config.text_model, self.config.vision_model]
        plan = bootstrap.compute_plan(endpoint, models)

        if not bootstrap.plan_has_work(plan):
            QMessageBox.information(
                self, "Dependências",
                "Todas as dependências já estão instaladas."
            )
            self._refresh_dep_status()
            return

        dlg = BootstrapDialog(endpoint, models, plan, self)
        dlg.exec()
        self._refresh_dep_status()

    def _refresh_session_label(self):
        path = Path(self.config.state_path)
        if path.exists():
            self.session_label.setText(f"✔ Sessão salva em {self.config.state_path}")
            self.session_label.setStyleSheet("color: #4caf50;")
        else:
            self.session_label.setText("✘ Sem sessão – faça login")
            self.session_label.setStyleSheet("color: #ef5350;")

    def _start_login(self, headless: bool):
        if self._login_worker and self._login_worker.isRunning():
            return
        self._save()
        self.log_box.clear()
        self._set_login_buttons(False)

        self._login_worker = LoginWorker(self.config, headless=headless)
        self._login_worker.progress.connect(self._on_login_progress)
        self._login_worker.finished.connect(self._on_login_done)
        self._login_worker.start()

    def _set_login_buttons(self, enabled: bool):
        self.login_headless_btn.setEnabled(enabled)
        self.login_browser_btn.setEnabled(enabled)

    def _on_login_progress(self, msg: str):
        self.log_box.append(msg)

    def _on_login_done(self, ok: bool, msg: str):
        self.log_box.append(("✔ " if ok else "✘ ") + msg)
        self._set_login_buttons(True)
        self._refresh_session_label()

    def _test_llm(self):
        from app.llm.analyzer import LLMAnalyzer
        self._save()
        analyzer = LLMAnalyzer(
            self.config.ollama_endpoint,
            self.config.text_model,
            self.config.vision_model,
        )
        ok, msg = analyzer.test_connection()
        self.conn_label.setText(("✔ " if ok else "✘ ") + msg)
        self.conn_label.setStyleSheet("color: #4caf50;" if ok else "color: #ef5350;")

    def _save(self):
        self.config.ig_username = self.user_edit.text().strip()
        self.config.ig_password = self.pass_edit.text()
        self.config.ollama_endpoint = self.endpoint_edit.text().strip()
        self.config.text_model = self.text_model_edit.text().strip()
        self.config.vision_model = self.vision_model_edit.text().strip()
        self.config.max_posts_per_account = self.max_spin.value()
        self.config.min_probability = self.min_prob_combo.currentData()
        self.config.analyze_all = self.analyze_all_cb.isChecked()
        self.config.save()
