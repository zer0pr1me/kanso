from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from platformdirs import user_data_path
from sqlmodel import Session, SQLModel, create_engine, func, select

from kanso.core import position
from kanso.core.clock import Clock
from kanso.core.models import Meta, Record, Todo, TodoList

NAME_MAX = 200
TODO_FIELDS = {"text", "done", "scheduled_at"}


class NotFound(LookupError):
    pass


def default_path() -> Path:
    return user_data_path("kanso", ensure_exists=True) / "kanso.db"


def clean_name(name: str) -> str:
    name = name.strip()
    if not 0 < len(name) <= NAME_MAX:
        raise ValueError(f"Name must be 1-{NAME_MAX} characters")
    return name


def position_at(items: list[Record], index: int | None) -> str:
    """Key for inserting at `index` among ordered `items`; `None` (or past the end) appends."""
    index = len(items) if index is None else max(0, min(index, len(items)))
    before = items[index - 1].position if index else None
    after = items[index].position if index < len(items) else None
    return position.between(before, after)


class Store:
    """The local database and every operation on it. UIs talk only to this."""

    def __init__(self, path: Path | None = None):
        self.engine = create_engine(f"sqlite:///{path or default_path()}")
        SQLModel.metadata.create_all(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        stamps = [self.db.exec(select(func.max(m.modified_at))).one() for m in (TodoList, Todo)]
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
        records = [self._get(TodoList, list_id), *self.todos(list_id)]
        for record in records:
            record.deleted = True
        self._save(*records)

    # Todos

    def todos(self, list_id: UUID) -> list[Todo]:
        self._get(TodoList, list_id)
        return self._live(Todo, Todo.list_id == list_id)

    def add_todo(self, list_id: UUID, text: str = "", index: int | None = None, scheduled_at: date | None = None) -> Todo:
        """Insert at `index` in the list's order; `None` appends."""
        pos = position_at(self.todos(list_id), index)
        return self._save(Todo(list_id=list_id, text=text, scheduled_at=scheduled_at, position=pos))

    def update_todo(self, todo_id: UUID, **changes) -> Todo:
        """Set any of `text`, `done`, `scheduled_at`; toggling `done` sets `completed_at`."""
        if unknown := changes.keys() - TODO_FIELDS:
            raise TypeError(f"Cannot update {', '.join(sorted(unknown))}")
        todo = self._get(Todo, todo_id)
        if "done" in changes and changes["done"] != todo.done:
            todo.completed_at = date.today() if changes["done"] else None
        todo.sqlmodel_update(changes)
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
