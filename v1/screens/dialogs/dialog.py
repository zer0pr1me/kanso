from blessed import Terminal

from screens.hotkeys import HotkeysMeta, hotkey

class Dialog(metaclass=HotkeysMeta):
    def __init__(self, term: Terminal, width: int = 60, height: int = 20):
        self.mode = 'dialog'
        self.term = term
        self.width = width
        self.height = height
        self.closed = False

    def _render_borders(self):
        start_x = self.term.width // 2 - self.width // 2
        end_x = self.term.width // 2 + self.width // 2
        start_y = self.term.height // 2 - self.height // 2
        end_y = self.term.height // 2 + self.height // 2

        print(self.term.move_xy(start_x, start_y), end='')
        print('+' + '-' * (self.width - 1) + '+')
        for y in range(start_y+1, end_y):
            print(self.term.move_xy(start_x, y), end='')
            print('|' + ' ' * (self.width - 1) + '|')

        print(self.term.move_xy(start_x, end_y), end='')
        print('+' + '-' * (self.width - 1) + '+')

    def render(self):
        self._render_borders()

    @hotkey(key='q')
    def close(self):
        self.closed = True
