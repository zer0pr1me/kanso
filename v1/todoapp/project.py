import dataclasses
from typing import List
from datetime import date, timedelta, datetime

from model.todo import Todo

def modification(func):
    def wrapper(*args, **kwargs):
        self, i = args[:2]
        result = func(*args, **kwargs)
        self.todos[i].modified_at = datetime.now()
        return result
    return wrapper

class Project:
    def __init__(self, todos: List[Todo]):
        self.todos = todos

    @property
    def todos_count(self) -> int:
        return len(self.todos)

    def swap(self, i: int, j: int) -> bool:
        if i < 0 or j < 0:
            return False
        if i >= len(self.todos) or j >= len(self.todos):
            return False
        self.todos[i], self.todos[j] = self.todos[j], self.todos[i]
        return True

    def move(self, old_pos: int, new_pos: int) -> bool:
        if new_pos < len(self.todos) or old_pos < len(self.todos):
            return False
        item = self.todos[old_pos]
        self.todos = self.todos[:old_pos] + self.todos[old_pos+1:]
        self.todos = self.todos[:new_pos] + [item] + self.todos[new_pos:]
        return True

    def sort(self):
        def _key(todo: Todo) -> int: 
            score = 0
            if todo.done:
                score -= 1_000_000

            if todo.done:
                if todo.completed_at:
                    score += (todo.completed_at - date.today()).days
                else:
                    score -= 1_000_000
            else:
                score += 1_000_000
                if todo.scheduled_at:
                    score += (todo.scheduled_at - date.today()).days

            return score

        self.todos = sorted(self.todos, key=_key)

    @modification
    def clear_scheduled(self, i: int):
        self.todos[i].scheduled_at = None

    @modification
    def schedule(self, i: int, date: date):
        # TODO: use separate function for setting completion
        if self.todos[i].done:
            self.todos[i].completed_at = date
        else:
            self.todos[i].scheduled_at = date

    def insert_empty(self, i: int) -> bool:
        # TODO: bounds decorator?
        if i < 0 and i >= len(self.todos):
            return False

        self.todos = self.todos[:i+1] + [Todo('', False)] + self.todos[i+1:]
        return True


    def delete(self, i: int) -> bool:
        if i < 0 and i >= len(self.todos):
            return False
        self.todos = self.todos[:i] + self.todos[i+1:]
        return True

    def copy(self, i: int) -> bool:
        if i >= len(self.todos) or i < 0:
            return False
        copy = dataclasses.replace(self.todos[i], 
                                   done=False, 
                                   created_at=date.today())

        self.todos = self.todos[:i+1] + [copy] + self.todos[i+1:]

        return True

    def swap_with_next(self, i: int) -> bool:
        return self.swap(i, i+1)

    def swap_with_prev(self, i: int) -> bool:
        return self.swap(i, i-1)

    def move_to_top(self, i: int) -> bool:
        return self.project.move(old_pos=i, new_pos=0)

    def move_to_bottom(self, i: int) -> bool:
        return self.project.move(old_pos=self.i, new_pos=len(self.project.todos) - 1)

    @modification
    def increment_scheduled_at(self, i: int):
        if self.todos[i].done:
            if self.todos[i].completed_at is None:
                self.todos[i].completed_at = date.today()
            self.todos[i].completed_at += timedelta(days=1)
        else:
            if self.todos[i].scheduled_at is None:
                self.schedule(i, date=date.today())
            self.todos[i].scheduled_at += timedelta(days=1)

    @modification
    def decrement_scheduled_at(self, i: int):
        if self.todos[i].done:
            if self.todos[i].completed_at is None:
                self.todos[i].completed_at = date.today()
            self.todos[i].completed_at -= timedelta(days=1)
        else:
            if self.todos[i].scheduled_at is None:
                self.schedule(i, date=date.today())
            self.todos[i].scheduled_at -= timedelta(days=1)

    @modification
    def toggle(self, i: int):
        done = not self.todos[i].done
        self.todos[i].done = done 
        self.todos[i].completed_at = date.today() if done else None

