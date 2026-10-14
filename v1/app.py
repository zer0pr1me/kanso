import blessed

from screens.menu_screen import MenuScreen

if __name__ == '__main__':
    term = blessed.Terminal()

    menu_screen = MenuScreen(term)
    menu_screen.run()

