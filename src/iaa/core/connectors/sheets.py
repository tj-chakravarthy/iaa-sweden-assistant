"""The workbook: one Google Sheets spreadsheet, several tabs.

Shared tabs:
- proposals  open proposals, waiting for yes/no in the approve column
- changelog  decided proposals (applied, acknowledged, rejected) and the
             reopened and superseded rows a run adds
- runs       one row per inbox file per run
- brief_*    one tab per skill per month

A skill keeps its own tabs (the register: members, aliases) through read and
write, which take the skill's model.

Notes:
- A tab is replaced in one request, never cleared first, so a failed write
  leaves the old content.
- Cells are written RAW and read unformatted. Values come back as written in
  any locale and nothing is evaluated as a formula. Typed dates come back as
  text.
- append works out the next free row itself. The append in Sheets stops at
  the first blank row and would overwrite rows below it.
- read_only refuses every write. Dry runs use it.
- A tab read as a model must have that model's columns (SchemaError).
- 429 and 5xx responses are retried (PatientClient).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, get_origin

import gspread
from gspread.http_client import HTTPClient
from gspread.utils import DateTimeOption, ValueRenderOption
from pydantic import BaseModel

from ..models import Proposal
from ..policy import assert_known_kind, assert_minimised
from .transaction import LOCK_TAB, Transaction

PROPOSAL_COLS = list(Proposal.model_fields)


class SchemaError(RuntimeError):
    """A tab's header row is not what the code writes. Nothing is read from it or written to it."""


class PatientClient(HTTPClient):
    """Retries reads and fixed-range writes on 429 and 5xx.

    The Sheets quota is per minute. Once it is used up, reads are refused for
    about a minute, and now and then for a few seconds more after that. So the
    waits grow to half a minute and then stay there, about four minutes in all.
    Batch mutations recover through the transaction marker.
    """

    WAITS = (2, 4, 8, 16, 32, 32, 32, 32, 32, 32, 32)  # seconds
    sleep = staticmethod(time.sleep)

    def request(self, *args: Any, **kwargs: Any):
        method = str(args[0] if args else kwargs.get("method", "")).lower()
        for wait in (*self.WAITS, None):
            try:
                return super().request(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                if method not in {"get", "put"} or wait is None or not (e.code == 429 or 500 <= e.code < 600):
                    raise
                self.sleep(wait)


class Workbook:
    def __init__(self, sh: gspread.Spreadsheet, read_only: bool = False):
        self.sh, self.read_only = sh, read_only
        self._transaction: Transaction | None = None

    @contextmanager
    def transaction(self):
        if self.read_only:
            raise RuntimeError("dry run: refusing a write transaction")
        if self._transaction is not None:
            raise RuntimeError("nested write transaction")
        transaction = Transaction(self)
        transaction.acquire()
        self._transaction = transaction
        try:
            yield
            transaction.commit()
        finally:
            self._transaction = None
            transaction.release()

    def lock(self) -> dict[str, str] | None:
        """The write lock as owner, state and started. None if there is no lock tab.

        The tab is the lock, not what is written in it: a lock tab someone has
        emptied still stops a run, and comes back as {}.
        """
        try:
            self.sh.worksheet(LOCK_TAB)
        except gspread.WorksheetNotFound:
            return None
        return {str(row[0]): str(row[1]) for row in self._raw_values(LOCK_TAB) if len(row) > 1}

    def unlock(self) -> None:
        """Remove a lock left by an interrupted run. Only when no run is in progress."""
        if self.read_only:
            raise RuntimeError("dry run: refusing to remove the lock")
        for sheet in self.sh.fetch_sheet_metadata()["sheets"]:
            if sheet["properties"]["title"] == LOCK_TAB:
                self.sh.batch_update({"requests": [{"deleteSheet": {"sheetId": sheet["properties"]["sheetId"]}}]})

    @classmethod
    def open(cls, sheet_id: str, read_only: bool = False) -> Workbook:
        from ..auth import credentials

        return cls(gspread.authorize(credentials(), http_client=PatientClient).open_by_key(sheet_id), read_only)

    # ---- helpers ----
    def _ws(self, name: str, cols: list[str], rows: int = 1000):
        """The tab, created with its header if missing. Used by writes only."""
        if self.read_only:
            raise RuntimeError(f"dry run: refusing to write the {name} tab")
        assert_minimised(cols)
        try:
            return self.sh.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(name, rows=rows, cols=len(cols))
            ws.update(values=[cols], range_name="A1")
            return ws

    @staticmethod
    def _replace(ws, rows: list[list[Any]]) -> None:
        width = max(ws.col_count, *(len(r) for r in rows))
        height = max(ws.row_count, len(rows))
        if (height, width) != (ws.row_count, ws.col_count):
            ws.resize(rows=height, cols=width)
        ws.update(values=grid(rows, height, width), range_name="A1")

    # ---- any tab ----
    def _values(self, name: str) -> list[list[Any]]:
        return self._transaction.read(name) if self._transaction else self._raw_values(name)

    def rows(self, name: str) -> list[dict[str, Any]]:
        """The tab as dicts keyed by header. [] if the tab does not exist."""
        return records(self._values(name))

    def _raw_values(self, name: str) -> list[list[Any]]:
        try:
            ws = self.sh.worksheet(name)
        except gspread.WorksheetNotFound:
            return []
        values = ws.get_all_values(
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.formatted_string,
        )
        return [] if values == [[]] else values

    def read[M: BaseModel](self, name: str, model: type[M], optional: tuple[str, ...] = ()) -> list[M]:
        """The tab as model objects. Its columns must be the model's fields.

        A renamed column would read as blank and an extra one would be dropped,
        and the next write would make that permanent. So either is refused.
        Columns in optional may be absent, for a sheet made before they existed.
        """
        values = self._values(name)
        if not values:
            return []
        check_header(name, values, list(model.model_fields), optional)
        return [model(**coerce(model, r)) for r in records(values)]

    def write(self, name: str, model: type[BaseModel], objects: list[BaseModel]) -> None:
        """Replace the tab with objects, one row each."""
        cols = list(model.model_fields)
        rows = [cols] + [[_cell(getattr(o, c)) for c in cols] for o in objects]
        if self._transaction:
            assert_minimised(cols)
            self._transaction.write(name, rows)
        else:
            self._replace(self._ws(name, cols), rows)

    def append(self, name: str, cols: list[str], rows: list[list[Any]]) -> None:
        """Add rows below everything in the tab, including below blank rows."""
        if self._transaction:
            assert_minimised(cols)
            values = self._transaction.read(name)
            if values and [str(header).strip() for header in values[0]][: len(cols)] != cols:
                raise RuntimeError(f"the {name} tab has unexpected columns")
            self._transaction.write(name, (values or [cols]) + [[_cell(value) for value in row] for row in rows])
            return
        ws = self._ws(name, cols)
        if not rows:
            return
        values = ws.get_all_values()
        used = 0 if values == [[]] else len(values)  # trailing blank rows are not returned, inner ones are
        new = [[_cell(v) for v in r] for r in rows]
        if used == 0:
            new.insert(0, cols)
        elif [str(h).strip() for h in values[0]][: len(cols)] != cols:
            raise RuntimeError(f"the {name} tab lacks the columns {cols}: rows would land under the wrong headings")
        height, width = max(ws.row_count, used + len(new)), max(ws.col_count, len(cols))
        if (height, width) != (ws.row_count, ws.col_count):
            ws.resize(rows=height, cols=width)
        ws.update(values=new, range_name=f"A{used + 1}")

    def write_text(self, name: str, text: str) -> None:
        """One column of text, one line per row."""
        rows = [[line] for line in text.splitlines()] or [[""]]
        if self._transaction:
            self._transaction.write(name, rows)
        else:
            self._replace(self._ws(name, ["text"], rows=100), rows)

    # ---- proposals ----
    def proposals(self) -> list[Proposal]:
        return self.read("proposals", Proposal)

    def write_proposals(self, proposals: list[Proposal]) -> None:
        """Full rewrite, used to remove decided rows. New proposals go through append_proposals."""
        for p in proposals:
            assert_known_kind(p.kind)
        self.write("proposals", Proposal, proposals)

    def append_proposals(self, proposals: list[Proposal]) -> None:
        """Add below what is there, so edits being typed are not overwritten."""
        for p in proposals:
            assert_known_kind(p.kind)
        self.append("proposals", PROPOSAL_COLS, [[getattr(p, c) for c in PROPOSAL_COLS] for p in proposals])


# ---- pure helpers ----
def _cell(v: Any) -> Any:
    if isinstance(v, list):
        return ";".join(map(str, v))
    if v is None:
        return ""
    return v


def grid(rows: list[list[Any]], height: int, width: int) -> list[list[Any]]:
    """rows padded with blanks to the full size of the tab, so one write replaces everything."""
    return [list(r) + [""] * (width - len(r)) for r in rows] + [[""] * width for _ in range(height - len(rows))]


def check_header(name: str, values: list[list[Any]], cols: list[str], optional: tuple[str, ...] = ()) -> None:
    header = [str(h).strip() for h in values[0]]
    named = [h for h in header if h]
    problems = {
        "repeated": sorted({h for h in named if named.count(h) > 1}),
        "not expected": sorted(set(named) - set(cols)),
        "missing": sorted(set(cols) - set(named) - set(optional)),
    }
    if any(str(v).strip() for row in values[1:] for i, v in enumerate(row) if i >= len(header) or not header[i]):
        problems["without a heading"] = ["some cells"]
    if any(problems.values()):
        found = "; ".join(f"{kind}: {', '.join(names)}" for kind, names in problems.items() if names)
        raise SchemaError(f"the {name} tab's columns are wrong ({found}); nothing was read or written")


def records(values: list[list[Any]]) -> list[dict[str, Any]]:
    """Header row + data rows -> dicts. Skips blank rows and unnamed columns.
    Whole floats (900000.0) become ints.
    """
    if not values:
        return []
    header = [str(h).strip() for h in values[0]]
    out = []
    for row in values[1:]:
        row = [int(v) if isinstance(v, float) and v.is_integer() else v for v in row]
        if any(str(v).strip() for v in row):
            out.append({h: v for h, v in zip(header, row + [""] * (len(header) - len(row)), strict=False) if h})
    return out


def coerce(model: type[BaseModel], row: dict[str, Any]) -> dict[str, Any]:
    """A sheet row as keyword arguments for model. A blank cell means the field
    default, lists are ;-separated, decimals may use a comma.
    """
    out: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        v = row.get(name, "")
        if field.annotation is str:
            out[name] = str(v)
        elif str(v).strip() == "":
            continue
        elif get_origin(field.annotation) is list:
            out[name] = [x.strip() for x in str(v).split(";") if x.strip()]
        elif field.annotation is float:
            try:
                out[name] = float(str(v).replace(",", "."))
            except ValueError:
                continue
        else:
            out[name] = v
    return out
