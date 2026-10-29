"""People: who is speaking, and what is theirs (voice_labs/people.py)."""

from __future__ import annotations

import pytest

from voice_labs.people import clean, mine, names, person_name, speaking

PEOPLE = {"Mike": {"todo_list": "todo.mike", "playlist": "Mike's Mix"}, "Anna": {}}


def test_everyone():
    # With things of their own, a voice, or both: once each.
    assert names(PEOPLE, ["mike", "Sam"]) == ["Anna", "Mike", "Sam"]
    assert names({}, []) == []


def test_who_is_speaking():
    # Their voice first; then whose wake word it was; then the only person.
    mike = speaking(PEOPLE, ["Mike", "Anna"], "Mike", 0.61, wake_owner="Anna")
    assert (mike.name, mike.by, mike.score) == ("Mike", "voice", 0.61)
    assert mike.as_dict() == {"name": "Mike", "score": 0.61, "by": "voice", "todo_list": "todo.mike", "calendar": None, "playlist": "Mike's Mix"}
    anna = speaking(PEOPLE, ["Mike", "Anna"], None, None, wake_owner="anna")
    assert (anna.name, anna.by) == ("Anna", "wake_word")
    assert speaking(PEOPLE, ["Mike", "Anna"], wake_owner="Nobody").name is None
    alone = speaking({"Mike": {"calendar": "calendar.mike"}}, [])
    assert (alone.name, alone.by, alone.own) == ("Mike", "only_person", {"calendar": "calendar.mike"})
    # Unless a voice was heard, and it wasn't theirs.
    assert speaking({"Mike": {}}, ["Mike"], refused=True).name is None
    assert speaking({"Mike": {}}, ["Mike"], refused=True, wake_owner="Mike").by == "wake_word"
    # Nobody in the home: nobody speaking.
    assert speaking({}, []).as_dict()["name"] is None


def test_my_things():
    mike = {"name": "Mike"}
    # Nobody has a calendar of their own: the command's own, whoever speaks.
    assert mine("calendar", None, PEOPLE) == (True, {})
    # Someone has a to-do list: theirs, the command's own for someone without,
    # and nothing for nobody known.
    assert mine("todo_list", mike, PEOPLE) == (True, {"entity": ["todo.mike"]})
    assert mine("todo_list", {"name": "anna"}, PEOPLE) == (True, {})
    assert mine("todo_list", {"name": None}, PEOPLE) == (False, {})
    assert mine("playlist", mike, PEOPLE) == (True, {"query": "Mike's Mix", "media_type": "playlist"})


def test_clean():
    assert clean({"todo_list": " todo.mike ", "calendar": "", "tts": "tts.cloud"}) == {"todo_list": "todo.mike", "tts": "tts.cloud"}
    with pytest.raises(ValueError, match="todo_list: a todo entity"):
        clean({"todo_list": "calendar.mike"})
    with pytest.raises(ValueError, match="unknown: color"):
        clean({"color": "red"})
    assert person_name("  mike o'brien! ") == "Mike O'brien" and person_name("") is None
