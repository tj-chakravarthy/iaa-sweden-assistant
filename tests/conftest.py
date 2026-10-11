"""Shared test doubles: an inbox with the fixtures and a spreadsheet in memory."""

import copy
import io
from pathlib import Path

import gspread
import openpyxl
import pytest

from iaa.core.connectors import drive
from iaa.core.tabular import read_rows

FX = Path(__file__).parent / "fixtures"


def as_xlsx(csv_name: str) -> bytes:
    wb = openpyxl.Workbook()
    for row in read_rows((FX / csv_name).read_bytes()):
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def inbox(monkeypatch):
    """The inbox folder as a dict of name -> bytes: fixtures under meaningless
    names, payments as a workbook, and a stray PDF.
    """
    files = {
        "Download (3).csv": (FX / "insead_export.csv").read_bytes(),
        "Book1.xlsx": as_xlsx("payments.csv"),
        "report.csv": (FX / "eventbrite_attendees.csv").read_bytes(),
        "minutes.pdf": b"%PDF-1.7 minutes of the board meeting",
    }
    monkeypatch.setattr(
        drive,
        "list_inbox",
        lambda folder: [
            {"id": n, "name": n, "mimeType": "x", "modifiedTime": f"2026-10-0{i}"} for i, n in enumerate(files, 1)
        ],
    )
    monkeypatch.setattr(drive, "download", lambda file_id, mime: files[file_id])
    return files


class FakeTab:
    """A worksheet that stores what it is given.
    No append_rows: Workbook.append must place rows itself.
    """

    def __init__(self, rows, cols):
        self.row_count, self.col_count, self.cells = rows, cols, []

    def update(self, values, range_name):
        assert range_name[0] == "A" and range_name[1:].isdigit()
        top = int(range_name[1:]) - 1
        assert top + len(values) <= self.row_count and all(len(r) <= self.col_count for r in values)
        while len(self.cells) < top + len(values):
            self.cells.append([])
        for i, row in enumerate(values, top):
            self.cells[i] = list(row) + self.cells[i][len(row) :]

    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols

    def get_all_values(self, value_render_option=None, date_time_render_option=None):
        used = max((i + 1 for i, r in enumerate(self.cells) if any(v != "" for v in r)), default=0)
        return copy.deepcopy(self.cells[:used]) or [[]]  # gspread returns [[]] for an empty tab


class FakeBook:
    title = "in memory"

    def __init__(self):
        self.tabs = {}
        self.protections = {}
        self.batches = []
        self.next_id = 1

    def worksheet(self, name):
        if name not in self.tabs:
            raise gspread.WorksheetNotFound(name)
        return self.tabs[name]

    def add_worksheet(self, name, rows, cols):
        if name in self.tabs:
            raise RuntimeError("duplicate worksheet title")
        self.tabs[name] = FakeTab(rows, cols)
        self.tabs[name].id = self.next_id
        self.next_id += 1
        return self.tabs[name]

    def fetch_sheet_metadata(self):
        return {
            "sheets": [
                {
                    "properties": {
                        "title": name,
                        "sheetId": tab.id,
                        "gridProperties": {"rowCount": tab.row_count, "columnCount": tab.col_count},
                    },
                    "protectedRanges": [
                        value for value in self.protections.values() if value["range"]["sheetId"] == tab.id
                    ],
                }
                for name, tab in self.tabs.items()
            ]
        }

    def batch_update(self, body):
        staged = copy.deepcopy(self)
        staged.batches.append(body)
        for request in body["requests"]:
            if "addSheet" in request:
                properties = request["addSheet"]["properties"]
                if any(tab.id == properties["sheetId"] for tab in staged.tabs.values()):
                    raise RuntimeError("duplicate worksheet id")
                tab = staged.add_worksheet(
                    properties["title"],
                    properties["gridProperties"]["rowCount"],
                    properties["gridProperties"]["columnCount"],
                )
                tab.id = properties["sheetId"]
            elif "addProtectedRange" in request:
                protected = request["addProtectedRange"]["protectedRange"]
                staged.protections[protected["protectedRangeId"]] = protected
            elif "deleteProtectedRange" in request:
                del staged.protections[request["deleteProtectedRange"]["protectedRangeId"]]
            elif "deleteSheet" in request:
                name = next(name for name, tab in staged.tabs.items() if tab.id == request["deleteSheet"]["sheetId"])
                del staged.tabs[name]
            elif "updateSheetProperties" in request:
                properties = request["updateSheetProperties"]["properties"]
                tab = next(tab for tab in staged.tabs.values() if tab.id == properties["sheetId"])
                tab.resize(properties["gridProperties"]["rowCount"], properties["gridProperties"]["columnCount"])
            elif "updateCells" in request:
                update = request["updateCells"]
                tab = next(tab for tab in staged.tabs.values() if tab.id == update["range"]["sheetId"])
                tab.cells = [
                    [next(iter(cell.get("userEnteredValue", {}).values()), "") for cell in row["values"]]
                    for row in update["rows"]
                ]
                assert len(tab.cells) <= tab.row_count
                assert all(len(row) <= tab.col_count for row in tab.cells)
            else:
                raise AssertionError("unsupported request")
        self.tabs, self.protections, self.batches = staged.tabs, staged.protections, staged.batches
        self.next_id = staged.next_id
        return {}

    def data(self, name):
        return [r for r in self.tabs[name].get_all_values()[1:] if r]


@pytest.fixture
def fake_book():
    return FakeBook()
