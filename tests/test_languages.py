"""Languages: the packs, and what the app takes from its own."""

from __future__ import annotations

import importlib.util
import inspect
import re
from dataclasses import replace
from pathlib import Path

import pytest

from adapt_ctc.config import ConfigError
from voice_call import languages, skills
from voice_call.commands import base_config
from voice_call.server import tdt_choice
from voice_call.store import Store, compile_store

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(name="english")
def fixture_english():
    yield languages.use("en")
    languages.use("en")


def test_packs(english):
    assert "en" in languages.codes() and languages.load("en") is english
    with pytest.raises(ValueError, match="no language 'xx'"):
        languages.load("xx")
    assert english.library.is_dir() and sorted(p.name for p in english.library.glob("*.yaml"))
    assert english.ctc_dir(ROOT).name.startswith("stt_en_parakeet_tdt_ctc_110m")
    # Without its models, the app says which and stops.
    assert english.missing(ROOT / "nowhere") == [
        "models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m", "models/piper/en_US-ellie-medium.onnx"]
    # Its matcher config is the base every home's commands build on.
    config = base_config()
    assert config["language"] == "en" and "the" in config["filler"] and config["settings"]["examples_first"]


def test_only_the_languages_skills(english, monkeypatch):
    assert list(skills.available()) == ["spelling", "dates", "clock", "units", "arithmetic"]
    monkeypatch.setattr(languages, "_current", replace(english, skills=("units",)))
    assert list(skills.available()) == ["units"]
    assert skills.enabled() == {"units"} and skills.answer("how do you spell cat") is None
    assert skills.answer("how many cups are in a quart").skill == "units"
    monkeypatch.setattr(languages, "_current", replace(english, skills=()))
    assert skills.enabled() == frozenset() and skills.answer("how many cups are in a quart") is None


def test_the_commands_language(english, tmp_path):
    assert compile_store({}).language == "en"  # none said: English
    assert compile_store({"language": "de"}).language == "de"
    with pytest.raises(ConfigError, match="language"):
        compile_store({"language": "German"})
    assert Store(tmp_path).document()["language"] == "en"  # a new home's says it


MIXED = {
    "language": "en",
    "commands": {
        "never_mind": {"description": "do nothing", "examples": ["never mind"]},
        "vergiss_es": {"description": "nichts tun", "examples": ["vergiss es"], "language": "de"},
    },
}


def test_only_the_apps_languages_commands(english):
    # Each command is in the file's language unless it says its own; only
    # the app's are compiled, the others kept (hidden) for when it is theirs.
    compiled = compile_store(MIXED)
    assert list(compiled.commands) == ["never_mind"] and compiled.hidden == {"vergiss_es": "de"}
    languages.use("de")
    compiled = compile_store(MIXED)
    assert list(compiled.commands) == ["vergiss_es"] and compiled.hidden == {"never_mind": "en"}
    with pytest.raises(ConfigError, match="vergiss_es.language"):
        compile_store({"commands": {"vergiss_es": {"examples": ["vergiss es"], "language": "Deutsch"}}})


def test_a_command_is_made_in_the_apps_language(english, tmp_path):
    store = Store(tmp_path)  # made in English: the app's language when first read
    store.put_command("never_mind", {"description": "do nothing", "examples": ["never mind"], "fallback": ["meaning"]})
    languages.use("de")
    compiled = store.put_command("vergiss_es", {"description": "nichts tun", "examples": ["vergiss es"]})
    assert store.load().data["commands"]["vergiss_es"]["language"] == "de"  # not the file's: said
    assert list(compiled.commands) == ["vergiss_es"] and compiled.hidden == {"never_mind": "en"}
    # Another language's command keeps its id, and is left as it was.
    with pytest.raises(ConfigError, match="taken by a command in English"):
        store.put_command("never_mind", {"description": "nichts tun", "examples": ["egal"]})
    store.put_fallback({"meaning": []})
    assert store.load().data["commands"]["never_mind"]["fallback"] == ["meaning"]
    languages.use("en")
    assert list(store.load().commands) == ["never_mind"]


def test_names_are_in_their_language(english, tmp_path):
    # A thing's name and aliases are in the file's language; another
    # language's are under its code. Only the app's are heard and shown.
    data = {
        "language": "en",
        "names": {"areas": {"kitchen": {"name": "Kitchen", "de": {"name": "Küche", "aliases": ["Kochecke"]}},
                            "office": "Office", "keller": {"de": "Keller"}}},
    }
    compiled = compile_store(data)
    assert compiled.data["names"]["areas"] == {"kitchen": {"name": "Kitchen"}, "office": "Office"}
    assert compiled.hidden_names == {"de": 1}
    languages.use("de")
    compiled = compile_store(data)
    assert compiled.data["names"]["areas"] == {"kitchen": {"name": "Küche", "aliases": ["Kochecke"]}, "keller": "Keller"}
    assert compiled.hidden_names == {"en": 1}
    with pytest.raises(ConfigError, match="neither name, aliases, house nor a language code"):
        compile_store({"names": {"areas": {"kitchen": {"name": "Küche", "German": "Küche"}}}})

    # Naming it in the app's language keeps its other names, and so does
    # taking that name away.
    store = Store(tmp_path)  # made in English: the app's language when first read
    languages.use("en")
    store.put_name("areas", "kitchen", {"name": "Kitchen"})
    languages.use("de")
    store.put_name("areas", "kitchen", {"name": "Küche", "aliases": ["Kochecke"]})
    entry = plain_names(store)["kitchen"]
    assert entry == {"name": "Kitchen", "de": {"name": "Küche", "aliases": ["Kochecke"]}}
    store.put_name("areas", "kitchen", None)
    assert plain_names(store)["kitchen"] == {"name": "Kitchen"}
    languages.use("en")
    store.put_name("areas", "kitchen", None)
    assert "kitchen" not in plain_names(store)


def test_a_devices_own_whatever_it_is_called_in(english, tmp_path):
    """A device's names in its area and in the whole house (``house``: left
    out, the same; empty, none) are in each language it is called in; its
    area for voice and whether it is part of its area and heard with its area
    said elsewhere are its own, kept when it is named in another language. One
    only part of its area has no name; removed, it is gone."""
    from voice_call.store import Device  # pylint: disable=import-outside-toplevel

    store = Store(tmp_path)
    store.put_name("entities", "light.office_lamp", {"name": "Lamp", "house": ["Office Lamp"], "area": "office", "with_area": False})
    assert store.load().devices == {"light.office_lamp": Device("office", ("Lamp",), ("Office Lamp",), with_area=False)}
    languages.use("de")
    store.put_name("entities", "light.office_lamp", {"name": "Lampe", "house": []})
    entities = store.document()["names"]["entities"]
    assert entities["light.office_lamp"] == {
        "name": "Lamp", "house": ["Office Lamp"], "de": {"name": "Lampe", "house": []}, "area": "office", "with_area": False}
    compiled = store.load()
    assert compiled.data["names"]["entities"]["light.office_lamp"] == {"name": "Lampe"}  # what adapt-ctc reads: every name
    assert compiled.devices["light.office_lamp"] == Device("office", ("Lampe",), (), with_area=False)
    languages.use("en")
    store.put_name("entities", "light.office_lamp", {"name": "Lamp", "aliases": ["Desk Lamp"], "area": None, "with_area": None})
    assert store.document()["names"]["entities"]["light.office_lamp"] == {"name": "Lamp", "aliases": ["Desk Lamp"], "de": {"name": "Lampe", "house": []}}
    assert store.load().devices["light.office_lamp"] == Device(None, ("Lamp", "Desk Lamp"), ("Lamp", "Desk Lamp"))
    # Only part of its area: no name, heard by none.
    store.put_name("entities", "light.night", {"name": "", "house": []})
    assert store.document()["names"]["entities"]["light.night"] == {}
    compiled = store.load()
    assert compiled.devices["light.night"] == Device() and "light.night" not in compiled.data["names"]["entities"]
    with pytest.raises(ConfigError, match="Give it a name"):
        store.put_name("entities", "light.night", {"name": "", "house": [], "implicit": False})
    store.put_name("entities", "light.night", None)
    assert "light.night" not in store.document()["names"]["entities"]
    with pytest.raises(ConfigError, match="implicit: true or false"):
        compile_store({"names": {"entities": {"light.x": {"name": "X", "implicit": "no"}}}})
    with pytest.raises(ConfigError, match="house: a list of names"):
        compile_store({"names": {"entities": {"light.x": {"name": "X", "house": "X"}}}})
    with pytest.raises(ConfigError, match="only a device has one"):
        compile_store({"names": {"areas": {"office": {"name": "Office", "implicit": False}}}})


def test_devices_from_before_have_their_names_where_they_were_heard(english, tmp_path):
    """A commands file from before version 3: a device's ``reach`` becomes
    where its names are heard -- anywhere, in the whole house too; its area,
    only there; by name only, not part of its area; only implicitly, by no
    name -- written once, the file as it was kept beside it."""
    import json  # pylint: disable=import-outside-toplevel

    from voice_call.store import VERSION, Device  # pylint: disable=import-outside-toplevel

    before = {"version": 2, "language": "en", "commands": {}, "lists": {}, "names": {"entities": {
        "light.porch": {"name": "Porch Light"},
        "light.office_lamp": {"name": "Lamp", "reach": "area", "area": "office", "de": "Lampe"},
        "light.old": {"name": "Old Lamp", "local": True},
        "light.desk": {"name": "Desk Light", "reach": "name"},
        "light.night": {"reach": "implicit"},
    }}}
    (tmp_path / "commands.json").write_text(json.dumps(before))
    store = Store(tmp_path)
    devices = store.load().devices
    assert devices == {
        "light.porch": Device(None, ("Porch Light",), ("Porch Light",)),
        "light.office_lamp": Device("office", ("Lamp",), ()),
        "light.old": Device(None, ("Old Lamp",), ()),
        "light.desk": Device(None, ("Desk Light",), ("Desk Light",), implicit=False),
        "light.night": Device(),
    }
    saved = json.loads((tmp_path / "commands.json").read_text())
    assert saved["version"] == VERSION
    assert saved["names"]["entities"]["light.office_lamp"] == {"name": "Lamp", "area": "office", "de": {"name": "Lampe", "house": []}, "house": []}
    assert json.loads((tmp_path / "commands.before-names.json").read_text()) == before


def test_name_clashes_are_names_two_devices_have_where_they_are_heard():
    from voice_call.store import Device, name_clashes  # pylint: disable=import-outside-toplevel

    devices = {
        "light.office_lamp": Device(None, ("Lamp",), ("Office Lamp",)),
        "light.bedroom_lamp": Device(None, ("Lamp",), ("Bedroom Lamp",)),
        "light.desk": Device(None, ("lamp",), ("office  lamp",)),
        "light.porch": Device(None, ("Lamp",), ()),
    }
    area = {"light.office_lamp": "office", "light.bedroom_lamp": "bedroom", "light.desk": "office", "light.porch": None}.get
    clashes = name_clashes(devices, area)
    # Lamp in the office twice, and Office Lamp in the whole house twice; the
    # bedroom's lamp is its own, and the porch's names in no area are heard nowhere.
    assert clashes == {
        "light.office_lamp": [{"name": "Lamp", "with": ["light.desk"], "area": "office"},
                              {"name": "Office Lamp", "with": ["light.desk"], "area": None}],
        "light.desk": [{"name": "lamp", "with": ["light.office_lamp"], "area": "office"},
                       {"name": "office  lamp", "with": ["light.office_lamp"], "area": None}],
    }


def test_lists_are_in_their_language(english, tmp_path):
    # Every home has its language's lists; one of its own by that name, in
    # that language, takes its place. A value's phrases in another language
    # than the file's go under its code.
    assert compile_store({}).data["lists"]["colors"]["turquoise"] == ["turquoise"]
    data = {"language": "en", "lists": {"colors": {"red": ["red", "crimson"], "blue": {"en": "blue", "de": "blau"}}}}
    assert compile_store(data).data["lists"]["colors"] == {"red": ["red", "crimson"], "blue": "blue"}
    languages.use("de")
    compiled = compile_store(data)
    assert compiled.data["lists"]["colors"] == {"blue": "blau"} and compiled.hidden_lists == {}
    assert compile_store({"language": "en", "lists": {"modes": ["eco"]}}).data["lists"]["colors"]["red"] == ["rot"]
    assert compile_store({"language": "en", "lists": {"modes": ["eco"]}}).hidden_lists == {"en": 1}

    store = Store(tmp_path)  # made in English: the app's language when first read
    languages.use("en")
    store.put_list("colors", {"red": ["red", "crimson"], "blue": "blue"})
    languages.use("de")
    store.put_list("colors", {"red": ["rot"], "green": ["grün"]})
    assert plain_lists(store)["colors"] == {"red": {"en": ["red", "crimson"], "de": ["rot"]}, "blue": "blue",
                                            "green": {"de": ["grün"]}}
    store.put_list("colors", None)  # German's gone: the built-in again, English's stays
    assert plain_lists(store)["colors"] == {"red": ["red", "crimson"], "blue": "blue"}
    assert store.load().data["lists"]["colors"]["green"] == ["grün"]


def plain_lists(store):
    return store.document()["lists"] or {}


def plain_names(store):
    """The areas' names as saved, every language."""
    return store.document()["names"]["areas"] or {}


def test_the_transducer_choice():
    # "en", from before languages had packs, is the language's own.
    assert tdt_choice("en") == tdt_choice(None) == tdt_choice("own") == "own"
    assert tdt_choice("multilingual") == "multilingual" and tdt_choice("nope") == "own"


DATA = (str, dict, list, tuple, set, re.Pattern)


def public(module) -> dict[str, object]:
    """Its sentences and what makes them: not what it imports."""
    return {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("_")
        and (isinstance(value, DATA) or getattr(value, "__module__", None) == module.__name__)
    }


@pytest.mark.parametrize("module", ["speech", "reading"])
@pytest.mark.parametrize("code", languages.codes())
def test_every_language_says_and_reads_what_english_does(code, module):
    english = public(getattr(languages.load("en"), module))
    own = public(getattr(languages.load(code), module))
    assert set(english) <= set(own), f"{code} lacks {sorted(set(english) - set(own))}"
    for name, value in english.items():
        if callable(value) and not isinstance(value, re.Pattern):
            assert inspect.signature(own[name]).parameters.keys() == inspect.signature(value).parameters.keys(), name
        elif isinstance(value, dict):
            assert set(own[name]) == set(value), name
        else:
            assert type(own[name]) is type(value) and own[name], name


def test_what_is_said_is_the_apps_language(english, monkeypatch):
    assert languages.speech() is english.speech
    assert english.speech.place([("area", "Kitchen")], to=True) == "to the Kitchen"
    assert english.speech.place([("floor", "First Floor"), ("floor", "upstairs")]) == "on the First Floor and upstairs"
    monkeypatch.setattr(languages, "_current", replace(english, code="xx"))
    with pytest.raises(ModuleNotFoundError):
        languages.speech()


def bench_script():
    spec = importlib.util.spec_from_file_location("bench_language", ROOT / "scripts/bench_language.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("code", [c for c in languages.codes() if (languages.load(c).directory / "bench.yaml").is_file()])
def test_the_bench_typed(code):
    """A pack's bench sentences, typed: each becomes its command and slots
    with the pack's whole library (scripts/bench_language.py speaks them)."""
    pack = languages.use(code)
    try:
        if pack.missing(ROOT):
            pytest.skip(f"no {pack.name} models")
        bench = bench_script()
        data = bench.bench(code)
        engine = bench.engine_for(data, threads=2)
        satellite = bench.satellite_for(data)
        wrong = []
        for text, command, slots, _shows in data["sentences"]:
            ok, got, got_slots = bench.scored(engine.recognize_text(text, satellite), command, slots or {})
            if not ok:
                wrong.append((text, got, got_slots))
        assert not wrong
    finally:
        languages.use(languages.DEFAULT)


SKILL_GRAMMAR = {
    "numbers": ("DECIMAL", "THOUSANDS", "normalize", "sentence"),
    "live": ("is_live",),
    "spelling": ("EXAMPLES", "answer"),
    "arithmetic": ("EXAMPLES", "answer"),
    "clock": ("EXAMPLES", "answer", "HOURS"),
    "dates": ("EXAMPLES", "answer", "HOLIDAYS", "WHEN_EXAMPLES", "when", "key"),
    "units": ("EXAMPLES", "answer", "UNITS", "VOLUMES", "VOLUME_NAMES", "DEFAULT_VOLUMES", "QUALIFIED", "OUNCE",
              "FLUID_OUNCE", "plural_of", "key", "read_unit", "forms", "temperature_pattern"),
}
"""What voice_call/skills takes from a language's skills/<name>.py."""


@pytest.mark.parametrize("code", languages.codes())
def test_every_language_reads_its_skills(code):
    pack = languages.load(code)
    for name in ("numbers", "live", *pack.skills) if pack.skills else ():
        module = pack.grammar(name)
        missing = [attr for attr in SKILL_GRAMMAR[name] if not hasattr(module, attr)]
        assert not missing, f"{code} skills/{name}.py lacks {missing}"
        if name == "units":
            assert module.DEFAULT_VOLUMES in module.VOLUMES and set(module.VOLUME_NAMES) == set(module.VOLUMES)
