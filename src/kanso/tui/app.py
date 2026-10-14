from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class KansoApp(App):
    TITLE = "kanso"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Hello, kanso")
        yield Footer()


def main():
    KansoApp().run()


if __name__ == "__main__":
    main()
