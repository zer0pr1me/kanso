"""Schema migrations, written as transforms of plain row data.

The schema always comes from the current models; a migration only rewrites rows from the
previous version's shape into the next one's. Rows are `{table: [row, ...]}` with raw SQLite
values (str/int/float/None), the same shape that sync will send between replicas. So one
definition serves both: opening an old database dumps its rows, upgrades them and rebuilds
the tables, and a replica receiving rows from an older one upgrades them with `upgrade`.

Rules: append only, never edit a released migration; transform each row on its own, since
sync sends only changed rows; never touch the `meta` table.
"""

import json
import logging
from collections.abc import Callable

from sqlalchemy import Connection, Engine, inspect, text
from sqlmodel import SQLModel

type Rows = dict[str, list[dict]]


def add_todo_labels(rows: Rows) -> Rows:
    return {**rows, "todo": [{**row, "labels": "[]"} for row in rows.get("todo", [])]}


def add_todo_repeat_id(rows: Rows) -> Rows:
    return {**rows, "todo": [{**row, "repeat_id": None} for row in rows.get("todo", [])]}


def add_field_stamps(rows: Rows) -> Rows:
    """Stamp every field with the row's `modified_at`, the only stamp rows had."""

    def stamped(row: dict) -> dict:
        stamps = {field: row["modified_at"] for field in row if field not in {"id", "modified_at"}}
        return {**row, "stamps": json.dumps(stamps)}

    tables = ("todolist", "repeat", "todo")
    return {**rows, **{table: [stamped(row) for row in rows.get(table, [])] for table in tables}}


def add_devices(rows: Rows) -> Rows:
    """The `device` table is new and starts empty; no rows change."""
    return rows


def add_habits(rows: Rows) -> Rows:
    """The `habit` and `habitday` tables are new and start empty; no rows change."""
    return rows


def add_introductions(rows: Rows) -> Rows:
    """The `introduction` table is new and starts empty; no rows change."""
    return rows


MIGRATIONS: list[Callable[[Rows], Rows]] = [
    add_todo_labels,
    add_todo_repeat_id,
    add_field_stamps,
    add_devices,
    add_habits,
    add_introductions,
]

VERSION_KEY = "schema_version"

log = logging.getLogger(__name__)


def current_version() -> int:
    return len(MIGRATIONS)


def upgrade(rows: Rows, version: int) -> Rows:
    """Bring rows written by schema `version` up to the current one."""
    if version > current_version():
        raise ValueError(f"Data is from schema {version}, newer than this kanso ({current_version()}); update kanso")
    for migration in MIGRATIONS[version:]:
        rows = migration(rows)
    return rows


def migrate(engine: Engine):
    """Create the schema, or upgrade an existing database to it, atomically."""
    with engine.connect().execution_options(immediate=True) as conn, conn.begin():
        tables = inspect(conn).get_table_names()
        version = stored_version(conn) if "meta" in tables else 0 if tables else current_version()
        if version == current_version():
            SQLModel.metadata.create_all(conn)
        else:
            log.info("Upgrading the database from schema %d to %d", version, current_version())
            rows = upgrade(dump(conn, tables), version)
            for table in tables:
                conn.execute(text(f'DROP TABLE "{table}"'))
            SQLModel.metadata.create_all(conn)
            load(conn, rows)
        conn.execute(
            text("INSERT OR REPLACE INTO meta (key, value) VALUES (:key, :value)"),
            {"key": VERSION_KEY, "value": str(current_version())},
        )


def stored_version(conn: Connection) -> int:
    value = conn.execute(text("SELECT value FROM meta WHERE key = :key"), {"key": VERSION_KEY}).scalar()
    return int(value or 0)


def dump(conn: Connection, tables: list[str]) -> Rows:
    return {t: [dict(row._mapping) for row in conn.execute(text(f'SELECT * FROM "{t}"'))] for t in tables}


def load(conn: Connection, rows: Rows):
    if unknown := rows.keys() - SQLModel.metadata.tables.keys():
        raise ValueError(f"Rows for unknown tables: {', '.join(sorted(unknown))}")
    # Parents before children, so foreign keys hold.
    for table in SQLModel.metadata.sorted_tables:
        for row in rows.get(table.name, []):
            columns = ", ".join(f'"{c}"' for c in row)
            values = ", ".join(f":{c}" for c in row)
            conn.execute(text(f'INSERT INTO "{table.name}" ({columns}) VALUES ({values})'), row)
