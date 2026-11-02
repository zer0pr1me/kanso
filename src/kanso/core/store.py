import json
import os
from collections.abc import Callable
from datetime import date, timedelta
from itertools import takewhile
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from platformdirs import user_data_path
from sqlalchemy import Engine, event, inspect
from sqlalchemy import text
from sqlmodel import Session, create_engine, func, select

from kanso.core import position
from kanso.core.clock import Clock
from kanso.core.merge import merge
from kanso.core.migrations import Rows, migrate, upgrade
from kanso.core.models import SYNCED_MODELS, UNSTAMPED, Device, Meta, Record, Repeat, Todo, TodoList, device_id
from kanso.core.repeats import RULE_FIELDS, clean_rule, occurs_on
from kanso.core.sync import Digest, decode, encode, newest_stamp, stamps_hash

NAME_MAX = 200
LABEL_MAX = 50
TODO_FIELDS = {"text", "done", "scheduled_at", "labels"}
REPEAT_FIELDS = {"text"} | RULE_FIELDS


class NotFound(LookupError):
    pass


def default_path() -> Path:
    """`kanso.db` in `$KANSO_DATA_DIR` if set, else in the user data directory; creates the directory."""
    env = os.environ.get("KANSO_DATA_DIR")
    data_dir = Path(env).expanduser() if env else user_data_path("kanso")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "kanso.db"


def connect(path: Path) -> Engine:
    """SQLite engine whose transactions also cover DDL, so migrations are atomic.

    pysqlite otherwise commits before DDL statements; this is SQLAlchemy's documented fix.
    """
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def disable_pysqlite_transactions(dbapi_connection, _):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin(conn):
        conn.exec_driver_sql("BEGIN")

    return engine


def check_fields(changes: dict, allowed: set[str]):
    if unknown := changes.keys() - allowed:
        raise TypeError(f"Cannot set {', '.join(sorted(unknown))}")


def apply_changes(todo: Todo, changes: dict) -> Todo:
    check_fields(changes, TODO_FIELDS)
    if "text" in changes:
        changes = {**changes, "text": clean_text(changes["text"])}
    if "labels" in changes:
        changes = {**changes, "labels": clean_labels(changes["labels"])}
    if "done" in changes and changes["done"] != todo.done:
        todo.completed_at = date.today() if changes["done"] else None
    todo.sqlmodel_update(changes)
    return todo


def clean_name(name: str) -> str:
    name = name.strip()
    if not 0 < len(name) <= NAME_MAX:
        raise ValueError(f"Name must be 1-{NAME_MAX} characters")
    return name


def clean_text(text: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Text must not be blank")
    return text


def clean_labels(labels: list[str]) -> list[str]:
    """Stripped of whitespace and a leading `#`, blanks and repeats dropped, in order."""
    if isinstance(labels, str):
        raise TypeError("Labels must be a list of strings")
    cleaned = []
    for label in labels:
        label = label.strip().removeprefix("#")
        if len(label) > LABEL_MAX or any(c.isspace() for c in label):
            raise ValueError(f"Labels must be at most {LABEL_MAX} characters, without spaces")
        if label and label not in cleaned:
            cleaned.append(label)
    return cleaned


def changed_fields(record: Record) -> set[str]:
    """Fields set to a new value since the record was last saved; all of them if it never was."""
    state = inspect(record)
    return {a.key for a in state.attrs if not state.persistent or a.history.has_changes()} - UNSTAMPED


class Store:
    """The local database and every operation on it. UIs talk only to this."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.engine = connect(self.path)
        try:
            migrate(self.engine)
        except BaseException:
            self.engine.dispose()
            raise
        # No autoflush: a query between changing a record and `_save` would write the change
        # unstamped and hide it from `changed_fields`.
        self.db = Session(self.engine, expire_on_commit=False, autoflush=False)
        self.clock = Clock(self.meta("node", lambda: uuid4().hex), self.last_change())

    def last_change(self) -> str | None:
        """The newest stamp of any synced row, written here or merged from elsewhere."""
        stamps = [self.db.exec(select(func.max(m.modified_at))).one() for m in SYNCED_MODELS]
        return max(filter(None, stamps), default=None)

    def close(self):
        self.db.close()
        self.engine.dispose()

    def meta(self, key: str, make: Callable[[], str] | None = None) -> str | None:
        """This replica's own setting `key`, never synced; if unset, set to `make()` or `None`."""
        if not (meta := self.db.get(Meta, key)):
            if not make:
                return None
            meta = Meta(key=key, value=make())
            self.db.add(meta)
            self.db.commit()
        return meta.value

    def set_meta(self, key: str, value: str | None):
        """Set this replica's own setting `key`, or remove it if `value` is `None`."""
        if value is None:
            if meta := self.db.get(Meta, key):
                self.db.delete(meta)
        else:
            self.db.merge(Meta(key=key, value=value))
        self.db.commit()

    def _save[T: Record](self, *records: T) -> T:
        """Stamp each record's changed fields with one new stamp and commit; unchanged records aren't written."""
        stamp = self.clock.now()
        for record in records:
            if changed := changed_fields(record):
                record.stamps = {**record.stamps, **dict.fromkeys(changed, stamp)}
                record.modified_at = stamp
                self.db.add(record)
        self.db.commit()
        return records[0]

    # A record is live unless deleted or in a list that is deleted or missing, which happens
    # when replicas concurrently delete a list and add to it, or sync delivers a child first.

    def _is_live(self, record: Record | None) -> bool:
        if not record or record.deleted:
            return False
        list_id = getattr(record, "list_id", None)
        return list_id is None or self._is_live(self.db.get(TodoList, list_id))

    def _position_at(self, items: list[Record], index: int | None) -> str:
        """Key for inserting at `index` among ordered `items`; `None` (or past the end) appends.

        Replicas pick keys independently, so neighbours can share one, leaving no key between
        them; then the tied items after the gap get new keys first, keeping their order.
        """
        index = len(items) if index is None else max(0, min(index, len(items)))
        before = items[index - 1].position if index else None
        after = items[index:]
        if before is None or not after or after[0].position > before:
            return position.between(before, after[0].position if after else None)
        tied = list(takewhile(lambda r: r.position <= before, after))
        limit = after[len(tied)].position if len(tied) < len(after) else None
        key = position.between(before, limit)
        last = key
        for record in tied:
            record.position = last = position.between(last, limit)
        self._save(*tied)
        return key

    def _get[T: Record](self, model: type[T], id: UUID) -> T:
        record = self.db.get(model, id)
        if not self._is_live(record):
            raise NotFound(f"{model.__name__} {id} not found")
        return record

    def _live[T: Record](self, model: type[T], *where) -> list[T]:
        query = select(model).where(model.deleted == False, *where)  # noqa: E712
        if hasattr(model, "list_id"):
            query = query.join(TodoList, TodoList.id == model.list_id).where(TodoList.deleted == False)  # noqa: E712
        return list(self.db.exec(query.order_by(model.position, model.id)).all())

    def _in_list[T: Record](self, model: type[T], list_id: UUID) -> list[T]:
        self._get(TodoList, list_id)
        return self._live(model, model.list_id == list_id)

    # Lists

    def lists(self) -> list[TodoList]:
        return self._live(TodoList)

    def add_list(self, name: str) -> TodoList:
        return self._save(TodoList(name=clean_name(name), position=self._position_at(self.lists(), None)))

    def rename_list(self, list_id: UUID, name: str) -> TodoList:
        todo_list = self._get(TodoList, list_id)
        todo_list.name = clean_name(name)
        return self._save(todo_list)

    def delete_list(self, list_id: UUID):
        """Tombstone it, which also hides its todos and repeats."""
        todo_list = self._get(TodoList, list_id)
        todo_list.deleted = True
        self._save(todo_list)

    # Todos

    def todos(self, list_id: UUID) -> list[Todo]:
        return self._in_list(Todo, list_id)

    def labels(self) -> list[str]:
        """Every label used by a live todo, sorted case-insensitively."""
        return sorted({label for todo in self._live(Todo) for label in todo.labels}, key=str.casefold)

    def add_todo(self, list_id: UUID, text: str, index: int | None = None, **fields) -> Todo:
        """Insert at `index` in the list's order (`None` appends); `fields` as in `update_todo`.

        New todos are scheduled for today unless `scheduled_at` says otherwise.
        """
        pos = self._position_at(self.todos(list_id), index)
        todo = Todo(list_id=list_id, position=pos, scheduled_at=date.today())
        return self._save(apply_changes(todo, {"text": text, **fields}))

    def update_todo(self, todo_id: UUID, **changes) -> Todo:
        """Set any of `text` (not blank), `done`, `scheduled_at`, `labels`; toggling `done` sets
        `completed_at` and moves it to the end of the list if done, else to its start.
        A repeating todo is titled by its repeat, so setting its `text` raises `ValueError` while the
        repeat is live, and otherwise makes it an ordinary todo."""
        todo = self._get(Todo, todo_id)
        if "text" in changes and self.repeat_of(todo_id):
            raise ValueError("Edit its repeat instead")
        toggled = "done" in changes and changes["done"] != todo.done
        apply_changes(todo, changes)
        if "text" in changes:
            todo.repeat_id = None
        if toggled:
            # Shown done first, then open: a newly done todo goes last of the done, a reopened one first of the open.
            others = [t for t in self.todos(todo.list_id) if t.id != todo.id]
            todo.position = self._position_at(others, None if todo.done else 0)
        return self._save(todo)

    def duplicate_todo(self, todo_id: UUID) -> Todo:
        """Insert a copy below it with its text, labels and date, not done. A repeating todo,
        even one of a deleted repeat, is an occurrence of its repeat, so copying one raises `ValueError`."""
        todo = self._get(Todo, todo_id)
        if todo.repeat_id:
            raise ValueError("Repeating todos can't be duplicated")
        index = self.todos(todo.list_id).index(todo) + 1
        return self.add_todo(todo.list_id, todo.text, index, scheduled_at=todo.scheduled_at, labels=todo.labels)

    def shift_todo(self, todo_id: UUID, days: int) -> Todo:
        """Move the scheduled date by `days`; an unscheduled todo counts as scheduled today."""
        todo = self._get(Todo, todo_id)
        todo.scheduled_at = (todo.scheduled_at or date.today()) + timedelta(days=days)
        return self._save(todo)

    def move_todo(self, todo_id: UUID, index: int) -> Todo:
        """Move to `index` in the list's order (as if the todo were removed first)."""
        todo = self._get(Todo, todo_id)
        todo.position = self._position_at([t for t in self.todos(todo.list_id) if t.id != todo.id], index)
        return self._save(todo)

    def delete_todo(self, todo_id: UUID):
        todo = self._get(Todo, todo_id)
        todo.deleted = True
        self._save(todo)

    # Repeats

    def repeats(self, list_id: UUID) -> list[Repeat]:
        return self._in_list(Repeat, list_id)

    def add_repeat(self, list_id: UUID, text: str, kind: str = "days", **rule) -> Repeat:
        """Append to the list's repeats, spawning today's todo if due; `rule` as in `repeats.clean_rule`."""
        pos = self._position_at(self.repeats(list_id), None)
        repeat = self._save(Repeat(list_id=list_id, text=clean_text(text), position=pos, **clean_rule(kind, **rule)))
        self._spawn(repeat, date.today())
        return repeat

    def update_repeat(self, repeat_id: UUID, **changes) -> Repeat:
        """Set `text` (which titles its todos too) and any rule fields, spawning today's todo if
        now due; changing the rule restarts it today."""
        check_fields(changes, REPEAT_FIELDS)
        repeat = self._get(Repeat, repeat_id)
        updates = {"text": clean_text(changes.pop("text"))} if "text" in changes else {}
        current = {field: getattr(repeat, field) for field in RULE_FIELDS}
        if (rule := clean_rule(**{**current, **changes})) != current:
            updates |= {**rule, "start": date.today()}
        repeat.sqlmodel_update(updates)
        self._save(repeat)
        self._spawn(repeat, date.today())
        return repeat

    def delete_repeat(self, repeat_id: UUID):
        """Tombstone it; the todos it spawned stay."""
        repeat = self._get(Repeat, repeat_id)
        repeat.deleted = True
        self._save(repeat)

    def repeat_of(self, todo_id: UUID) -> Repeat | None:
        """The live repeat that spawned the todo, if any."""
        todo = self._get(Todo, todo_id)
        repeat = todo.repeat_id and self.db.get(Repeat, todo.repeat_id)
        return repeat if self._is_live(repeat) else None

    def spawn_repeats(self, day: date | None = None) -> list[Todo]:
        """Add the todo of each repeat matching `day` (default today), appended to its list,
        unless that occurrence was already spawned, even if since deleted. Occurrence ids
        derive from the repeat and day, so replicas spawning the same one agree on it."""
        day = day or date.today()
        return [todo for repeat in self._live(Repeat) if (todo := self._spawn(repeat, day))]

    def _spawn(self, repeat: Repeat, day: date) -> Todo | None:
        id = uuid5(repeat.id, day.isoformat())
        if not occurs_on(repeat, day) or self.db.get(Todo, id):
            return None
        pos = self._position_at(self.todos(repeat.list_id), None)
        return self._save(Todo(id=id, list_id=repeat.list_id, repeat_id=repeat.id, text=repeat.text, position=pos, scheduled_at=day))

    # Devices

    def devices(self) -> list[Device]:
        return self._live(Device)

    def is_trusted(self, key: str) -> bool:
        """Whether `key` belongs to a device of this network."""
        return self._is_live(self.db.get(Device, device_id(key)))

    def add_device(self, key: str, name: str) -> Device:
        """Trust `key` (again, if it was removed), named `name`."""
        device = self.db.get(Device, device_id(key)) or Device(id=device_id(key), key=key, position="")
        device.sqlmodel_update({"name": clean_name(name), "deleted": False})
        if not device.position:
            device.position = self._position_at(self.devices(), None)
        return self._save(device)

    # Sync: raw rows in and out (see `sync.py`)

    def _tables(self):
        return [(model, model.__tablename__) for model in SYNCED_MODELS]

    def digest(self) -> Digest:
        """Every synced row's id and stamps hash, deleted ones too, by table."""
        conn = self.db.connection()
        return {
            table: {id: stamps_hash(stamps) for id, stamps in conn.execute(text(f'SELECT id, stamps FROM "{table}"'))}
            for _, table in self._tables()
        }

    def export(self, ids: dict[str, list[str]]) -> Rows:
        """The rows with `ids`, by table, as raw rows."""
        conn = self.db.connection()
        query = 'SELECT * FROM "{}" WHERE id IN (SELECT value FROM json_each(:ids))'
        return {
            table: [dict(row._mapping) for row in conn.execute(text(query.format(table)), {"ids": json.dumps(ids[table])})]
            for _, table in self._tables()
            if ids.get(table)
        }

    def merge_rows(self, rows: Rows, version: int) -> int:
        """Merge raw rows another replica sent in schema `version`, field by field; returns
        how many rows changed. All or nothing: raises `ValueError` for a newer schema,
        unknown tables or columns, malformed stamps, or stamps too far in the future."""
        rows = upgrade(rows, version)
        tables = dict((table, model) for model, table in self._tables())
        if unknown := rows.keys() - tables.keys():
            raise ValueError(f"Rows for unknown tables: {', '.join(sorted(unknown))}")
        if newest := newest_stamp(rows):
            self.clock.observe(newest)
        conn = self.db.connection()
        changed = 0
        try:
            for table, model in tables.items():
                columns = set(model.__table__.columns.keys())
                for row in rows.get(table, []):
                    if set(row) != columns:
                        raise ValueError(f"Row {row.get('id')} of {table} doesn't match its columns")
                    local = conn.execute(text(f'SELECT * FROM "{table}" WHERE id = :id'), {"id": row["id"]}).first()
                    local = decode(dict(local._mapping)) if local else None
                    if (merged := merge(local, decode(row))) != local:
                        names = ", ".join(f'"{c}"' for c in merged)
                        values = ", ".join(f":{c}" for c in merged)
                        conn.execute(text(f'INSERT OR REPLACE INTO "{table}" ({names}) VALUES ({values})'), encode(merged))
                        changed += 1
        except BaseException:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.expire_all()
        return changed
