import pytest

from kanso.core.merge import merge


def record(**fields) -> dict:
    """A todo stamped `<field>=(value, stamp)`; `modified_at` is the newest stamp."""
    stamps = {field: stamp for field, (_, stamp) in fields.items()}
    values = {field: value for field, (value, _) in fields.items()}
    return {"id": "t", **values, "stamps": stamps, "modified_at": max(stamps.values())}


def test_new_record_is_taken_as_is():
    remote = record(text=("a", "1"))
    assert merge(None, remote) == remote


def test_concurrent_edits_of_different_fields_both_survive():
    base = {"text": ("a", "1"), "done": (False, "1")}
    local = record(**{**base, "text": ("ours", "2-a")})
    remote = record(**{**base, "done": (True, "3-b")})
    assert merge(local, remote) == record(text=("ours", "2-a"), done=(True, "3-b"))
    assert merge(remote, local) == merge(local, remote)


@pytest.mark.parametrize("ours, theirs, winner", [("2-a", "2-b", "theirs"), ("2-b", "2-a", "ours"), ("3", "2", "ours")])
def test_same_field_takes_the_newer_stamp(ours, theirs, winner):
    merged = merge(record(text=("ours", ours)), record(text=("theirs", theirs)))
    assert (merged["text"], merged["stamps"]["text"]) == (winner, max(ours, theirs))


def test_deletion_survives_a_later_edit_of_another_field():
    local = record(text=("a", "1"), deleted=(True, "2"))
    remote = record(text=("b", "3"), deleted=(False, "1"))
    assert merge(local, remote) == record(text=("b", "3"), deleted=(True, "2"))


def test_unstamped_field_loses_to_a_stamped_one():
    local = {**record(text=("a", "1")), "labels": []}
    remote = record(text=("a", "1"), labels=(["x"], "1"))
    assert merge(local, remote)["labels"] == ["x"]


def test_records_must_be_the_same():
    with pytest.raises(ValueError):
        merge(record(text=("a", "1")), {**record(text=("a", "1")), "id": "other"})
