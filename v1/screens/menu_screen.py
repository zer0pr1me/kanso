from dataclasses import asdict
import json

from model.config import Config, TodolistConfig
from screens.dialogs.create_todo_list_dialog import CreateTodoListDialog
from screens.hotkeys import hotkey
from screens.screen import Screen
from screens.todo_screen import TodoScreen
from storage.config_storage import ConfigStorage
from storage.json_storage import JsonStorage
from storage.storage import Storage 
from todoapp.project import Project
from globals import CONFIG_FILE

from blessed import Terminal
from dacite import from_dict


class MenuScreen(Screen):
    def __init__(self, term: Terminal):
        # TODO: abstract out both screens to PickerScreen
        super().__init__(term)
        self.i = 0

    def save(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(asdict(self.config), f, indent=4)

    def render(self):
        print("TODOLISTS")
        print("==========================")
        print()
        for i, todolist in enumerate(self.config.todolists):
            if i == self.i:
                print(self.term.on_snow + self.term.black, end='')
            print(self.term.move_xy(0, i+3), end='')
            print(self.term.orangered + f'[{todolist.storage_type}] ' + self.term.normal, end='')
            if i == self.i:
                print(self.term.on_snow + self.term.black, end='')
            print(f'{todolist.name}', end='')
            if todolist.storage_type == 'json':
                print(f'=> {todolist.args["dir"]}', end='')
            print()
            print(self.term.normal)

    def on_start(self):
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                self.config = from_dict(data_class=Config, data=json.load(f))

        else:
            self.config = Config(todolists = [TodolistConfig(storage_type='json',
                                                             name='cwd', 
                                                             args={
                                                                'dir': '.',
                                                             })])


    def on_exit(self):
        self.save()

    @hotkey(key='j')
    def move_down(self):
        self.i = min(self.i+1, len(self.config.todolists)-1)

    @hotkey(key='k')
    def move_up(self):
        self.i = max(self.i - 1, 0)

    @hotkey(key='q')
    def quit(self):
        self.exit()

    @hotkey(name='KEY_ENTER')
    @hotkey(key='o')
    def open_todolist(self):
        tl_data = self.config.todolists[self.i]
        if tl_data.storage_type == 'json':
            storage = JsonStorage(tl_data.name, tl_data.args['dir'])
        elif tl_data.storage_type == 'config':
            storage = ConfigStorage(tl_data.name)
        else:
            raise Exception(f'Unknown todo list storage type: {tl_data.storage_type}')

        project = Project(storage.load())
        self.to_screen(TodoScreen(self.term, 
                                  storage=storage,
                                  project=project))

    
    @hotkey(key='c')
    def create_todolist(self):
        def _create(name: str):
            # TODO: other types of storage
            self.config.todolists.append(TodolistConfig(storage_type='config',
                                                        name=name,
                                                        args={}))
            self.save()
        dialog = CreateTodoListDialog(term=self.term, on_create=_create)

        self.show_dialog(dialog)
