"""Music commands: which player, what was asked for, and how it went."""

from __future__ import annotations

import asyncio

import pytest

from voice_call import actions, music
from voice_call.commands import parse_command
from voice_call.engine import Decision, Satellite
from voice_call.hass import HomeAssistant
from voice_call.music import Item, Player, Query

from . import fake_hass

OWN = "media_player.fake_vpe"
KITCHEN = "media_player.kitchen_speaker"


def players(own="idle", kitchen="idle", **attrs):
    return {
        OWN: Player(OWN, "Fake VPE", own, **attrs),
        KITCHEN: Player(KITCHEN, "Kitchen Speaker", kitchen, title="So What", artist="Miles Davis"),
    }


def chosen(operation, states, named=None, paused=()):
    choice = music.choose(operation, states, OWN, named, [KITCHEN], list(paused))
    return [p.entity_id for p in choice.players], choice.outcome


def test_the_satellites_own_player_by_default():
    assert chosen("play", players()) == ([OWN], None)
    assert chosen("volume_up", players()) == ([OWN], None)


def test_what_is_playing_nearby_when_its_own_is_not():
    assert chosen("pause", players(kitchen="playing")) == ([KITCHEN], None)
    assert chosen("next", players(own="playing", kitchen="playing")) == ([OWN], None)
    assert chosen("volume_down", players(kitchen="playing")) == ([KITCHEN], None)
    assert chosen("status", players(kitchen="playing")) == ([KITCHEN], None)
    assert chosen("pause", players()) == ([], "nothing_playing")


def test_resume_what_voice_paused():
    states = players(own="idle", kitchen="paused")
    assert chosen("resume", states, paused=[KITCHEN]) == ([KITCHEN], None)
    assert chosen("resume", states) == ([OWN], None)
    assert chosen("resume", players(own="playing")) == ([], "already")


def test_named_players():
    assert chosen("play", players(), named=[KITCHEN]) == ([KITCHEN], None)
    assert chosen("play", players(), named=[]) == ([], "no_player")  # an area with none
    assert chosen("pause", players(), named=[KITCHEN]) == ([], "nothing_playing")


def test_no_player():
    assert music.choose("play", {}, None, None, [], []).outcome == "no_player"
    unavailable = {OWN: Player(OWN, state="unavailable")}
    assert music.choose("pause", unavailable, OWN, None, [], []).outcome == "no_player"


def test_controls():
    own = Player(OWN, volume=0.45)
    assert music.control("volume_up", {}, [own])[0] == [("volume_set", {"volume_level": 0.55})]
    assert music.control("volume_down", {}, [Player(OWN, volume=0.05)])[0] == [("volume_set", {"volume_level": 0.0})]
    assert music.control("volume_set", {"percentage": 40}, [own])[0] == [("volume_set", {"volume_level": 0.4})]
    assert music.control("volume_set", {}, [own])[2] == "missing_volume"
    assert music.control("pause", {}, [own]) == ([("media_pause", {})], "", "done")
    playing = Player(OWN, state="playing", title="So What", artist="Miles Davis")
    assert music.control("status", {}, [playing])[1] == "This is So What by Miles Davis."
    paused = Player(OWN, state="paused", title="So What", artist="Miles Davis")
    assert music.control("status", {}, [paused])[1] == "So What by Miles Davis is paused."


@pytest.mark.parametrize(
    ("heard", "readings"),
    [
        ("Miles Davis", [Query("miles davis", "miles davis")]),
        ("the album Kind of Blue", [Query("kind of blue", "kind of blue", None, ["album"])]),
        ("songs by Miles Davis.", [Query("miles davis", "miles davis", None, ["artist"])]),
        ("my chill playlist please", [Query("chill", "chill", None, ["playlist"])]),
        ("some jazz", [Query("jazz", "jazz")]),
        (
            "so what by miles davis",
            [
                Query("so what by miles davis", "so what by miles davis"),
                Query("so what by miles davis", "so what", "miles davis", ["track", "album"]),
            ],
        ),
    ],
)
def test_what_was_asked_for(heard, readings):
    assert music.queries(heard) == readings


def test_generic_requests_resume():
    assert music.generic("some music") and music.generic("") and not music.generic("jazz")


def test_the_closest_result_wins():
    found = [
        Item("Miles Davis", "a", "artist"),
        Item("Kind of Blue", "b", "album", "Miles Davis"),
        Item("So What", "c", "track", "Miles Davis"),
    ]
    assert music.best([(Query("kind of blue", "kind of blue"), found)]).uri == "b"
    assert music.best([(Query("miles davis", "miles davis"), found)]).uri == "a"
    by = Query("so what by miles davis", "so what", "miles davis", ["track"])
    assert music.best([(Query("so what by miles davis", "so what by miles davis"), found), (by, found)]).uri == "c"
    assert music.best([(Query("zzz top hits", "zzz top hits"), found)]) is None


def test_every_outcome_has_an_example():
    for operation, outcomes in music.OUTCOMES.items():
        assert tuple(music.EXAMPLES[operation]) == outcomes


# ------------------------------------------------- through Home Assistant


async def _hass():
    fake, server = await fake_hass.serve()
    port = server.sockets[0].getsockname()[1]
    hass = HomeAssistant(f"http://127.0.0.1:{port}", fake_hass.TOKEN)
    await hass.start()
    assert await hass.wait_connected(5)
    return fake, server, hass


SATELLITE = Satellite("aabbccddeeff", "Fake VPE", "office", "Office")


def _run(hass, spec, slots=None, satellite=None, dry_run=False, helper=None):
    command = parse_command("c", {"description": "do it", "examples": ["do it"], **spec})
    decision = Decision({"transcript": "do it"}, command, command.behavior, "command", slots=slots or {})
    sat = satellite or Satellite(**{**SATELLITE.__dict__, "media_player": hass.catalog.music_player_for(SATELLITE.key, "fake-vpe")})
    return actions.run(hass, decision, sat, None, dry_run, None, helper or music.Music(hass))


def test_play_pause_and_resume_through_home_assistant():
    async def scenario():
        fake, server, hass = await _hass()
        try:
            # Without the integration, the one ESPHome player named like the satellite.
            assert hass.catalog.music_player_for("aabbccddeeff", "fake-vpe") == OWN
            assert hass.catalog.music_player_for("aabbccddeeff", "other-vpe") is None
            helper = music.Music(hass)
            outcome = await _run(hass, {"music": "play"}, {"query": "the album kind of blue"}, helper=helper)
            assert (outcome.name, outcome.speech) == ("done", "Playing Kind of Blue by Miles Davis.")
            play = next(m for m in fake.services if m["service"] == "play_media")
            assert play["target"] == {"entity_id": [OWN]}
            assert play["service_data"] == {"media_id": "library://album/1", "media_type": "album"}
            assert fake.states[OWN][0] == "playing"

            outcome = await _run(hass, {"music": "status"}, helper=helper)
            assert outcome.speech == "This is Kind of Blue by Miles Davis."
            outcome = await _run(hass, {"music": "pause", "response": {"nothing_playing": "Shh."}}, helper=helper)
            assert (outcome.name, outcome.speech) == ("done", "")
            assert fake.states[OWN][0] == "paused"
            outcome = await _run(hass, {"music": "pause", "response": {"nothing_playing": "Shh."}}, helper=helper)
            assert (outcome.name, outcome.speech) == ("nothing_playing", "Shh.")
            outcome = await _run(hass, {"music": "resume"}, helper=helper)
            assert outcome.name == "done" and fake.states[OWN][0] == "playing"

            outcome = await _run(hass, {"music": "volume_set"}, {"percentage": 30}, helper=helper)
            assert fake.states[OWN][1]["volume_level"] == 0.3
            outcome = await _run(hass, {"music": "radio"}, helper=helper)
            assert outcome.speech == "Playing music like Kind of Blue by Miles Davis."
            assert fake.services[-1]["service_data"]["radio_mode"] is True

            outcome = await _run(hass, {"music": "play"}, {"query": "zzz top hits"}, helper=helper)
            assert (outcome.name, outcome.speech) == ("not_found", "I couldn't find zzz top hits.")
            # "Play some music" resumes; a dry run searches but plays nothing.
            calls = len(fake.services)
            outcome = await _run(hass, {"music": "play"}, {"query": "so what by miles davis"}, dry_run=True, helper=helper)
            assert outcome.speech == "Playing So What by Miles Davis."
            assert len([m for m in fake.services[calls:] if m["service"] != "search"]) == 0
        finally:
            await hass.stop()
            server.close()

    asyncio.run(scenario())


def test_without_a_player_of_its_own():
    async def scenario():
        fake, server, hass = await _hass()
        try:
            lonely = Satellite("112233445566", "Elsewhere", "bedroom", "Bedroom")
            outcome = await _run(hass, {"music": "play"}, {"query": "miles davis"}, satellite=lonely)
            assert (outcome.name, outcome.speech) == ("no_player", music.NO_PLAYER)
            # In the kitchen, the kitchen's speaker is the one playing.
            fake.states[KITCHEN] = ("playing", {**fake.states[KITCHEN][1], "media_title": "So What"})
            kitchen = Satellite("112233445566", "Kitchen", "kitchen", "Kitchen")
            outcome = await _run(hass, {"music": "pause"}, satellite=kitchen)
            assert outcome.name == "done" and fake.states[KITCHEN][0] == "paused"
            # Naming an area plays there.
            outcome = await _run(hass, {"music": "play"}, {"query": "chill playlist", "area": ["kitchen"]}, satellite=lonely)
            assert outcome.speech == "Playing the Chill playlist."
            assert fake.services[-1]["target"] == {"entity_id": [KITCHEN]}
        finally:
            await hass.stop()
            server.close()

    asyncio.run(scenario())


def test_guessing_a_satellites_player():
    from voice_call.hass import Catalog, Entity

    catalog = Catalog(
        entities={
            "media_player.a": Entity("media_player.a", "A", platform="music_assistant", unique_id="upd83bdaa3d5f4"),
            "media_player.b": Entity("media_player.b", "B", platform="music_assistant", unique_id="up1", device_id="d1"),
            "media_player.c": Entity("media_player.c", "C", platform="music_assistant", unique_id="up2", device_id="d2"),
        },
        device_models={"d1": ("ESPHome", "kitchen-vpe"), "d2": ("ESPHome", "voice-call-vpe")},
    )
    assert catalog.music_player_for("d83bdaa3d5f4") == "media_player.a"  # "up" + the MAC
    assert catalog.music_player_for("aabbccddeeff", "kitchen-vpe") == "media_player.b"
    catalog.device_models["d1"] = ("ESPHome", "voice-call-vpe")
    assert catalog.music_player_for("aabbccddeeff", "voice-call-vpe") is None  # two alike: no guess


def test_search_through_the_player_when_music_assistant_search_fails():
    async def scenario():
        fake, server, hass = await _hass()
        try:
            fake.broken_search = True
            helper = music.Music(hass)
            outcome = await _run(hass, {"music": "play"}, {"query": "the album kind of blue"}, helper=helper)
            assert (outcome.name, outcome.speech) == ("done", "Playing Kind of Blue by Miles Davis.")
            searched = [m["service"] for m in fake.services if "search" in m["service"]]
            assert searched == ["search", "search_media"]
            outcome = await _run(hass, {"music": "play"}, {"query": "so what by miles davis"}, helper=helper)
            assert outcome.speech == "Playing So What by Miles Davis."
            searched = [m["service"] for m in fake.services if "search" in m["service"]]
            assert searched.count("search") == 1  # not tried again
        finally:
            await hass.stop()
            server.close()

    asyncio.run(scenario())


def test_browse_items():
    found = music.browse_items(
        [
            {"title": "Miles Davis - Kind of Blue", "media_class": "album", "media_content_id": "spotify://album/1"},
            {"title": "KEXP", "media_class": "music", "media_content_id": "library://radio/2"},
            {"title": "Chill", "media_class": "playlist", "media_content_id": "library://playlist/3"},
        ]
    )
    assert [(i.name, i.artist, i.media_type) for i in found] == [
        ("Kind of Blue", "Miles Davis", "album"),
        ("KEXP", None, "radio"),
        ("Chill", None, "playlist"),
    ]
    assert [i.media_type for i in music.browse_items([{"title": "KEXP", "media_content_id": "library://radio/2"}], ["album"])] == []
