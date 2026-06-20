from typing import List

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox,
)

from app.llm.analyzer import AnalysisResult
from app.utils import csv_exporter

# Probability → (label, background color)
_PROB_STYLE = {
    1: ("1 – Baixa",    "#e0e0e0"),
    2: ("2 – Possível", "#fff176"),
    3: ("3 – Provável", "#ffb74d"),
    4: ("4 – Alta",     "#ef9a9a"),
}

_COLUMNS = [
    ("Autor",        "author",          150),
    ("Conta-fonte",  "source_account",  150),
    ("Cidade",       "city",            110),
    ("Tipo",         "content_type",     70),
    ("Prob.",        "_prob_label",      90),
    ("Motivo",       "reason",          200),
    ("Texto",        "text",            300),
    ("URL",          "url",             200),
]

# Column indices used for special handling.
_PROB_COL = 4
_TEXT_COL = 6
_URL_COL = 7


class ResultsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: List[AnalysisResult] = []
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.count_label = QLabel("Nenhum resultado ainda.")
        self.count_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self.count_label)
        header.addStretch()

        self.export_btn = QPushButton("Exportar CSV")
        self.export_btn.clicked.connect(self._export_csv)
        self.clear_btn = QPushButton("Limpar")
        self.clear_btn.clicked.connect(self._clear)
        header.addWidget(self.export_btn)
        header.addWidget(self.clear_btn)
        root.addLayout(header)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in _COLUMNS])
        hh = self.table.horizontalHeader()
        for idx, (_, _, width) in enumerate(_COLUMNS):
            if width:
                self.table.setColumnWidth(idx, width)
        hh.setSectionResizeMode(_TEXT_COL, QHeaderView.ResizeMode.Stretch)  # Text column
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._open_url)
        root.addWidget(self.table)

        note = QLabel("Duplo-clique numa linha para abrir a URL no navegador.")
        note.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(note)

    # ------------------------------------------------------------------

    def add_result(self, result: AnalysisResult):
        self._results.append(result)
        row = self.table.rowCount()
        self.table.insertRow(row)

        prob_label, prob_color = _PROB_STYLE.get(result.probability, ("?", "#ffffff"))

        values = [
            result.author,
            result.source_account,
            result.city,
            result.content_type,
            prob_label,
            result.reason[:100],
            result.text[:300],
            result.url,
        ]

        for col, val in enumerate(values):
            item = QTableWidgetItem(val)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if col == _PROB_COL:
                item.setBackground(QColor(prob_color))
            self.table.setItem(row, col, item)

        self.table.scrollToBottom()
        self._update_count()

    def _update_count(self):
        n = len(self._results)
        self.count_label.setText(f"{n} resultado{'s' if n != 1 else ''} encontrado{'s' if n != 1 else ''}.")

    def _clear(self):
        self.table.setRowCount(0)
        self._results.clear()
        self._update_count()

    def _export_csv(self):
        if not self._results:
            QMessageBox.information(self, "Vazio", "Nenhum resultado para exportar.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar CSV", "resultados_epid.csv", "CSV (*.csv)"
        )
        if not path:
            return
        n = csv_exporter.export(self._results, path)
        QMessageBox.information(self, "Exportado", f"{n} linhas salvas em:\n{path}")

    def _open_url(self, row: int, _col: int):
        url_item = self.table.item(row, _URL_COL)
        if url_item:
            QDesktopServices.openUrl(QUrl(url_item.text()))
