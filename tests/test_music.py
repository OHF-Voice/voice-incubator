"""Music commands: which player, what was asked for, and how it went."""

from __future__ import annotations

import asyncio

import pytest

from voice_labs import actions, music
from voice_labs.commands import parse_command
from voice_labs.engine import Decision, Satellite
from voice_labs.hass import HomeAssistant
from voice_labs.languages.en import speech as english
from voice_labs.music import Item, Player, Query

from . import fake_hass

OWN = "media_player.fake_vpe"
KITCHEN = "media_player.kitchen_speaker"


def players(own="idle", kitchen="idle", **attrs):
    return {
        OWN: Player(OWN, "Fake VPE", own, **attrs),
        KITCHEN: Player(KITCHEN, "Kitchen Speaker", kitchen, title="So What", artist="Miles Davis"),
    }


def chosen(operation, states, named=None, paused=(), nearby=(KITCHEN,), sent=()):
    choice = music.choose(operation, states, OWN, named, list(nearby), list(paused), list(sent))
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


def test_following_the_music_it_sent_elsewhere():
    # Moved to (or played on) the kitchen, which is not in the satellite's
    # area: with nothing playing here, what names no player follows it.
    away = {"nearby": (), "sent": (KITCHEN,)}
    assert chosen("volume_down", players(kitchen="playing"), **away) == ([KITCHEN], None)
    assert chosen("pause", players(kitchen="playing"), **away) == ([KITCHEN], None)
    assert chosen("status", players(kitchen="playing"), **away) == ([KITCHEN], None)
    assert chosen("next", players(kitchen="playing"), **away) == ([KITCHEN], None)
    # Paused there: resumed, or asked about; not turned up.
    assert chosen("resume", players(kitchen="paused"), **away) == ([KITCHEN], None)
    assert chosen("status", players(kitchen="paused"), **away) == ([KITCHEN], None)
    assert chosen("volume_up", players(kitchen="paused"), **away) == ([OWN], None)
    # Music here wins; "play" plays here; never sent anywhere, nothing follows.
    assert chosen("volume_down", players(own="playing", kitchen="playing"), **away) == ([OWN], None)
    assert chosen("play", players(kitchen="playing"), **away) == ([OWN], None)
    assert chosen("pause", players(kitchen="playing"), nearby=()) == ([], "nothing_playing")


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
        ("some jazz music", [Query("jazz", "jazz", None, ["genre"])]),
        ("the jazz genre", [Query("jazz", "jazz", None, ["genre"])]),
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
        assert tuple(music.examples()[operation]) == outcomes


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
            assert (outcome.name, outcome.speech) == ("no_player", english.NO_PLAYER)
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
    from voice_labs.hass import Catalog, Entity

    catalog = Catalog(
        entities={
            "media_player.a": Entity("media_player.a", "A", platform="music_assistant", unique_id="upd83bdaa3d5f4"),
            "media_player.b": Entity("media_player.b", "B", platform="music_assistant", unique_id="up1", device_id="d1"),
            "media_player.c": Entity("media_player.c", "C", platform="music_assistant", unique_id="up2", device_id="d2"),
        },
        device_models={"d1": ("ESPHome", "kitchen-vpe"), "d2": ("ESPHome", "voice-labs-vpe")},
    )
    assert catalog.music_player_for("d83bdaa3d5f4") == "media_player.a"  # "up" + the MAC
    assert catalog.music_player_for("aabbccddeeff", "kitchen-vpe") == "media_player.b"
    catalog.device_models["d1"] = ("ESPHome", "voice-labs-vpe")
    assert catalog.music_player_for("aabbccddeeff", "voice-labs-vpe") is None  # two alike: no guess


def test_a_genre_by_name_or_else_anything_by_it():
    """A genre is played by its name, which Music Assistant looks up; one it
    can't find is the closest of anything by that name."""
    async def scenario():
        fake, server, hass = await _hass()
        try:
            helper = music.Music(hass)
            outcome = await _run(hass, {"music": "play"}, {"query": "some jazz music"}, helper=helper)
            assert (outcome.name, outcome.speech) == ("done", "Playing jazz music.")
            plays = [m["service_data"] for m in fake.services if m["service"] == "play_media"]
            assert plays == [{"media_id": "jazz", "media_type": "genre"}]
            assert not [m for m in fake.services if m["service"] == "search"]
            # No such genre: the Chill playlist, by its name.
            outcome = await _run(hass, {"music": "play"}, {"query": "chill music"}, helper=helper)
            assert (outcome.name, outcome.speech) == ("done", "Playing the Chill playlist.")
            plays = [m["service_data"] for m in fake.services if m["service"] == "play_media"]
            assert plays[-2:] == [{"media_id": "chill", "media_type": "genre"},
                                  {"media_id": "library://playlist/1", "media_type": "playlist"}]
            assert "Could not resolve" in outcome.music["genre"]["error"]
            # Nor anything else.
            outcome = await _run(hass, {"music": "play"}, {"query": "polka music"}, helper=helper)
            assert (outcome.name, outcome.speech) == ("not_found", "I couldn't find polka.")
        finally:
            await hass.stop()
            server.close()

    asyncio.run(scenario())


def test_the_controls_are_any_media_players():
    """Pausing and the volume act on whatever plays in the satellite's area,
    a TV among them; "the music" is Music Assistant's players alone, "the TV"
    the TVs. A TV that can't be set to a volume is stepped, and says so."""
    async def scenario():
        fake, server = await fake_hass.serve()
        # The living room's TV is on; its volume only steps (no VOLUME_SET).
        fake.states["media_player.tv"] = ("playing", {"friendly_name": "TV", "device_class": "tv",
                                                      "supported_features": 1 | 1024, "media_title": "The News"})
        port = server.sockets[0].getsockname()[1]
        hass = HomeAssistant(f"http://127.0.0.1:{port}", fake_hass.TOKEN)
        await hass.start()
        assert await hass.wait_connected(5)
        den = Satellite("aabbccddeeff", "Den", "living_room", "Living Room", media_player=OWN)
        try:
            def sent():
                return [(m["service"], m.get("target", {}).get("entity_id")) for m in fake.services if m["domain"] == "media_player"]

            helper = music.Music(hass)
            # Nothing named: the TV, which is what plays here.
            outcome = await _run(hass, {"music": "volume_down"}, satellite=den, helper=helper)
            assert outcome.name == "done" and sent()[-1] == ("volume_down", ["media_player.tv"])
            outcome = await _run(hass, {"music": "pause"}, satellite=den, helper=helper)
            assert outcome.name == "done" and sent()[-1] == ("media_pause", ["media_player.tv"])
            fake.states["media_player.tv"] = ("playing", fake.states["media_player.tv"][1])
            # "The music": Music Assistant's players alone, and none plays.
            outcome = await _run(hass, {"music": "pause"}, {"kind": "music"}, satellite=den, helper=helper)
            assert outcome.name == "nothing_playing"
            # "The TV", to a volume: it can't be set to one.
            outcome = await _run(hass, {"music": "volume_set"}, {"kind": "tv", "percentage": 20}, satellite=den, helper=helper)
            assert (outcome.name, outcome.speech) == ("not_supported", "TV can't be set to a volume.")
            # "The TV" in a room with none.
            office = Satellite("aabbccddeeff", "Office", "office", "Office", media_player=OWN)
            outcome = await _run(hass, {"music": "pause"}, {"kind": "tv"}, satellite=office, helper=helper)
            assert outcome.name == "no_player"
        finally:
            await hass.stop()
            server.close()

    asyncio.run(scenario())


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


def test_what_music_says_by_level():
    own = Player(OWN, "Kitchen Speaker", "playing", "So What", "Miles Davis", volume=0.5)
    assert music.control_said("pause", {}, [own]) == {
        "brief": "Paused.", "specific": "Paused So What by Miles Davis.", "verbose": "Paused So What by Miles Davis on Kitchen Speaker.",
    }
    assert music.control_said("volume_up", {}, [own])["specific"] == "Volume at 60%."
    assert music.control_said("next", {}, [own])["brief"] == "Next track."
    item = music.Item("Kind of Blue", "library://album/1", "album", "Miles Davis")
    said = music.found_said("play", item, [own])
    assert said["brief"] == "Playing music." and said["specific"] == "Playing Kind of Blue by Miles Davis."
    assert set(music.level_examples()) == set(music.OUTCOMES) - {"status"}


def test_moving_chooses_from_what_plays_here_to_what_is_named():
    own = Player(OWN, "Fake VPE", "playing", "So What", "Miles Davis")
    kitchen = Player(KITCHEN, "Kitchen Speaker", "idle")
    den = Player("media_player.den", "Den Speaker", "playing", "Blue in Green")
    players = {p.entity_id: p for p in (own, kitchen, den)}
    choice = music.choose_move(players, OWN, [KITCHEN], [])
    assert choice.source is own and choice.players == [kitchen] and choice.outcome is None
    # Nothing named, or nothing there: no player.
    assert music.choose_move(players, OWN, None, []).outcome == "no_player"
    # Already there.
    assert music.choose_move(players, OWN, [OWN], []).outcome == "already"
    # Nothing playing here: the one player playing anywhere; with two, none.
    quiet = {**players, OWN: Player(OWN, "Fake VPE", "idle")}
    assert music.choose_move({k: v for k, v in quiet.items() if k != "media_player.den"}, OWN, [KITCHEN], []).outcome == "nothing_playing"
    assert music.choose_move(quiet, OWN, [KITCHEN], []).source.entity_id == "media_player.den"
    both = {**quiet, KITCHEN: Player(KITCHEN, "Kitchen Speaker", "playing", "So What")}
    assert music.choose_move(both, OWN, [OWN], []).outcome == "nothing_playing"
    # Paused here counts, when nothing plays here.
    paused = {OWN: Player(OWN, "Fake VPE", "paused", "So What"), KITCHEN: kitchen}
    assert music.choose_move(paused, OWN, [KITCHEN], []).source.entity_id == OWN
    # With two playing elsewhere, the one this satellite sent it to.
    both = {**quiet, KITCHEN: Player(KITCHEN, "Kitchen Speaker", "playing", "So What")}
    assert music.choose_move(both, OWN, [OWN], [], sent=[KITCHEN]).source.entity_id == KITCHEN
    said = music.moved_said(own, kitchen, "to the Kitchen")
    assert said == {"brief": "Moved the music.", "specific": "Moved the music to the Kitchen.",
                    "verbose": "Moved So What by Miles Davis from Fake VPE to Kitchen Speaker."}


def test_moving_the_music_through_home_assistant():
    async def scenario():
        fake, server, hass = await _hass()
        try:
            helper = music.Music(hass)
            move = {"music": "move", "names": {"area": {"count": 1}, "entity": {"count": 1}},
                    "combinations": [["area"], ["entity"]]}
            outcome = await _run(hass, move, {"area": ["kitchen"]}, helper=helper)
            assert (outcome.name, outcome.speech) == ("nothing_playing", "Nothing is playing to move.")
            await _run(hass, {"music": "play"}, {"query": "the album kind of blue"}, helper=helper)
            outcome = await _run(hass, move, {"area": ["kitchen"]}, helper=helper)
            assert (outcome.name, outcome.speech) == ("done", "Moved the music to the Kitchen.")
            transfer = fake.services[-1]
            assert transfer["service"] == "transfer_queue" and transfer["target"] == {"entity_id": [KITCHEN]}
            assert transfer["service_data"] == {"source_player": OWN, "auto_play": True}
            assert fake.states[KITCHEN][0] == "playing" and fake.states[OWN][0] == "idle"
            assert fake.queues[KITCHEN]["uri"] == "library://album/1" and OWN not in fake.queues
            # Nothing plays here now: "turn it down" follows the music there.
            assert helper.sent_by_voice(SATELLITE.key) == [KITCHEN]
            outcome = await _run(hass, {"music": "volume_set"}, {"percentage": 20}, helper=helper)
            assert outcome.name == "done" and fake.services[-1]["target"] == {"entity_id": [KITCHEN]}
            assert fake.states[KITCHEN][1]["volume_level"] == 0.2
            assert outcome.speech == "Volume at 20% on Kitchen Speaker."  # a beep, here
            # Back to the satellite's own player, by name: from the one playing anywhere.
            outcome = await _run(hass, {**move, "verbosity": "verbose"}, {"entity": [OWN]}, helper=helper)
            assert outcome.speech == "Moved Kind of Blue by Miles Davis from Kitchen Speaker to Fake VPE."
            assert fake.states[OWN][0] == "playing"
            outcome = await _run(hass, move, {"entity": [OWN]}, helper=helper)
            assert (outcome.name, outcome.speech) == ("already", "The music is already playing there.")
            outcome = await _run(hass, move, {"area": ["bedroom"]}, helper=helper)
            assert (outcome.name, outcome.speech) == ("no_player", "There's no music player there.")
        finally:
            await hass.stop()
            server.close()

    asyncio.run(scenario())


def test_free_text_without_the_place_it_ends_on():
    from voice_labs.engine import without_place  # pylint: disable=import-outside-toplevel

    assert without_place("jazz in the kitchen", ["kitchen"]) == "jazz"
    assert without_place("Kind of Blue upstairs", ["upstairs"]) == "Kind of Blue"
    assert without_place("some music in the living room please", ["living room"], ["the", "please"]) == "some music"
    assert without_place("the kitchen sink", ["kitchen"]) == "the kitchen sink"  # not where it ends
    assert without_place("jazz", []) == "jazz"
    # Compared as spoken: the name is "Mark two", the transcript says "2".
    assert without_place("She paints me blue on the mark 2.", ["mark two"]) == "She paints me blue"
    assert without_place("the mark 2", ["mark two"]) == "the mark 2"  # nothing left: it is the text
    # "Like this": like what is playing.
    assert music.generic("this") and music.generic("that song") and not music.generic("this will destroy you")
