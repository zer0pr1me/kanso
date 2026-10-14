from typing import List
from datetime import date

from blessed import Terminal

from model.todo import Todo, human_date
from screens.screen import Screen
from screens.hotkeys import hotkey, unhandled_key_handler
from screens.dialogs.confirmation_dialog import ConfirmationDialog
from storage.storage import Storage
from todoapp.project import Project

class TodoScreen(Screen):
    def __init__(self, term: Terminal, storage: Storage, project: Project):
        super().__init__(term)
        self.project = project
        self.storage = storage
        self.mode = 'normal'
        self.start_i = 0

        self.i = 0 # selected in filtered (visible) todo list

        self.edit_cursor = 0

        self.today_only = False
        self.index_map = dict()

    @property
    def selected_index(self) -> Todo:
        return self.index_map[self.i] if self.filtered_count > 0 else 0

    @property
    def selected_todo(self) -> Todo:
        return self.todos[self.selected_index]

    @property
    def filtered_count(self) -> int:
        return len(self.index_map)

    @property
    def visible_row_count(self) -> int:
        total_count = self.filtered_count
        max_visible = self.term.height
        if self.start_i != 0:
            max_visible -= 1
        if total_count - self.start_i >= max_visible:
            max_visible -= 1

        return min(total_count - self.start_i, max_visible) - 1

    @property
    def todos(self) -> List[Todo]:
        # TODO: isolate todos so that we can't set it directly
        return self.project.todos

    @property
    def filtered_todos(self) -> List[Todo]:
        return [self.todos[index] 
                for index in self.index_map]

    def _ensure_selected_is_seen(self):
        if self.i < self.start_i:
            self.start_i = self.i
        if self.i >= self.start_i + self.visible_row_count:
            self.start_i += self.i - (self.start_i + self.visible_row_count)

    def _start_edit(self):
        self.mode = 'edit'
        self.edit_cursor = len(self.selected_todo.text)

    def _apply_filters(self):
        if self.index_map:
            last_start_index = self.index_map[self.start_i]
            last_selected_index = self.selected_index
        if self.today_only:
            self.index_map = [i 
                              for i, todo in enumerate(self.todos)
                              if todo.scheduled_at == date.today()]
        else:
            self.index_map = list(range(self.project.todos_count))

        # set start index as close as possible to previous start
        if 'last_start_index' in locals():
            start_i_found = False
            i_found = False
            for i, index in enumerate(self.index_map):
                if not start_i_found and index >= last_start_index:
                    self.start_i = i
                    start_i_found = True
                if index >= last_selected_index:
                    self.i = i
                    i_found = True
                    break

            if not start_i_found:
                self.start_i = 0

            if not i_found:
                self.i = len(self.index_map) - 1

            self._ensure_selected_is_seen()


    @hotkey(key='n', ctrl = True)
    def swap_with_next(self):
        if self.filtered_count > self.i+1:
            self.project.swap(self.selected_index, self.index_map[self.i + 1])
            self.i += 1

    @hotkey(key='p', ctrl = True)
    def swap_with_prev(self):
         if self.i != 0:
            self.project.swap(self.selected_index, self.index_map[self.i - 1])
            self.i -= 1

    @hotkey(key='t', ctrl = True)
    def move_to_top(self):
        # TODO: fix hotkey
        if self.project.move_to_top(self.selected_index):
            self.i = 0

    @hotkey(key='e', ctrl = True)
    def move_to_bottom(self):
        # TODO: fix hotkey
        if self.project.move_to_bottom(self.selected_index):
            self.i = self.project.todos_count - 1

    @hotkey(key='s', alt = True)
    def sort_todo_list(self):
        self.project.sort()

    @hotkey(key='w', mode='normal')
    def schedule_to_today(self):
        self.project.schedule(self.selected_index, date=date.today())

    @hotkey(key='h', mode='normal')
    def move_schedule_prev(self):
        self.project.decrement_scheduled_at(self.selected_index)

    @hotkey(key='l', mode='normal')
    def move_schedule_next(self):
        self.project.increment_scheduled_at(self.selected_index)

    @hotkey(key='c', mode='normal')
    def copy_todo(self):
        if self.project.copy(self.selected_index):
            self._apply_filters()
            self.i += 1

    @hotkey(key='q', mode='normal')
    def quit(self):
        self.exit()

    @hotkey(key='j', mode='normal')
    def move_down(self):
        self.i = min(self.i + 1, self.filtered_count - 1)
        self._ensure_selected_is_seen()

    @hotkey(key='k', mode='normal')
    def move_up(self):
        self.i = max(self.i - 1, 0)
        self._ensure_selected_is_seen()

    @hotkey(key='t', mode='normal')
    def move_to_top(self):
        self.i = 0
        self._ensure_selected_is_seen()

    @hotkey(key='b', mode='normal')
    def move_to_bottom(self):
        self.i = self.filtered_count - 1
        self._ensure_selected_is_seen()

    @hotkey(key=' ', mode='normal')
    def toggle_todo(self):
        self.project.toggle(self.selected_index)

    @hotkey(key='N', mode='normal')
    def clear_scheduled(self):
        self.project.clear_scheduled(self.selected_index)

    # TODO: not working correctly, take a look
    # @hotkey(key='a', mode='normal')
    def add_todo_to_top(self):
        if self.project.insert_empty(0):
            self.i = 0
            self._start_edit()

    @hotkey(key='e', mode='normal')
    def edit_todo(self):
        self._start_edit()

    @hotkey(key='i', mode='normal')
    def insert_todo(self):
        if self.today_only:
            # TODO: think how you can make it work when filters are applied
            return
        if self.project.insert_empty(self.selected_index):
            self._apply_filters()
            if self.filtered_count != 1:
                self.i += 1
            self._start_edit()

    @hotkey(key='d', mode='normal')
    def delete_todo(self):
        def _delete():
            if self.project.delete(self.selected_index):
                self.i = min(self.i, self.filtered_count - 1)
            self._apply_filters()
        dialog = ConfirmationDialog(term=self.term,
                                    msg='Do you want to delete this TODO entry?',
                                    on_confirm=_delete)
        self.show_dialog(dialog)

    @hotkey(key='T', mode='normal')
    def toggle_show_today_only(self):
        # TODO:  generalize as filter
        self.today_only = not self.today_only
        self._apply_filters()

    @unhandled_key_handler()
    def handle_editing(self, name: str, key: str, ctrl: bool, alt: bool):
        if self.mode == 'edit':
            if name == 'KEY_ENTER' or name == 'KEY_ESCAPE' or (key == '[' and ctrl):
                self.mode = 'normal' 
            elif name == 'KEY_BACKSPACE':
                text = self.selected_todo.text
                self.selected_todo.text = text[:self.edit_cursor-1] + text[self.edit_cursor:]
                self.edit_cursor = max(0, self.edit_cursor - 1)
            elif key == 'b' and ctrl:
                self.edit_cursor = max(self.edit_cursor - 1, 0)
            elif key == 'f' and ctrl:
                self.edit_cursor = min(self.edit_cursor + 1, len(self.todos[self.selected_index].text))
            else: 
                if self.edit_cursor == len(self.selected_todo.text):
                    self.selected_todo.text += key
                else:
                    text = self.todos[self.selected_index].text 
                    self.selected_todo.text = text[:self.edit_cursor] + key + text[self.edit_cursor:]
                self.edit_cursor += 1

    def render(self):
        start_i = self.start_i
        if start_i != 0:
            print(" ... ")

        end_i = start_i + self.visible_row_count + 1

        for i, todo in enumerate(self.filtered_todos[start_i:end_i], start_i):
            if i == self.i:
                if self.mode == 'edit':
                    print(f'[{"x" if todo.done else " "}] {todo.text[:self.edit_cursor]}', end='')
                    # and cursor
                    if self.edit_cursor < len(todo.text):
                        print(self.term.on_snow + 
                              self.term.black + 
                              todo.text[self.edit_cursor], end='')
                        print(self.term.normal + todo.text[self.edit_cursor+1:])
                    else:
                        print(self.term.on_snow + self.term.black + ' ' + self.term.normal)
                    continue

                print(self.term.on_snow + self.term.black, end='')
            print(f'[{"x" if todo.done else " "}] {todo.text}', end='')

            if todo.done:
                todo_date = todo.completed_at
                date_color = self.term.green
            else:
                todo_date = todo.scheduled_at
                date_color = self.term.yellow
                if todo_date and todo_date < date.today():
                    date_color = self.term.orange
            if todo_date:
                print(date_color + f' {human_date(todo_date)}', end='')
            print(self.term.normal)

        if self.visible_row_count > end_i: 
            print(" ... ")


    def on_start(self):
        self.project = self.storage.load()
        self._apply_filters()


    def on_exit(self):
        self.storage.save(self.project)

