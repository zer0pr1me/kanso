"""Hybrid logical clock: timestamps that sort by wall time, yet never repeat or go backwards.

Stamps are fixed-width strings `<millis>-<counter>-<node>`, so plain string comparison
orders them, and the node id breaks ties between replicas for last-writer-wins merges.
"""

import time


class Clock:
    def __init__(self, node: str, last: str | None = None):
        self.node = node
        self.millis, self.counter = (int(part) for part in last.split("-")[:2]) if last else (0, 0)

    def now(self) -> str:
        millis = time.time_ns() // 1_000_000
        if millis > self.millis:
            self.millis, self.counter = millis, 0
        else:
            self.counter += 1
        return f"{self.millis:015d}-{self.counter:06d}-{self.node}"
