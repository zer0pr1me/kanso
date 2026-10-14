from platformdirs import user_config_dir
import json
from pathlib import Path

from model.todo import Todo
from storage.json_storage import JsonStorage
from globals import CONFIG_DIR

class ConfigStorage(JsonStorage):
    def __init__(self, name: str):
        super().__init__(name, CONFIG_DIR / name)
