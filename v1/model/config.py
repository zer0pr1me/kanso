from typing import List, Dict
from dataclasses import dataclass

@dataclass
class TodolistConfig:
    storage_type: str
    name: str
    args: Dict[str, str]

@dataclass
class Config:
    todolists: List[TodolistConfig]
