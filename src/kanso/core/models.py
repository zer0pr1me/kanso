"""Tables are shaped for replicas that sync later.

Ids are UUIDs so replicas never collide, deletes are tombstones so they can propagate,
and `modified_at` is a hybrid-logical-clock stamp for last-writer-wins merges.
"""

from datetime import date
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class Meta(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class Record(SQLModel):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    position: str = Field(index=True)
    modified_at: str = ""
    deleted: bool = False


class TodoList(Record, table=True):
    name: str


class Todo(Record, table=True):
    list_id: UUID = Field(foreign_key="todolist.id", index=True)
    text: str = ""
    done: bool = False
    created_at: date = Field(default_factory=date.today)
    scheduled_at: date | None = None
    completed_at: date | None = None
