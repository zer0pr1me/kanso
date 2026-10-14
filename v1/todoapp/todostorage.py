from abc import ABC, abstractmethod

class TodoList(ABC):
    name: str

    @abstractmethod
    def save(self):
        pass

    @abstractmethod
    def load(self):
        pass
