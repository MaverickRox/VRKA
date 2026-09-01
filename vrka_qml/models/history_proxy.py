"""History search filter proxy (Stage 4).

Case-insensitive substring match on title and path, mirroring the 3.0
``_rebuild_history_list`` behaviour (vrka_downloader.py:7503).
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QRegularExpression,
    QSortFilterProxyModel,
    QTimer,
    Signal,
    Slot,
)

from .history_model import HistoryListModel


class HistoryFilterProxy(QSortFilterProxyModel):
    """Filters a HistoryListModel by a search query (title + path)."""

    filteredCountChanged = Signal()

    def __init__(self, source: HistoryListModel, *, parent=None):
        super().__init__(parent)
        self.setSourceModel(source)
        self._query = ""
        self._apply_counter = 0

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self._apply)

        source.modelReset.connect(self.filteredCountChanged)
        source.rowsInserted.connect(self.filteredCountChanged)
        source.rowsRemoved.connect(self.filteredCountChanged)
        self.rowsInserted.connect(self.filteredCountChanged)
        self.rowsRemoved.connect(self.filteredCountChanged)
        self.modelReset.connect(self.filteredCountChanged)

    @Property(int, notify=filteredCountChanged)
    def filteredCount(self) -> int:
        return self.rowCount()

    @Slot(str)
    def setFilter(self, text: str) -> None:
        self._query = str(text or "").strip().lower()
        self._debounce.start()

    def _apply(self) -> None:
        self._apply_counter += 1
        self.setFilterRegularExpression(
            QRegularExpression(f"(?:{self._apply_counter})")
        )

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        if not self._query:
            return True
        model = self.sourceModel()
        index_title = model.index(source_row, 0)
        title = str(model.data(index_title, HistoryListModel.TitleRole) or "").lower()
        if self._query in title:
            return True
        index_path = model.index(source_row, 0)
        path = str(model.data(index_path, HistoryListModel.PathRole) or "").lower()
        return self._query in path
