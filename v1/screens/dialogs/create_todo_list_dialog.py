from typing import Callable 

from blessed import Terminal

from screens.hotkeys import HotkeysMeta, hotkey
from screens.dialogs.dialog import Dialog
from screens.hotkeys import hotkey, unhandled_key_handler

CREATE_PROMPT_MSG = "Please enter name of new todolist:"

class CreateTodoListDialog(Dialog):
    def __init__(self, term: Terminal, width: int = 60, height: int = 20,
                 on_create: Callable[[str], None] = lambda name: None):
        super().__init__(term, width, height)
        self.on_create = on_create
        self.name = ""

    def _render_prompt_and_editbox(self):
        y = self.term.height // 2
        x = self.term.width // 2 - len(CREATE_PROMPT_MSG) // 2

        print(self.term.move_xy(x, y), end='')
        print(CREATE_PROMPT_MSG)
        print(self.term.move_xy(x, y+1), end='')

        box_len = len(CREATE_PROMPT_MSG)

        print(self.term.on_gray37 + self.term.limegreen, end='') 
        print(self.name + (box_len - len(self.name)) * ' ')
        print(self.term.normal)


    def render(self):
        super().render()
        self._render_prompt_and_editbox()


    @hotkey(name='KEY_ENTER')
    def create(self):
        self.on_create(self.name)
        self.closed = True


    @unhandled_key_handler()
    def key_handler(self, name: str, key: str, ctrl: bool, alt: bool):
        if name == 'KEY_BACKSPACE':
            self.name = self.name[:-1]
        else: 
            self.name += key
