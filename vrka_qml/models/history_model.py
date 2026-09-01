"""History presentation model for QML (Stage 2).

History reaches this layer as plain dicts (newest first, bounded at 1000)
exactly as the existing application stores them; storage itself is untouched.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal

MAX_HISTORY_ENTRIES = 1000  # mirrors the existing application bound


class HistoryListModel(QAbstractListModel):
    EntryIdRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    UrlRole = Qt.ItemDataRole.UserRole + 3
    PathRole = Qt.ItemDataRole.UserRole + 4
    ModeRole = Qt.ItemDataRole.UserRole + 5
    TimestampRole = Qt.ItemDataRole.UserRole + 6

    _ROLE_NAMES = {
        EntryIdRole: b"entryId",
        TitleRole: b"title",
        UrlRole: b"url",
        PathRole: b"path",
        ModeRole: b"mode",
        TimestampRole: b"timestamp",
    }

    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[dict] = []

    def roleNames(self):
        return dict(self._ROLE_NAMES)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._entries):
            return None
        entry = self._entries[row]
        if role == self.EntryIdRole:
            return entry["entry_id"]
        return entry.get(self._ROLE_NAMES[role].decode())

    def set_entries(self, entries) -> None:
        """Replace the whole list (mirrors the existing rebuild-on-refresh)."""
        normalized = []
        for entry in tuple(entries)[:MAX_HISTORY_ENTRIES]:
            if not isinstance(entry, dict):
                continue
            normalized.append({
                "entry_id": str(entry.get("id", "")),
                "title": str(entry.get("title", "")),
                "url": str(entry.get("url", "")),
                "path": str(entry.get("path", "")),
                "mode": str(entry.get("mode", "")),
                "timestamp": str(entry.get("timestamp", "")),
            })
        self.beginResetModel()
        self._entries = normalized
        self.endResetModel()
        self.countChanged.emit()
