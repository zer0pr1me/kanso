from blessed import Terminal

from screens.dialogs.dialog import Dialog
from screens.hotkeys import HotkeysMeta

class Screen(metaclass=HotkeysMeta):
    def __init__(self, term: Terminal):
        self.term = term
        self.mode = None
        self.dialog = None
        self._dbg_msg = ""
        self._transition_to = None


        return False

    def on_start(self):
        pass

    def render(self):
        pass

    def on_exit(self):
        pass

    def debug_msg(self, msg: str):
        self._dbg_msg = msg

    def run(self):
        with self.term.hidden_cursor(), self.term.cbreak():
            self.on_start()
            self.running = True

            try:
                while self.running and not self._transition_to:
                    print(self.term.home + self.term.clear)

                    if self.dialog:
                        if self.dialog.closed:
                            self.dialog = None
                            continue
                        self.dialog.render()
                        key = self.term.inkey()
                        self.dialog.handle_key(key.name, key.value, key._ctrl, key._alt)
                        continue

                    if self._dbg_msg != "":
                        print(self._dbg_msg)
                        self.term.inkey()
                        self._dbg_msg = ""
                        continue

                    self.render()
                    key = self.term.inkey()
                    self.handle_key(key.name, key.value, key._ctrl, key._alt)
            except Exception:
                self.on_exit()
                raise


            if not self._transition_to:
                print(self.term.clear, end='')
                self.on_exit()

        if self._transition_to:
            self._transition_to.run()

        if self.running:
            self._transition_to = None
            self.run()

    def exit(self):
        self.running = False

    def to_screen(self, screen: 'Screen'):
        self._transition_to = screen

    def show_dialog(self, dialog: Dialog):
        self.dialog = dialog
