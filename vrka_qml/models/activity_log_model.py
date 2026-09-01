"""Bounded activity-log presentation model for QML (Stage 2).

Ring-style bounded model mirroring the existing 1000-line activity log.
Rows are plain structs inside the model; no per-line QObject is created.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal

MAX_LOG_LINES = 1000  # mirrors the existing application bound


class ActivityLogModel(QAbstractListModel):
    TimestampRole = Qt.ItemDataRole.UserRole + 1
    LevelRole = Qt.ItemDataRole.UserRole + 2
    MessageRole = Qt.ItemDataRole.UserRole + 3

    _ROLE_NAMES = {
        TimestampRole: b"timestamp",
        LevelRole: b"level",
        MessageRole: b"message",
    }

    countChanged = Signal()
    textChanged = Signal()

    def __init__(self, capacity: int = MAX_LOG_LINES, parent=None):
        super().__init__(parent)
        self._capacity = max(1, int(capacity))
        self._rows: list[tuple[str, str, str]] = []

    def roleNames(self):
        return dict(self._ROLE_NAMES)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._rows):
            return None
        record = self._rows[row]
        if role == self.TimestampRole:
            return record[0]
        if role == self.LevelRole:
            return record[1]
        if role == self.MessageRole:
            return record[2]
        return None

    def get_plain_text(self) -> str:
        return "\n".join(
            (f"[{ts}] {msg}" if ts else msg)
            for ts, _lvl, msg in self._rows
        )

    @staticmethod
    def _classify(message: str) -> str:
        upper = message.upper()
        if "ERROR" in upper or "FAILED" in upper:
            return "error"
        if "WARNING" in upper or "NOTICE" in upper:
            return "warning"
        return "info"

    def append_messages(self, messages) -> None:
        """Append a batch; drops the oldest rows beyond the capacity.

        One insert notification for the batch plus one removal notification
        if the head was trimmed - never a full reset for normal appends.
        """
        fresh = []
        for message in messages:
            for line in str(message).rstrip("\r\n").splitlines() or [""]:
                fresh.append((time.strftime("%H:%M:%S"), self._classify(line), line))
        if not fresh:
            return

        overflow = len(self._rows) + len(fresh) - self._capacity
        if overflow > 0:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self._rows[:overflow]
            self.endRemoveRows()
        if len(fresh) > self._capacity:
            fresh = fresh[-self._capacity:]

        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(fresh) - 1)
        self._rows.extend(fresh)
        self.endInsertRows()
        self.countChanged.emit()
        self.textChanged.emit()

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()
        self.countChanged.emit()
        self.textChanged.emit()
