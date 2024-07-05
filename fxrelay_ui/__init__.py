#!/usr/bin/env python3
# SPDX-License-Identifier: WTFPL

from argparse import ArgumentParser
import asyncio
from dataclasses import dataclass
import datetime
from enum import Enum
from gettext import gettext as g
import os
import subprocess

from httpx import AsyncClient

from rich.text import Text
from rich.style import Style
from textual import log
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select


__version__ = "0.2.0"

DRY_RUN = False


@dataclass
class Column:
    label: str
    json_key: str | None
    editable: bool = False

    @property
    def column_key(self):
        return self.json_key

    def format(self, entry) -> str:
        value = entry[self.json_key]

        if block_entry_to_enum(entry) == Blocking.ALL:
            return Text(str(value), style=Style(color="grey53", strike=True, italic=True))

        return str(value)

    def sortkey(self, value):
        if isinstance(value, Text):
            return value.plain
        return value


class BlockColumn(Column):
    def format(self, entry) -> str:
        return block_enum_to_label(block_entry_to_enum(entry))

    @property
    def column_key(self):
        return "blocking"


class Blocking(Enum):
    NONE = 1
    PROMOTIONS = 2
    ALL = 3


def block_entry_to_enum(entry):
    if not entry["enabled"]:
        return Blocking.ALL
    if entry["block_list_emails"]:
        return Blocking.PROMOTIONS
    return Blocking.NONE


def block_enum_to_label(blocking):
    if ARGS.emojis:
        texts = {
            Blocking.ALL: Text(g("⛔ All"), style=Style(color="white", bgcolor="red")),
            Blocking.PROMOTIONS: Text(g("🗑️ Promotions"), style=Style(color="black", bgcolor="#ff7700")),
            Blocking.NONE: Text(g("✅ None"), style=Style(color="black", bgcolor="#33ff33")),
        }
    else:
        texts = {
            Blocking.ALL: Text(g("All"), style=Style(color="white", bgcolor="red")),
            Blocking.PROMOTIONS: Text(g("Promotions"), style=Style(color="black", bgcolor="#ff7700")),
            Blocking.NONE: Text(g("None"), style=Style(color="black", bgcolor="#33ff33")),
        }

    return texts[blocking]


def block_enum_to_entry(blocking):
    values = {
        Blocking.ALL: {"enabled": False, "block_list_emails": True},
        Blocking.PROMOTIONS: {"enabled": True, "block_list_emails": True},
        Blocking.NONE: {"enabled": True, "block_list_emails": False},
    }
    return values[blocking]


class IntColumn(Column):
    def sortkey(self, value):
        value = super().sortkey(value)
        return int(value)


class DateColumn(Column):
    def format(self, entry) -> str:
        value = entry[self.json_key]
        date = datetime.datetime.fromisoformat(value).astimezone()
        return date.strftime("%Y-%m-%d %H:%M %z")


COLS = [
    Column(g("Description"), "description", editable=True),
    Column(g("E-mail address"), "full_address"),
    IntColumn(g("ID"), "id"),
    BlockColumn(g("Block?"), None, editable=True),
    DateColumn(g("Created at"), "created_at"),
    IntColumn(g("Forwarded"), "num_forwarded"),
    IntColumn(g("Blocked"), "num_blocked"),
    IntColumn(g("Replied"), "num_replied"),
    # IntColumn("#Spam", "num_spam"),
]


class FxRelayClient:
    def __init__(self, token):
        self.token = token
        self.session = AsyncClient()
        self.session.headers["Authorization"] = f"Token {token}"

    async def list_entries(self):
        response = await self.session.get("https://relay.firefox.com/api/v1/relayaddresses/")
        response.raise_for_status()
        return response.json()

    async def new_entry(self):
        response = await self.session.post(
            "https://relay.firefox.com/api/v1/relayaddresses/",
            json={},
        )
        response.raise_for_status()
        return response.json()

    async def edit_entry(self, id, changes):
        if DRY_RUN:
            log(changes)
            return (await self.session.get(f"https://relay.firefox.com/api/v1/relayaddresses/{id}/")).json()

        response = await self.session.patch(
            f"https://relay.firefox.com/api/v1/relayaddresses/{id}/", json=changes,
        )
        response.raise_for_status()
        return response.json()

    async def delete_entry(self, id):
        if DRY_RUN:
            return

        response = await self.session.delete(f"https://relay.firefox.com/api/v1/relayaddresses/{id}/")
        response.raise_for_status()


class WorkerGroup(Enum):
    HTTP_LIST = "http-list"


class Table(DataTable):
    BINDINGS = [
        Binding("(", "sort_asc_col", "Sort column in ascending order"),
        Binding(")", "sort_desc_col", "Sort column in descending order"),
        Binding("ctrl+n", "new_row", "Add a new row"),
        Binding("e", "edit_cell", "Edit current cell"),
        Binding("delete", "delete_row", "Delete current row permanently"),
        Binding("C", "clipboard_email", "Copy email to system clipboard"),
        Binding("/", "prompt_search"),
        Binding("n", "search_next"),
        Binding("N", "search_previous"),
        Binding("R", "refresh_entries"),
    ]

    def __init__(self, client):
        super().__init__()
        self.zebra_stripes = True

        self.client = client
        self._columns = {}
        self.entries = {}
        self.searcher = Searcher(self)

        for column in COLS:
            self._columns[column.column_key] = column

    def on_mount(self):
        for key, col in self._columns.items():
            self.add_column(col.label, key=key)
        self.run_worker(
            self.refresh_entries(), group=WorkerGroup.HTTP_LIST.value, exclusive=True,
        )

    @property
    def cursor_key(self):
        return self.coordinate_to_cell_key(self.cursor_coordinate)

    async def refresh_entries(self):
        self.entries = {str(jentry["id"]): jentry for jentry in await self.client.list_entries()}
        self.clear()
        for entry in self.entries.values():
            self._add_row(entry)

    def _add_row(self, entry):
        self.add_row(
            *(col.format(entry) for col in self._columns.values()), key=str(entry["id"])
        )

    def _update_row(self, row_key):
        entry = self.entries[row_key]
        for col_key, col in self._columns.items():
            self.update_cell(row_key, col_key, col.format(entry))

    def action_sort_asc_col(self):
        col_key = self.cursor_key.column_key
        self.sort(col_key, key=self._columns[col_key].sortkey)

    def action_sort_desc_col(self):
        col_key = self.cursor_key.column_key
        self.sort(col_key, key=self._columns[col_key].sortkey, reverse=True)

    async def action_new_row(self):
        if DRY_RUN:
            entry = next(iter(self.entries.values()))
            entry["id"] = 42
        else:
            entry = await self.client.new_entry()

        self.entries[str(entry["id"])] = entry
        self._add_row(entry)
        coord = self.get_cell_coordinate(str(entry["id"]), "description")
        self.move_cursor(row=coord.row, column=coord.column, animate=True)

    def action_delete_row(self):
        key = self.cursor_key.row_key

        async def on_dismiss(value):
            if not value:
                return

            if not DRY_RUN:
                await self.client.delete_entry(key.value)
            self.remove_row(key)
            del self.entries[key]

        self.app.push_screen(ConfirmScreen(), on_dismiss)

    def action_refresh_entries(self):
        self.run_worker(
            self.refresh_entries(), group=WorkerGroup.HTTP_LIST.value, exclusive=True,
        )

    def _edit_cell(self, row_key, column):
        current_value = self.entries[row_key][column.json_key]

        async def on_dismiss(value):
            if value is None:
                return
            entry = await self.client.edit_entry(row_key.value, {column.json_key: value})
            self.entries[row_key] = entry
            self._update_row(row_key)

        self.app.push_screen(InputScreen(current_value), on_dismiss)

    def _edit_block(self, row_key, column):
        current_value = block_entry_to_enum(self.entries[row_key])

        async def on_dismiss(value):
            if value is None:
                return
            entry = await self.client.edit_entry(row_key.value, block_enum_to_entry(value))
            self.entries[row_key] = entry
            self._update_row(row_key)

        choices = {blocking: block_enum_to_label(blocking) for blocking in Blocking}
        self.app.push_screen(ChoiceScreen(choices, current_value), on_dismiss)

    def action_edit_cell(self):
        col_key = self.cursor_key.column_key
        row_key = self.cursor_key.row_key
        column = self._columns[col_key]
        if not column.editable:
            return

        if isinstance(column, BlockColumn):
            self._edit_block(row_key, column)
        else:
            self._edit_cell(row_key, column)

    def perform_edit(self, row_key, column, value):
        self.run_worker(
            self.client.edit_entry(row_key, {column.json_key: value}),
            group=WorkerGroup.HTTP_EDIT.value,
        )

    async def _subprocess(self, cmd: list[str], stdin: str):
        proc = await asyncio.create_subprocess_exec(*cmd, stdin=subprocess.PIPE)
        await proc.communicate(stdin.encode())
        await proc.wait()

    def action_clipboard_email(self):
        row_key = self.cursor_key.row_key
        email = self.entries[row_key]["full_address"]
        self.run_worker(
            self._subprocess(["xclip", "-i", "-selection", "clipboard"], email)
        )

    def action_prompt_search(self):
        async def on_dismiss(value):
            if not value:
                return
            self.searcher.text = value.lower()
            self.action_search_next()

        self.app.push_screen(InputScreen(self.searcher.text), on_dismiss)

    def action_search_next(self):
        self.searcher.next()

    def action_search_previous(self):
        self.searcher.previous()


class Searcher:
    def __init__(self, table):
        self.table = table
        self.text = ""

    def _new_coordinates_down(self, row, column):
        # FIXME hardcoding column indexes is ugly
        if column == 0:
            return (row, 1)
        else:
            return ((row + 1) % len(self.table.entries), 0)

    def _new_coordinates_up(self, row, column):
        if column == 0:
            return ((row - 1) % len(self.table.entries), 1)
        else:
            return (row, 0)

    def _all_search_cells(self, dir_func):
        current = start = (self.table.cursor_coordinate.row, self.table.cursor_coordinate.column)
        while True:
            current = dir_func(*current)
            if current == start:
                break
            yield current

    def _do_search(self, dir_func):
        if not self.text:
            return

        for row, column in self._all_search_cells(dir_func):
            cell_key = self.table.coordinate_to_cell_key(Coordinate(row, column))
            column_object = self.table._columns[cell_key.column_key]
            entry = self.table.entries[cell_key.row_key]
            if self.text in column_object.sortkey(column_object.format(entry)):
                self.table.move_cursor(row=row, column=column, animate=True)
                return

    def next(self):
        self._do_search(self._new_coordinates_down)

    def previous(self):
        self._do_search(self._new_coordinates_up)


class InputScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel"),
    ]

    def __init__(self, value):
        super().__init__()
        self.init_value = value

    def compose(self):
        yield Input(value=self.init_value)

    def on_input_submitted(self, message):
        self.dismiss(message.value)

    def action_cancel(self):
        self.dismiss(None)


class ChoiceScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, choices, selected):
        super().__init__()
        self.choices = choices
        self.selected = selected

    def compose(self):
        yield Select(
            [(value, key) for key, value in self.choices.items()],
            value=self.selected, allow_blank=False,
        )

    def on_select_changed(self, message):
        self.dismiss(message.value)

    def action_cancel(self):
        self.dismiss(None)

    def on_mount(self):
        self.query_one(Select).expanded = True


class ConfirmScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self):
        yield Grid(
            Button(g("OK"), id="ok"),
            Button(g("Cancel"), id="cancel"),
        )

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, message):
        if message.button.id == "cancel":
            self.dismiss(None)
        if message.button.id == "ok":
            self.dismiss(True)


class TableApp(App):
    # TODO error handling

    CSS_PATH = "fxrelay_ui.css"

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.changes = {}

    def compose(self) -> ComposeResult:
        yield Table(self.client)

    @property
    def table(self):
        # idiomatic?
        return self.query_one(DataTable)

    def on_mount(self) -> None:
        pass


def parse_args():
    global ARGS
    parser = ArgumentParser()
    parser.add_argument("--token")
    parser.add_argument("--emojis", action="store_true")
    ARGS = parser.parse_args()


def main():
    parse_args()
    if not ARGS.token:
        ARGS.token = os.environ["FXRELAY_TOKEN"]

    if ARGS.emojis:
        COLS[5].label = g("✅")
        COLS[6].label = g("⛔")
        COLS[7].label = g("↩️")

    client = FxRelayClient(ARGS.token)
    app = TableApp(client)
    app.run()


if __name__ == "__main__":
    main()
