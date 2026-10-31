"""Hybrid logical clock: timestamps that sort by wall time, yet never repeat or go backwards.

Stamps are fixed-width strings `<millis>-<counter>-<node>`, so plain string comparison
orders them, and the node id breaks ties between replicas for last-writer-wins merges.
"""

import time

MAX_DRIFT_MS = 60 * 60 * 1000


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

    def observe(self, stamp: str):
        """Move past `stamp`, received from another replica, so our next stamps sort after it.

        Refuses stamps over an hour ahead of our wall time: a replica with a wrong clock
        would otherwise drag every later stamp, everywhere, into its future.
        """
        millis, counter = (int(part) for part in stamp.split("-")[:2])
        if millis > time.time_ns() // 1_000_000 + MAX_DRIFT_MS:
            raise ValueError(f"Stamp {stamp} is over an hour ahead of this clock")
        self.millis, self.counter = max((self.millis, self.counter), (millis, counter))
