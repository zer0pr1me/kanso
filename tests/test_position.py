import random

import pytest

from kanso.core.position import between


@pytest.mark.parametrize(
    "a, b",
    [(None, None), (None, "V"), ("V", None), ("V", "W"), ("V", "V1"), ("V0V", "V1"), (None, "01"), ("z", None), ("zz", None)],
)
def test_between_sorts_strictly_between(a, b):
    key = between(a, b)
    assert (a is None or a < key) and (b is None or key < b)
    assert not key.endswith("0")


@pytest.mark.parametrize("a, b", [("V", "V"), ("W", "V")])
def test_between_rejects_unordered(a, b):
    with pytest.raises(ValueError):
        between(a, b)


def test_repeated_appends_prepends_and_inserts_stay_ordered():
    keys = [between(None, None)]
    rng = random.Random(0)
    for _ in range(500):
        i = rng.randint(0, len(keys))
        keys.insert(i, between(keys[i - 1] if i else None, keys[i] if i < len(keys) else None))
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_appends_grow_slowly():
    key = None
    for _ in range(100):
        key = between(key, None)
    assert len(key) <= 20
