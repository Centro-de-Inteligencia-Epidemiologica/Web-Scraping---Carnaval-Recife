from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
    QMessageBox,
)

from app.config import Config, AccountConfig


class SourcesPage(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._setup_ui()
        self._reload_table()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel("Contas do Instagram a Monitorar")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel(
            "Selecione quais tipos de conteúdo coletar por conta. "
            "As contas devem ser públicas ou seguidas pela conta configurada."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #cccccc;")
        root.addWidget(hint)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["Conta (@)", "Posts", "Stories", "Reels", ""]
        )
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(col, 72)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 90)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.table)

        # Add-account row
        add_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Nome de usuário (sem @)…")
        self.input.returnPressed.connect(self._add_account)
        add_btn = QPushButton("+ Adicionar")
        add_btn.clicked.connect(self._add_account)
        add_row.addWidget(self.input)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

    # ------------------------------------------------------------------

    def _reload_table(self):
        self.table.setRowCount(0)
        for acc in self.config.accounts:
            self._append_row(acc)

    def _append_row(self, acc: AccountConfig):
        row = self.table.rowCount()
        self.table.insertRow(row)

        name_item = QTableWidgetItem(f"@{acc.username}")
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, name_item)

        for col, attr in [(1, "scrape_posts"), (2, "scrape_stories"), (3, "scrape_reels")]:
            cb = QCheckBox()
            cb.setChecked(getattr(acc, attr))
            cb.stateChanged.connect(
                lambda state, a=acc, at=attr: self._toggle(a, at, bool(state))
            )
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.addWidget(cb)
            lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, col, cell)

        rm = QPushButton("Remover")
        rm.clicked.connect(lambda _, u=acc.username: self._remove(u))
        self.table.setCellWidget(row, 4, rm)

    def _add_account(self):
        raw = self.input.text().strip().lstrip("@")
        if not raw:
            return
        if any(a.username == raw for a in self.config.accounts):
            QMessageBox.information(self, "Duplicado", f"@{raw} já está na lista.")
            return
        acc = AccountConfig(username=raw)
        self.config.accounts.append(acc)
        self._append_row(acc)
        self.input.clear()
        self.config.save()

    def _remove(self, username: str):
        self.config.accounts = [
            a for a in self.config.accounts if a.username != username
        ]
        self.config.save()
        self._reload_table()

    def _toggle(self, acc: AccountConfig, attr: str, value: bool):
        setattr(acc, attr, value)
        self.config.save()
