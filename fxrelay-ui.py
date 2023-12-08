#!/usr/bin/env python3

from dataclasses import dataclass
import os

from requests import Session

from rich.text import Text
from textual import log, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.message_pump import MessagePump
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input


@dataclass
class Column:
    label: str
    json_key: str | None
    editable: bool = False

    def format(self, value) -> str:
        return str(value)

    def sortkey(self, value):
        return value


class BoolColumn(Column):
    def format(self, value) -> str:
        if value:
            return "✅"
        else:
            return " "


class IntColumn(Column):
    def sortkey(self, value):
        return int(value)


COLS = [
    Column("Description", "description", editable=True),
    Column("E-mail address", "full_address"),
    IntColumn("ID", "id"),
    BoolColumn("Enabled?", "enabled", editable=True),
    BoolColumn("Block?", "block_list_emails", editable=True), # TODO merge columns
    Column("Created at", "created_at"),
    IntColumn("#Forwarded", "num_forwarded"),
    IntColumn("#Blocked", "num_blocked"),
    # Column("#Replied", "num_replied"),
    # Column("#Spam", "num_spam"),
]


class FxRelayClient:
    def __init__(self, token):
        self.token = token
        self.session = Session()
        self.session.headers["Authorization"] = f"Token {token}"

    def list_entries(self):
        response = self.session.get("https://relay.firefox.com/api/v1/relayaddresses/")
        response.raise_for_status()
        return response.json()

    def new_entry(self):
        response = self.session.post(
            "https://relay.firefox.com/api/v1/relayaddresses/",
            json={},
        )
        response.raise_for_status()
        return response.json()

    def edit_entry(self, id, changes):
        log(changes)
        return
        response = self.session.patch(
            f"https://relay.firefox.com/api/v1/relayaddresses/{id}",
            json={},
        )
        response.raise_for_status()
        return response.json()

    def delete_entry(self, id):
        response = self.session.delete(f"https://relay.firefox.com/api/v1/relayaddresses/{id}")
        response.raise_for_status()
        return response.json()


class Table(DataTable):
    BINDINGS = [
        # ("T", "toggle_cell", "toogle"),
        Binding("(", "sort_asc_col"),
        Binding(")", "sort_desc_col"),
        Binding("ctrl+n", "new_row"),
    ]

    [
        Binding("/", "enter_search"),
        Binding("n", "search_next"),
        Binding("shift+n", "search_prev"),
        Binding("Y", "copy_clipboard_cell"),
        Binding("delete", "delete_row"),
        Binding("ctrl+s", "save_changes"),
    ]

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._columns = {}
        self.entries = {}

        for n, column in enumerate(COLS):
            self._columns[str(n)] = column

    def on_mount(self):
        for key, col in self._columns.items():
            self.add_column(col.label, key=key)
        self.refresh_entries()

    @property
    def cursor_key(self):
        return self.coordinate_to_cell_key(self.cursor_coordinate)

    def refresh_entries(self):
        self.entries = {str(jentry["id"]): jentry for jentry in self.client.list_entries()}
        self.clear()
        for row_key, entry in self.entries.items():
            self.add_row(
                *(col.format(entry[col.json_key]) for col in self._columns.values()), key=row_key
            )

    def action_sort_asc_col(self):
        col_key = self.cursor_key.column_key
        self.sort(col_key, key=self._columns[col_key].sortkey)

    def action_sort_desc_col(self):
        col_key = self.cursor_key.column_key
        self.sort(col_key, key=self._columns[col_key].sortkey, reverse=True)

    def action_new_row(self):
        entry = self.client.new_entry()
        # self.entries[entry["id"]] = entry
        # self.table.add_row(*(col.format(jentry[col.json_key]) for col in COLS), key=jentry["id"])

    def action_delete_row(self):
        key = self.cursor_key.row_key
        # self.client.delete_entry(key)
        self.table.remove_row(key)
        del self.entries[key]


class InputScreen(ModalScreen):
    def __init__(self, value):
        super().__init__()
        self.init_value = value

    def compose(self):
        yield Input(value=self.init_value)

    def on_input_submitted(self, message):
        self.dismiss(message.value)


class TableApp(App):
    BINDINGS = [
        Binding("e", "edit_cell"),
        ("t", "toggle_cell", "blah"),
    ]
    # TODO search
    # TODO edit input
    # TODO edit bool
    # TODO patches? [id, key] = value
    # TODO error handling

    def __init__(self, client):
        super().__init__()
        self.client = client
        # self.columns = {}
        # self.entries = {}
        self.changes = {}

    def compose(self) -> ComposeResult:
        yield Table(self.client)

    @property
    def table(self):
        # idiomatic?
        return self.query_one(DataTable)

    def action_edit_cell(self):
        col_key = self.cursor_key.column_key
        row_key = self.cursor_key.row_key
        column = self.columns[col_key]
        if not column.editable:
            return
        current_value = self.entries[row_key][column.json_key]

        def on_dismiss(value):
            if value is None:
                return
            self.perform_edit(row_key, column, value)

        self.push_screen(InputScreen(current_value), on_dismiss)

    def perform_edit(self, row_key, column, value):
        self.client.edit_entry(row_key, {column.json_key: value})

    def on_mount(self) -> None:
        pass

# TODO async workers for client
# TODO httpx


if __name__ == "__main__":
    client = FxRelayClient(os.environ["FXRELAY_TOKEN"])

    app = TableApp(client)
    app.run()
