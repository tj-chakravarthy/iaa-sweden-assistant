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

    def worksheet(self, name):
        if name not in self.tabs:
            raise gspread.WorksheetNotFound(name)
        return self.tabs[name]

    def add_worksheet(self, name, rows, cols):
        self.tabs[name] = FakeTab(rows, cols)
        return self.tabs[name]

    def data(self, name):
        return [r for r in self.tabs[name].get_all_values()[1:] if r]


@pytest.fixture
def fake_book():
    return FakeBook()
