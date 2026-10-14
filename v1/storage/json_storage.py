import json
from pathlib import Path

from model.todo import Todo
from storage.storage import Storage
from todoapp.project import Project

class JsonStorage(Storage):
    def __init__(self, name: str, dirname: str):
        super().__init__(name)
        self.dir = Path(dirname)
        self.todo_file = self.dir / 'todo-list.json'

    def save(self, project: Project):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.todo_file, 'w') as f:
            json.dump([todo.to_dict() for todo in project.todos], f, indent=4)

    def load(self) -> Project:
        # TODO: use Optional
        if not self.todo_file.exists():
            return Project(todos=[])
        with open(self.todo_file, 'r') as f:
            todos = [Todo.from_dict(todo)
                     for todo in json.load(f)]
            return Project(todos)
