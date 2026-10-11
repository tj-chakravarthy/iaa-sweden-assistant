"""Serialised workbook writes: a lock tab, a check for edits, one atomic commit.

The lock keeps two runs apart. It does not stop a person editing: an edit made
during a run is caught by the comparison in commit, and the run writes nothing.
A lock left by an interrupted run is removed with `iaa unlock`.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import gspread

if TYPE_CHECKING:
    from .sheets import Workbook

LOCK_TAB = "_iaa_write_lock"


def cells_request(sheet_id: int, rows: list[list[Any]]) -> dict:
    def cell(value):
        if value is None or value == "":
            return {}
        kind = (
            "boolValue"
            if isinstance(value, bool)
            else "numberValue"
            if isinstance(value, (int, float))
            else "stringValue"
        )
        return {"userEnteredValue": {kind: value if kind != "stringValue" else str(value)}}

    return {
        "updateCells": {
            "range": {"sheetId": sheet_id},
            "rows": [{"values": [cell(value) for value in row]} for row in rows],
            "fields": "userEnteredValue",
        }
    }


class Transaction:
    def __init__(self, book: Workbook):
        self.book = book
        self.token = uuid4().hex
        self.staged: dict[str, list[list[Any]]] = {}
        self.original: dict[str, list[list[Any]]] = {}
        self.sheets: dict[str, dict] = {}
        self.used: set[int] = set()
        self.uncertain = False

    def identifier(self) -> int:
        value = secrets.randbelow(2**30)
        while value in self.used:
            value = secrets.randbelow(2**30)
        self.used.add(value)
        return value

    def acquire(self) -> None:
        metadata = self.book.sh.fetch_sheet_metadata()["sheets"]
        self.sheets = {sheet["properties"]["title"]: sheet["properties"] for sheet in metadata}
        if LOCK_TAB in self.sheets:
            raise RuntimeError("workbook is locked; finish the other run, or see iaa unlock in the runbook")
        self.used = {sheet["properties"]["sheetId"] for sheet in metadata}
        self.lock_id = self.identifier()
        self.started = datetime.now(UTC).isoformat()
        requests = [
            {
                "addSheet": {
                    "properties": {
                        "sheetId": self.lock_id,
                        "title": LOCK_TAB,
                        "gridProperties": {"rowCount": 3, "columnCount": 2},
                    }
                }
            },
            cells_request(self.lock_id, self.marker("locked")),
        ]
        try:
            self.book.sh.batch_update({"requests": requests})
        except Exception:
            if self.book._raw_values(LOCK_TAB) != self.marker("locked"):
                raise RuntimeError("could not acquire workbook lock; inspect the lock tab before retrying") from None

    def marker(self, state: str) -> list[list[str]]:
        return [["owner", self.token], ["state", state], ["started", self.started]]

    def read(self, name: str) -> list[list[Any]]:
        if name in self.staged:
            return self.staged[name]
        if name in self.original:
            return self.original[name]  # commit checks it is still what the tab holds
        values = self.book._raw_values(name)
        if name not in self.sheets and values:
            raise RuntimeError("a tab was added during the run; no data was written")
        self.original.setdefault(name, values)
        return values

    def write(self, name: str, rows: list[list[Any]]) -> None:
        if name == LOCK_TAB:
            raise ValueError("the lock tab is reserved")
        if name not in self.original:
            self.read(name)
        self.staged[name] = rows

    def commit(self) -> None:
        if not self.staged:
            return
        if any(self.book._raw_values(name) != original for name, original in self.original.items()):
            raise RuntimeError("the workbook changed during the run; no data was written")
        requests = []
        for name, rows in self.staged.items():
            properties = self.sheets.get(name)
            height, width = max(1, len(rows)), max(1, *(len(row) for row in rows))
            if properties is None:
                sheet_id = self.identifier()
                requests.append(
                    {
                        "addSheet": {
                            "properties": {
                                "sheetId": sheet_id,
                                "title": name,
                                "gridProperties": {"rowCount": height, "columnCount": width},
                            }
                        }
                    }
                )
            else:
                sheet_id = properties["sheetId"]
                grid = properties["gridProperties"]
                height, width = max(height, grid["rowCount"]), max(width, grid["columnCount"])
                requests.append(
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {"rowCount": height, "columnCount": width},
                            },
                            "fields": "gridProperties.rowCount,gridProperties.columnCount",
                        }
                    }
                )
            requests.append(cells_request(sheet_id, rows))
        requests.append(cells_request(self.lock_id, self.marker("committed")))
        try:
            self.book.sh.batch_update({"requests": requests})
        except Exception as error:
            self.uncertain = True
            if self.book._raw_values(LOCK_TAB) == self.marker("committed"):
                self.uncertain = False
                return
            if isinstance(error, gspread.exceptions.APIError) and error.code in {400, 403, 404, 429}:
                self.uncertain = False
            raise

    def release(self) -> None:
        if self.uncertain:
            return
        marker = self.book._raw_values(LOCK_TAB)
        if not marker or marker[0] != ["owner", self.token]:
            raise RuntimeError("workbook lock ownership changed")
        try:
            self.book.sh.batch_update({"requests": [{"deleteSheet": {"sheetId": self.lock_id}}]})
        except Exception:
            if self.book._raw_values(LOCK_TAB):
                raise RuntimeError("workbook lock cleanup failed; see iaa unlock in the runbook") from None
