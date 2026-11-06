import sys

from textual_serve.server import Server


def main():
    Server(f"{sys.executable} -m kanso", port=8080).serve()
