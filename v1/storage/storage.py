from abc import ABC, abstractmethod

from todoapp.project import Project

class Storage(ABC):
    def __init__(self, name: str):
        self.todos = []
        self.name = name

    @abstractmethod
    def save(self, project: Project):
        pass

    @abstractmethod
    def load(self) -> Project:
        pass


