#!/usr/bin/env python3

from dataclasses import dataclass
import datetime
from enum import Enum
import os

from requests import Session

from rich.text import Text
from rich.style import Style
from textual import log
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Select


DRY_RUN = True


@dataclass
class Column:
    label: str
    json_key: str | None
    editable: bool = False

    def format(self, entry) -> str:
        value = entry[self.json_key]
        return str(value)

    def sortkey(self, value):
        return value


class BoolColumn(Column):
    def format(self, entry) -> str:
        value = entry[self.json_key]
        if value:
            return "✅"
        else:
            return " "


class BlockColumn(Column):
    def format(self, entry) -> str:
        return block_enum_to_label(block_entry_to_enum(entry))


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
    texts = {
        Blocking.ALL: Text("⛔ All", style=Style(color="white", bgcolor="red")),
        Blocking.PROMOTIONS: Text("🗑️ Promotions", style=Style(color="black", bgcolor="#ff7700")),
        Blocking.NONE: Text("✅ None", style=Style(color="black", bgcolor="#33ff33")),
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
        return int(value)


class DateColumn(Column):
    def format(self, entry) -> str:
        value = entry[self.json_key]
        date = datetime.datetime.fromisoformat(value).astimezone()
        return date.strftime("%Y-%m-%d %H:%M %z")


COLS = [
    Column("Description", "description", editable=True),
    Column("E-mail address", "full_address"),
    IntColumn("ID", "id"),
    BlockColumn("Block?", None, editable=True),
    DateColumn("Created at", "created_at"),
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
        if DRY_RUN:
            log(changes)
            return

        response = self.session.patch(
            f"https://relay.firefox.com/api/v1/relayaddresses/{id}", json=changes,
        )
        response.raise_for_status()
        return response.json()

    def delete_entry(self, id):
        if DRY_RUN:
            return

        response = self.session.delete(f"https://relay.firefox.com/api/v1/relayaddresses/{id}")
        response.raise_for_status()
        return response.json()


class Table(DataTable):
    BINDINGS = [
        # ("T", "toggle_cell", "toogle"),
        Binding("(", "sort_asc_col"),
        Binding(")", "sort_desc_col"),
        Binding("ctrl+n", "new_row"),
        Binding("e", "edit_cell"),
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
        self.zebra_stripes = True

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
        for entry in self.entries.values():
            self._add_row(entry)

    def _add_row(self, entry):
        self.add_row(
            *(col.format(entry) for col in self._columns.values()), key=str(entry["id"])
        )

    def action_sort_asc_col(self):
        col_key = self.cursor_key.column_key
        self.sort(col_key, key=self._columns[col_key].sortkey)

    def action_sort_desc_col(self):
        col_key = self.cursor_key.column_key
        self.sort(col_key, key=self._columns[col_key].sortkey, reverse=True)

    def action_new_row(self):
        if DRY_RUN:
            entry = next(iter(self.entries.values()))
            entry["id"] = 42
        else:
            entry = self.client.new_entry()

        self.entries[str(entry["id"])] = entry
        self._add_row(entry)
        coord = self.get_cell_coordinate(str(entry["id"]), "0")
        self.move_cursor(row=coord.row, column=coord.column, animate=True)

    def action_delete_row(self):
        key = self.cursor_key.row_key
        if not DRY_RUN:
            self.client.delete_entry(key)
        self.table.remove_row(key)
        del self.entries[key]

    def _edit_cell(self, row_key, column):
        current_value = self.entries[row_key][column.json_key]

        def on_dismiss(value):
            if value is None:
                return
            self.perform_edit(row_key, column, value)

        self.app.push_screen(InputScreen(current_value), on_dismiss)

    def _edit_block(self, row_key, column):
        current_value = block_entry_to_enum(self.entries[row_key])

        def on_dismiss(value):
            if value is None:
                return
            print(value)
            #self.perform_edit(row_key, column, value)

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
        self.client.edit_entry(row_key, {column.json_key: value})


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
        Binding("escape", "cancel"),
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


class TableApp(App):
    BINDINGS = [
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

    def on_mount(self) -> None:
        pass

# TODO async workers for client
# TODO httpx


if __name__ == "__main__":
    client = FxRelayClient(os.environ["FXRELAY_TOKEN"])

    app = TableApp(client)
    app.run()
