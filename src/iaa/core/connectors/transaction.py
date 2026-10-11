"""Serialised workbook writes with temporary editor protection."""

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
        self.protections: list[int] = []
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
            raise RuntimeError("workbook is locked; finish the other run or follow lock recovery in the runbook")
        self.used = {sheet["properties"]["sheetId"] for sheet in metadata}
        self.used.update(
            protection["protectedRangeId"] for sheet in metadata for protection in sheet.get("protectedRanges", [])
        )
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
        credentials = getattr(getattr(self.book.sh, "client", None), "auth", None)
        email = getattr(credentials, "service_account_email", None)
        for sheet in [*metadata, {"properties": {"sheetId": self.lock_id}}]:
            protection_id = self.identifier()
            self.protections.append(protection_id)
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "protectedRangeId": protection_id,
                            "range": {"sheetId": sheet["properties"]["sheetId"]},
                            "description": f"iaa lock {self.token}",
                            "warningOnly": False,
                            "editors": {"users": [email] if email else [], "groups": [], "domainUsersCanEdit": False},
                        }
                    }
                }
            )
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
        requests = [{"deleteProtectedRange": {"protectedRangeId": identifier}} for identifier in self.protections]
        requests.append({"deleteSheet": {"sheetId": self.lock_id}})
        try:
            self.book.sh.batch_update({"requests": requests})
        except Exception:
            if self.book._raw_values(LOCK_TAB):
                raise RuntimeError("workbook lock cleanup failed; follow lock recovery in the runbook") from None
