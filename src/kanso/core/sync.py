"""Raw rows as replicas exchange them, and what each side must send the other.

Rows have the shape migrations use: `{table: [row, ...]}` with raw SQLite values and
`stamps` as JSON text. A digest maps each row id to a hash of its stamps, so two replicas
find which rows differ without sending them; `modified_at` wouldn't do, as rows can share
their newest stamp yet differ in older fields.
"""

import hashlib
import json

from kanso.core.migrations import Rows

type Digest = dict[str, dict[str, str]]


def stamps_hash(stamps: str) -> str:
    canonical = json.dumps(json.loads(stamps), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def wanted(mine: Digest, theirs: Digest) -> dict[str, list[str]]:
    """Ids of our rows the other side lacks or has otherwise, by table."""
    return {
        table: ids
        for table, hashes in mine.items()
        if (ids := [id for id, h in hashes.items() if theirs.get(table, {}).get(id) != h])
    }


def decode(row: dict) -> dict:
    stamps = json.loads(row["stamps"])
    if not isinstance(stamps, dict) or not all(isinstance(s, str) for s in stamps.values()):
        raise ValueError(f"Row {row['id']} has malformed stamps")
    return {**row, "stamps": stamps}


def encode(row: dict) -> dict:
    return {**row, "stamps": json.dumps(row["stamps"], sort_keys=True)}


def newest_stamp(rows: Rows) -> str | None:
    return max((s for table in rows.values() for row in table for s in decode(row)["stamps"].values()), default=None)
