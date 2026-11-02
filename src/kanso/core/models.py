"""Tables are shaped for replicas that sync later.

Ids are UUIDs so replicas never collide, and deletes are tombstones so they can propagate.
`stamps` holds a hybrid-logical-clock stamp per field, from the write that last changed it,
so replicas merge field by field, last writer wins (`merge.py`); `modified_at` is the newest.
"""

from datetime import date
from uuid import UUID, uuid4, uuid5

from sqlalchemy import JSON, Column
from sqlalchemy.orm import object_session
from sqlmodel import Field, SQLModel


class Meta(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class Record(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    position: str = Field(index=True)
    modified_at: str = Field(default="", index=True)
    stamps: dict[str, str] = Field(default_factory=dict, sa_type=JSON, nullable=False)
    deleted: bool = False


class TodoList(Record, table=True):
    name: str


class Scheduled(Record):
    """A template in a list that spawns an instance on each day its rule matches; see `repeats.py`."""

    list_id: UUID = Field(foreign_key="todolist.id", index=True)
    text: str
    kind: str = "days"
    interval: int = 1
    weekdays: list[int] = Field(default_factory=list, sa_type=JSON, nullable=False)
    monthdays: list[int] = Field(default_factory=list, sa_type=JSON, nullable=False)
    start: date = Field(default_factory=date.today)


class Repeat(Scheduled, table=True):
    """Spawns a todo on its days."""


class Habit(Scheduled, table=True):
    """Spawns a habit day on its days, from `start` on, missed ones included."""


class Todo(Record, table=True):
    """A repeating todo, one spawned by a repeat (`repeat_id`), is titled by the repeat, even
    a deleted one: its own `text` is a copy from spawning, shown only if the repeat is missing,
    as when sync delivers the todo first. Other todos are titled by their `text`."""

    list_id: UUID = Field(foreign_key="todolist.id", index=True)
    repeat_id: UUID | None = Field(default=None, foreign_key="repeat.id", index=True)
    text: str = ""
    done: bool = False
    created_at: date = Field(default_factory=date.today)
    scheduled_at: date | None = None
    completed_at: date | None = None
    labels: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))

    @property
    def title(self) -> str:
        session = object_session(self)
        repeat = session and self.repeat_id and session.get(Repeat, self.repeat_id)
        return repeat.text if repeat else self.text


class HabitDay(Record, table=True):
    """One day of a habit, unchecked until marked a `success` or `failure` once it's over. Its
    `day` never changes. Titled by its habit, even a deleted one; `text`, a copy from spawning,
    shows only if the habit is missing."""

    list_id: UUID = Field(foreign_key="todolist.id", index=True)
    habit_id: UUID = Field(foreign_key="habit.id", index=True)
    text: str
    day: date
    state: str | None = None

    @property
    def title(self) -> str:
        session = object_session(self)
        habit = session and session.get(Habit, self.habit_id)
        return habit.text if habit else self.text


class Device(Record, table=True):
    """A replica of this network: its node `key` (public key) is trusted to sync. The id
    derives from the key (`device_id`), so networks that both know a device merge its row."""

    key: str = Field(index=True)
    name: str


DEVICE_NAMESPACE = UUID("6f1c2a4e-9b0d-4f5e-8a37-2d9c1b7e4a60")


def device_id(key: str) -> UUID:
    return uuid5(DEVICE_NAMESPACE, key)


SYNCED_MODELS = (TodoList, Repeat, Habit, Todo, HabitDay, Device)
"""The tables replicas exchange, parents first."""

UNSTAMPED = {"id", "modified_at", "stamps"}
"""Fields without a stamp of their own: the immutable id, and the stamps themselves."""
