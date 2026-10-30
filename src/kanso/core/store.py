import os
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from platformdirs import user_data_path
from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine, func, select

from kanso.core import position
from kanso.core.clock import Clock
from kanso.core.migrations import migrate
from kanso.core.models import Meta, Record, Repeat, Todo, TodoList
from kanso.core.repeats import RULE_FIELDS, clean_rule, occurs_on

NAME_MAX = 200
LABEL_MAX = 50
TODO_FIELDS = {"text", "done", "scheduled_at", "labels"}


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


def apply_changes(todo: Todo, changes: dict) -> Todo:
    if unknown := changes.keys() - TODO_FIELDS:
        raise TypeError(f"Cannot set {', '.join(sorted(unknown))}")
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


def position_at(items: list[Record], index: int | None) -> str:
    """Key for inserting at `index` among ordered `items`; `None` (or past the end) appends."""
    index = len(items) if index is None else max(0, min(index, len(items)))
    before = items[index - 1].position if index else None
    after = items[index].position if index < len(items) else None
    return position.between(before, after)


class Store:
    """The local database and every operation on it. UIs talk only to this."""

    def __init__(self, path: Path | None = None):
        self.engine = connect(path or default_path())
        try:
            migrate(self.engine)
        except BaseException:
            self.engine.dispose()
            raise
        self.db = Session(self.engine, expire_on_commit=False)
        stamps = [self.db.exec(select(func.max(m.modified_at))).one() for m in (TodoList, Todo, Repeat)]
        self.clock = Clock(self._node_id(), max(filter(None, stamps), default=None))

    def close(self):
        self.db.close()
        self.engine.dispose()

    def _node_id(self) -> str:
        meta = self.db.get(Meta, "node") or Meta(key="node", value=uuid4().hex)
        self.db.add(meta)
        self.db.commit()
        return meta.value

    def _save[T: Record](self, *records: T) -> T:
        stamp = self.clock.now()
        for record in records:
            record.modified_at = stamp
            self.db.add(record)
        self.db.commit()
        return records[0]

    def _get[T: Record](self, model: type[T], id: UUID) -> T:
        record = self.db.get(model, id)
        if not record or record.deleted:
            raise NotFound(f"{model.__name__} {id} not found")
        return record

    def _live[T: Record](self, model: type[T], *where) -> list[T]:
        query = select(model).where(model.deleted == False, *where)  # noqa: E712
        return list(self.db.exec(query.order_by(model.position, model.id)).all())

    # Lists

    def lists(self) -> list[TodoList]:
        return self._live(TodoList)

    def add_list(self, name: str) -> TodoList:
        return self._save(TodoList(name=clean_name(name), position=position_at(self.lists(), None)))

    def rename_list(self, list_id: UUID, name: str) -> TodoList:
        todo_list = self._get(TodoList, list_id)
        todo_list.name = clean_name(name)
        return self._save(todo_list)

    def delete_list(self, list_id: UUID):
        records = [self._get(TodoList, list_id), *self.todos(list_id), *self.repeats(list_id)]
        for record in records:
            record.deleted = True
        self._save(*records)

    # Todos

    def todos(self, list_id: UUID) -> list[Todo]:
        self._get(TodoList, list_id)
        return self._live(Todo, Todo.list_id == list_id)

    def labels(self) -> list[str]:
        """Every label used by a live todo, sorted case-insensitively."""
        return sorted({label for todo in self._live(Todo) for label in todo.labels}, key=str.casefold)

    def add_todo(self, list_id: UUID, text: str = "", index: int | None = None, **fields) -> Todo:
        """Insert at `index` in the list's order (`None` appends); `fields` as in `update_todo`.

        New todos are scheduled for today unless `scheduled_at` says otherwise.
        """
        pos = position_at(self.todos(list_id), index)
        todo = Todo(list_id=list_id, text=text, position=pos, scheduled_at=date.today())
        return self._save(apply_changes(todo, fields))

    def update_todo(self, todo_id: UUID, **changes) -> Todo:
        """Set any of `text`, `done`, `scheduled_at`, `labels`; toggling `done` sets `completed_at`."""
        return self._save(apply_changes(self._get(Todo, todo_id), changes))

    def shift_todo(self, todo_id: UUID, days: int) -> Todo:
        """Move the scheduled date by `days`; an unscheduled todo counts as scheduled today."""
        todo = self._get(Todo, todo_id)
        todo.scheduled_at = (todo.scheduled_at or date.today()) + timedelta(days=days)
        return self._save(todo)

    def move_todo(self, todo_id: UUID, index: int) -> Todo:
        """Move to `index` in the list's order (as if the todo were removed first)."""
        todo = self._get(Todo, todo_id)
        todo.position = position_at([t for t in self.todos(todo.list_id) if t.id != todo.id], index)
        return self._save(todo)

    def delete_todo(self, todo_id: UUID):
        todo = self._get(Todo, todo_id)
        todo.deleted = True
        self._save(todo)

    # Repeats

    def repeats(self, list_id: UUID) -> list[Repeat]:
        self._get(TodoList, list_id)
        return self._live(Repeat, Repeat.list_id == list_id)

    def add_repeat(self, list_id: UUID, text: str, kind: str = "days", **rule) -> Repeat:
        """Append to the list's repeats; `rule` as in `repeats.clean_rule`."""
        pos = position_at(self.repeats(list_id), None)
        return self._save(Repeat(list_id=list_id, text=clean_text(text), position=pos, **clean_rule(kind, **rule)))

    def update_repeat(self, repeat_id: UUID, text: str | None = None, **rule) -> Repeat:
        """Set `text` and any rule fields; changing the rule restarts it today."""
        repeat = self._get(Repeat, repeat_id)
        changes = {} if text is None else {"text": clean_text(text)}
        current = {field: getattr(repeat, field) for field in RULE_FIELDS}
        if rule and (new := clean_rule(**{**current, **rule})) != current:
            changes |= {**new, "start": date.today()}
        repeat.sqlmodel_update(changes)
        return self._save(repeat)

    def delete_repeat(self, repeat_id: UUID):
        """Tombstone it; the todos it spawned stay."""
        repeat = self._get(Repeat, repeat_id)
        repeat.deleted = True
        self._save(repeat)

    def spawn_repeats(self, day: date | None = None) -> list[Todo]:
        """Add the todo of each repeat matching `day` (default today), appended to its list,
        unless that occurrence was already spawned, even if since deleted. Occurrence ids
        derive from the repeat and day, so replicas spawning the same one agree on it."""
        day = day or date.today()
        spawned = []
        for repeat in self._live(Repeat):
            id = uuid5(repeat.id, day.isoformat())
            if occurs_on(repeat, day) and not self.db.get(Todo, id):
                pos = position_at(self.todos(repeat.list_id), None)
                todo = Todo(id=id, list_id=repeat.list_id, repeat_id=repeat.id, text=repeat.text, position=pos, scheduled_at=day)
                spawned.append(self._save(todo))
        return spawned
