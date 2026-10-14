from typing import Callable

from blessed import Terminal

from screens.dialogs.dialog import Dialog
from screens.hotkeys import hotkey

class ConfirmationDialog(Dialog):
    def __init__(self, term: Terminal, msg: str, width: int = 60, height: int = 20,
                 on_confirm: Callable[[], None] = lambda: None,
                 on_decline: Callable[[], None] = lambda: None):
        super().__init__(term, width, height)
        self.msg = msg
        self.yes_selected = False
        self.on_confirm = on_confirm
        self.on_decline = on_decline

    def _render_message(self):
        y = self.term.height // 2
        # TODO: word wrap
        x = self.term.width // 2 - len(self.msg) // 2

        print(self.term.move_xy(x, y), end='')
        print(self.msg)


    def _render_buttons(self):
        y = self.term.height // 2 + self.height // 2 - 5

        yes_x = self.term.width // 2 - self.width // 2 + 10 
        no_x = self.term.width // 2 + self.width // 2 - (10 + len("[ No ]"))

        if self.yes_selected:
            print(self.term.on_snow + self.term.black, end='')
        print(self.term.move_xy(yes_x, y) + '[ Yes ]')

        print(self.term.normal)
        if not self.yes_selected:
            print(self.term.on_snow + self.term.black, end='')
        print(self.term.move_xy(no_x, y) + '[ No ]')
        print(self.term.normal)

    def render(self):
        super().render()
        self._render_buttons()
        self._render_message()

    @hotkey(name='KEY_TAB')
    def toggle_choice(self):
        self.yes_selected = not self.yes_selected


    @hotkey(name='KEY_ENTER')
    def enter(self):
        if self.yes_selected:
            self.on_confirm()
        else:
            self.on_decline()

        self.closed = True

