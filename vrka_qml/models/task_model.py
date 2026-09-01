"""Task presentation model for QML queue views (Stage 2).

Rows mirror the presentation fields the existing UI contract carries
(``DownloadTask`` fields that reach the ui_queue tuples). Backend task
objects are never exposed.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal


class TaskListModel(QAbstractListModel):
    TaskIdRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    ProgressRole = Qt.ItemDataRole.UserRole + 4
    StageRole = Qt.ItemDataRole.UserRole + 5
    SpeedRole = Qt.ItemDataRole.UserRole + 6
    EtaRole = Qt.ItemDataRole.UserRole + 7
    ErrorRole = Qt.ItemDataRole.UserRole + 8
    OutputPathRole = Qt.ItemDataRole.UserRole + 9
    UrlRole = Qt.ItemDataRole.UserRole + 10
    ModeRole = Qt.ItemDataRole.UserRole + 11

    _ROLE_NAMES = {
        TaskIdRole: b"taskId",
        TitleRole: b"title",
        StatusRole: b"status",
        ProgressRole: b"progress",
        StageRole: b"stage",
        SpeedRole: b"speed",
        EtaRole: b"eta",
        ErrorRole: b"error",
        OutputPathRole: b"outputPath",
        UrlRole: b"url",
        ModeRole: b"mode",
    }

    # Presentation defaults for a row created from its first queue event.
    _DEFAULTS = {
        "title": "",
        "status": "queued",
        "progress": 0.0,
        "stage": "Waiting",
        "speed": "",
        "eta": "",
        "error": "",
        "output_path": "",
        "url": "",
        "mode": "",
    }

    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._row_by_id: dict[str, int] = {}

    def roleNames(self):
        return dict(self._ROLE_NAMES)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = index.row()
        if not 0 <= row < len(self._rows):
            return None
        record = self._rows[row]
        if role == self.TaskIdRole:
            return record.get("task_id") or record.get("taskId", "")
        if role == self.OutputPathRole:
            return record.get("outputPath") or record.get("output_path", "")
        role_name = self._ROLE_NAMES.get(role)
        if role_name:
            key = role_name.decode()
            return record.get(key, "")
        return None

    def row_for(self, task_id: str) -> int:
        return self._row_by_id.get(task_id, -1)

    def upsert(self, task_id: str, **fields) -> tuple[int, list[int]]:
        """Create or update one row; returns (row, changed role list).

        Only genuinely changed fields produce dataChanged roles, so repeated
        progress ticks with identical values stay cheap.
        """
        row = self._row_by_id.get(task_id)
        if row is None:
            record = {"task_id": str(task_id), **self._DEFAULTS}
            record.update(fields)
            if "output_path" in record and "outputPath" not in record:
                record["outputPath"] = record["output_path"]
            parent = QModelIndex()
            self.beginInsertRows(parent, len(self._rows), len(self._rows))
            self._rows.append(record)
            self._row_by_id[record["task_id"]] = len(self._rows) - 1
            self.endInsertRows()
            self.countChanged.emit()
            return len(self._rows) - 1, list(self._ROLE_NAMES)

        record = self._rows[row]
        changed: list[int] = []
        for name, value in fields.items():
            canonical_name = "outputPath" if name == "output_path" else name
            if canonical_name in record and record[canonical_name] == value:
                continue
            record[canonical_name] = value
            record[name] = value
            for role, role_name in self._ROLE_NAMES.items():
                if role_name.decode() == canonical_name:
                    changed.append(role)
                    break
        if changed:
            index = self.index(row)
            self.dataChanged.emit(index, index, changed)
        return row, changed

    def remove_task(self, task_id: str) -> bool:
        row = self._row_by_id.pop(task_id, None)
        if row is None:
            return False
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._rows[row]
        for other_id, other_row in self._row_by_id.items():
            if other_row > row:
                self._row_by_id[other_id] = other_row - 1
        self.endRemoveRows()
        self.countChanged.emit()
        return True

    def clear(self) -> None:
        self.beginResetModel()
        self._rows.clear()
        self._row_by_id.clear()
        self.endResetModel()
        self.countChanged.emit()
