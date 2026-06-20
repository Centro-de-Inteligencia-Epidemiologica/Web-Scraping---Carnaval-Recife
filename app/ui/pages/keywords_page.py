from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
)

from app.config import Config


class KeywordsPage(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._setup_ui()
        self._reload_list()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        title = QLabel("Termos de Vigilância Epidemiológica")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        root.addWidget(title)

        hint = QLabel(
            "O LLM verificará se os posts/stories/reels estão semanticamente "
            "relacionados a estes termos, mesmo que a palavra exata não apareça."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        root.addWidget(self.list_widget)

        add_row = QHBoxLayout()
        self.term_input = QLineEdit()
        self.term_input.setPlaceholderText("Novo termo…")
        self.term_input.returnPressed.connect(self._add_term)
        add_btn = QPushButton("+ Adicionar")
        add_btn.clicked.connect(self._add_term)
        add_row.addWidget(self.term_input)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        rm_btn = QPushButton("Remover Selecionado")
        rm_btn.clicked.connect(self._remove_selected)
        root.addWidget(rm_btn, alignment=Qt.AlignmentFlag.AlignLeft)

    # ------------------------------------------------------------------

    def _reload_list(self):
        self.list_widget.clear()
        for kw in self.config.keywords:
            self.list_widget.addItem(QListWidgetItem(kw))

    def _add_term(self):
        term = self.term_input.text().strip().lower()
        if not term or term in self.config.keywords:
            return
        self.config.keywords.append(term)
        self.config.save()
        self.list_widget.addItem(QListWidgetItem(term))
        self.term_input.clear()

    def _remove_selected(self):
        selected = self.list_widget.selectedItems()
        for item in selected:
            term = item.text()
            if term in self.config.keywords:
                self.config.keywords.remove(term)
            self.list_widget.takeItem(self.list_widget.row(item))
        self.config.save()
