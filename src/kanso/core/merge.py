"""Merging a replica's version of a record into ours: per field, the newer stamp wins.

Records are plain dicts of model fields with `stamps` decoded. Stamps are unique per write
(they include the node), so equal stamps mean the same write and the same value. A field
without a stamp, as one added by a migration, counts as older than any stamped one.
"""


def merge(local: dict | None, remote: dict) -> dict:
    """`local` with each field `remote` stamped newer taken from `remote`; `remote` if we had none."""
    if local is None:
        return remote
    if local["id"] != remote["id"]:
        raise ValueError(f"Cannot merge record {remote['id']} into {local['id']}")
    merged, stamps = dict(local), dict(local["stamps"])
    for field, stamp in remote["stamps"].items():
        if stamp > stamps.get(field, ""):
            merged[field], stamps[field] = remote[field], stamp
    return {**merged, "stamps": stamps, "modified_at": max(stamps.values(), default="")}
