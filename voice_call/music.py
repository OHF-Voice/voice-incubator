"""Music: playing and controlling Music Assistant's players by voice.

A command says which operation (``music: play``). The player is

* one the command names (``slots.entity``, or the Music Assistant players in
  ``slots.area`` / ``slots.floor``);
* else the satellite's own: the Music Assistant player of its Sendspin client,
  found by MAC (``satellite.media_player``);
* for what acts on playing music (pause, next, "what's playing" ...), the one
  that is playing: the satellite's own, else Music Assistant's players in the
  satellite's area;
* to resume, what voice paused on this satellite, else its own player;
* to move the music (``music: move``), from what is playing here -- the
  satellite's own player, else Music Assistant's in its area, else the one
  player playing anywhere -- to a player the command names, with Music
  Assistant's ``transfer_queue``: the queue goes along, and the other player
  stops.

Playing searches Music Assistant (``music_assistant.search``) for what was
heard, picks the result closest to it, and plays that
(``music_assistant.play_media``), so what is said names what was found. Cues
in what was heard pick a type: "the album …", "songs by …", "my … playlist",
"the … station", "… by …".

Every operation has outcomes, like timers (see OUTCOMES), so a command can say
something else for each. Controls say nothing when done: a short sound.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .hass import HassError

_LOGGER = logging.getLogger(__name__)

OPERATIONS = (
    "play",
    "add",
    "radio",
    "pause",
    "resume",
    "stop",
    "next",
    "previous",
    "volume_up",
    "volume_down",
    "volume_set",
    "mute",
    "unmute",
    "shuffle_on",
    "shuffle_off",
    "repeat_all",
    "repeat_one",
    "repeat_off",
    "status",
    "move",
)
FIND = ("play", "add", "radio")
"""Operations that search Music Assistant for what to play."""

_ON_PLAYING = ("done", "nothing_playing", "no_player")
_ANY = ("done", "no_player")
OUTCOMES = {
    "play": ("done", "missing_query", "not_found", "no_player"),
    "add": ("done", "missing_query", "not_found", "no_player"),
    "radio": ("done", "not_found", "nothing_playing", "no_player"),
    "pause": _ON_PLAYING,
    "resume": ("done", "already", "no_player"),
    "stop": _ON_PLAYING,
    "next": _ON_PLAYING,
    "previous": _ON_PLAYING,
    "volume_up": _ANY,
    "volume_down": _ANY,
    "volume_set": ("done", "missing_volume", "no_player"),
    "mute": _ANY,
    "unmute": _ANY,
    "shuffle_on": _ON_PLAYING,
    "shuffle_off": _ON_PLAYING,
    "repeat_all": _ON_PLAYING,
    "repeat_one": _ON_PLAYING,
    "repeat_off": _ON_PLAYING,
    "status": _ON_PLAYING,
    "move": ("done", "nothing_playing", "no_player", "already"),
}

NO_PLAYER = "There's no music player here."
NOTHING_PLAYING = "Nothing is playing."
SAID = {
    "no_player": NO_PLAYER,
    "nothing_playing": NOTHING_PLAYING,
    "missing_query": "What should I play?",
    "missing_volume": "What volume?",
    "already": "It's already playing.",
}

# Moving: where it goes is what the command names.
MOVE_SAID = {
    "nothing_playing": "Nothing is playing to move.",
    "no_player": "There's no music player there.",
    "already": "The music is already playing there.",
}

# What each outcome says, for the UI: the defaults a command's responses replace.
# A control that worked says nothing (a short sound).
EXAMPLES: dict[str, dict[str, str]] = {
    operation: {
        outcome: SAID.get(outcome, "")
        for outcome in outcomes
    }
    for operation, outcomes in OUTCOMES.items()
}
EXAMPLES["play"].update(done="Playing Kind of Blue by Miles Davis.", not_found="I couldn't find kind of blew.")
EXAMPLES["add"].update(done="Added So What by Miles Davis to the queue.", not_found="I couldn't find so watt.")
EXAMPLES["radio"].update(done="Playing music like So What by Miles Davis.", not_found="I couldn't find so watt.")
EXAMPLES["status"].update(done="This is So What by Miles Davis.")
EXAMPLES["move"].update(done="Moved the music to the Kitchen.", **MOVE_SAID)

VOLUME_STEP = 0.1
MATCH_AT_LEAST = 0.5
"""Least similarity between what was heard and a search result to play it."""

PAUSED_FOR = 6 * 3600
"""How long a pause by voice is remembered for "resume"."""

SEARCH_TYPES = ["artist", "album", "track", "playlist", "radio"]
# A small preference between results as close to what was heard: an artist's
# name is more often asked for than a track that happens to share it.
TYPE_PRIOR = {"artist": 0.03, "playlist": 0.02, "album": 0.01, "track": 0.0, "radio": 0.0}
GENERIC = {"music", "some music", "something", "anything", "songs", "some songs", "the music", "my music"}
"""Asked to play these, the player resumes instead."""


@dataclass
class Player:
    """A media player's state, as far as music commands care."""

    entity_id: str
    name: str = ""
    state: str = "unknown"
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    volume: float | None = None
    muted: bool | None = None

    @property
    def playing(self) -> bool:
        return self.state == "playing"

    @property
    def available(self) -> bool:
        return self.state not in ("unavailable", "unknown")

    @property
    def media(self) -> str | None:
        """"So What by Miles Davis"."""
        if not self.title:
            return None
        return f"{self.title} by {self.artist}" if self.artist else self.title

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "state": self.state,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "volume": self.volume,
            "muted": self.muted,
        }


@dataclass
class Query:
    """One reading of what was asked for."""

    text: str
    """What is compared with the results."""

    name: str
    artist: str | None = None
    media_types: list[str] | None = None


@dataclass
class Item:
    """A search result."""

    name: str
    uri: str
    media_type: str
    artist: str | None = None
    score: float = 0.0

    @property
    def spoken(self) -> str:
        if self.media_type == "playlist":
            return self.name if "playlist" in self.name.lower() else f"the {self.name} playlist"
        if self.artist and self.media_type in ("track", "album"):
            return f"{self.name} by {self.artist}"
        return self.name

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "uri": self.uri, "media_type": self.media_type, "artist": self.artist}


# ------------------------------------------------------------------ choosing


@dataclass
class Choice:
    players: list[Player] = field(default_factory=list)
    outcome: str | None = None
    """Set when nothing will be done: why."""
    source: Player | None = None
    """For a move: the player the music comes from."""


def choose(
    operation: str,
    players: dict[str, Player],
    own: str | None,
    named: list[str] | None,
    nearby: list[str],
    paused_by_voice: list[str],
) -> Choice:
    """Which players ``operation`` acts on.

    ``named``: the players the command names (None for "the music here");
    ``nearby``: Music Assistant's other players in the satellite's area;
    ``paused_by_voice``: what a "pause" on this satellite paused."""

    def known(ids: list[str]) -> list[Player]:
        return [players[i] for i in ids if i in players and players[i].available]

    if named is not None:
        pool = known(named)
    else:
        pool = known(([own] if own else []) + [i for i in nearby if i != own])
    if not pool:
        return Choice(outcome="no_player")
    first = pool[:1]
    playing = [p for p in pool if p.playing]

    if operation in ("play", "add") or (operation == "radio" and named):
        return Choice(pool if named else first)
    if operation in ("volume_up", "volume_down", "volume_set", "mute", "unmute"):
        if named:
            return Choice(pool)
        return Choice(playing[:1] or first)
    if operation == "resume":
        if not named:
            remembered = [players[i] for i in paused_by_voice if i in players and players[i].state == "paused"]
            if remembered:
                return Choice(remembered)
        waiting = [p for p in (pool if named else first) if not p.playing]
        return Choice(waiting) if waiting else Choice(outcome="already")
    if operation == "status":
        paused = [p for p in pool if p.state == "paused" and p.title]
        chosen = playing[:1] or paused[:1]
        return Choice(chosen) if chosen else Choice(outcome="nothing_playing")
    # pause, stop, next, previous, shuffle, repeat, radio from what is playing
    if not playing:
        return Choice(outcome="nothing_playing")
    if operation in ("pause", "stop") and named:
        return Choice(playing)
    return Choice(playing[:1])


def choose_move(
    players: dict[str, Player], own: str | None, named: list[str] | None, nearby: list[str]
) -> Choice:
    """Where the music comes from and goes to. From what is playing here --
    the satellite's own player, then the others in its area (paused, when
    nothing plays) -- else from the one player playing anywhere; to the
    first of the named players that is not the source, one that is not
    playing before one that is."""

    def known(ids: list[str]) -> list[Player]:
        return [players[i] for i in ids if i in players and players[i].available]

    destinations = known(named or [])
    if not destinations:
        return Choice(outcome="no_player")
    here = known(list(dict.fromkeys(([own] if own else []) + nearby)))
    source = next((p for p in here if p.playing), None) or next(
        (p for p in here if p.state == "paused" and p.title), None
    )
    if source is None:
        playing = [p for p in players.values() if p.playing and p.available]
        source = playing[0] if len(playing) == 1 else None
    if source is None:
        return Choice(outcome="nothing_playing")
    targets = [p for p in destinations if p.entity_id != source.entity_id]
    if not targets:
        return Choice(outcome="already", source=source)
    target = next((p for p in targets if not p.playing), targets[0])
    return Choice([target], source=source)


def control(operation: str, slots: dict[str, Any], players: list[Player]) -> tuple[list[tuple[str, str, dict]], str, str]:
    """The media_player service calls for a control, what to say, and the
    outcome. Each call targets every chosen player except where noted."""
    simple = {
        "pause": ("media_pause", {}),
        "resume": ("media_play", {}),
        "stop": ("media_stop", {}),
        "next": ("media_next_track", {}),
        "previous": ("media_previous_track", {}),
        "mute": ("volume_mute", {"is_volume_muted": True}),
        "unmute": ("volume_mute", {"is_volume_muted": False}),
        "shuffle_on": ("shuffle_set", {"shuffle": True}),
        "shuffle_off": ("shuffle_set", {"shuffle": False}),
        "repeat_all": ("repeat_set", {"repeat": "all"}),
        "repeat_one": ("repeat_set", {"repeat": "one"}),
        "repeat_off": ("repeat_set", {"repeat": "off"}),
    }
    if operation in simple:
        service, data = simple[operation]
        return [(service, data)], "", "done"
    if operation == "volume_set":
        level = volume_level(slots)
        if level is None:
            return [], SAID["missing_volume"], "missing_volume"
        return [("volume_set", {"volume_level": level})], "", "done"
    if operation in ("volume_up", "volume_down"):
        player = players[0]
        if player.volume is None:
            return [(operation, {})], "", "done"
        step = VOLUME_STEP if operation == "volume_up" else -VOLUME_STEP
        level = round(min(1.0, max(0.0, player.volume + step)), 2)
        return [("volume_set", {"volume_level": level})], "", "done"
    if operation == "status":
        player = players[0]
        if not player.media:
            return [], "I don't know what's playing.", "done"
        if player.state == "paused":
            return [], f"{_capital(player.media)} is paused.", "done"
        return [], f"This is {player.media}.", "done"
    raise ValueError(f"not a control: {operation}")


# What a control that worked says, brief (fixed, for the text-to-speech cache)
# and specific; verbose adds the players. By default controls beep.
CONTROL_SAID = {
    "pause": "Paused.",
    "resume": "Resumed.",
    "stop": "Stopped.",
    "next": "Next track.",
    "previous": "Previous track.",
    "volume_up": "Volume up.",
    "volume_down": "Volume down.",
    "volume_set": "Volume set.",
    "mute": "Muted.",
    "unmute": "Unmuted.",
    "shuffle_on": "Shuffle on.",
    "shuffle_off": "Shuffle off.",
    "repeat_all": "Repeating everything.",
    "repeat_one": "Repeating this track.",
    "repeat_off": "Repeat off.",
}
FOUND_SAID = {"play": "Playing music.", "add": "Added it to the queue.", "radio": "Playing similar music."}


def control_said(operation: str, slots: dict[str, Any], players: list[Player]) -> dict[str, str]:
    """A control that worked, by level of verbosity."""
    brief = CONTROL_SAID.get(operation, "Done.")
    specific = brief
    volume = volume_level(slots) if operation == "volume_set" else None
    if operation in ("volume_up", "volume_down") and players and players[0].volume is not None:
        step = VOLUME_STEP if operation == "volume_up" else -VOLUME_STEP
        volume = round(min(1.0, max(0.0, players[0].volume + step)), 2)
    if volume is not None:
        specific = f"Volume at {round(volume * 100)}%."
    elif operation in ("pause", "resume", "stop") and players and players[0].media:
        specific = f"{brief[:-1]} {players[0].media}."
    return {"brief": brief, "specific": specific, "verbose": _on_players(specific, players)}


def found_said(operation: str, item: Item, players: list[Player]) -> dict[str, str]:
    """What playing (or queueing) a search result says, by level of
    verbosity."""
    specific = {"add": f"Added {item.spoken} to the queue.", "radio": f"Playing music like {item.spoken}."}.get(
        operation, f"Playing {item.spoken}."
    )
    return {"brief": FOUND_SAID.get(operation, "Playing."), "specific": specific, "verbose": _on_players(specific, players)}


def moved_said(source: Player, target: Player, to: str) -> dict[str, str]:
    """A move, by level of verbosity; ``to`` is where, as heard: "to the
    Kitchen", "upstairs", "to Kitchen Speaker"."""
    what = source.media or "the music"
    return {
        "brief": "Moved the music.",
        "specific": f"Moved the music {to}." if to else f"Moved the music to {target.name}.",
        "verbose": f"Moved {what} from {source.name} to {target.name}.",
    }


def _on_players(sentence: str, players: list[Player]) -> str:
    """"Paused So What by Miles Davis on Kitchen Speaker."."""
    names = [p.name or p.entity_id for p in players]
    if not names:
        return sentence
    joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"{sentence[:-1]} on {joined}."


def volume_level(slots: dict[str, Any]) -> float | None:
    """0..1 from the percentage heard (``slots.percentage``, ``volume`` or
    ``number``)."""
    for key in ("percentage", "volume", "number"):
        value = slots.get(key)
        if isinstance(value, (int, float)):
            return round(min(100.0, max(0.0, float(value))) / 100, 2)
    return None


# ----------------------------------------------------------------- searching

_CUES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:some |any )?(?:songs|music|something|anything|stuff|tracks) (?:by|from) (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:the )?artist (?P<q>.+)$"), "artist"),
    (re.compile(r"^(?:the )?album (?P<q>.+)$"), "album"),
    (re.compile(r"^(?:the )?(?P<q>.+) album$"), "album"),
    (re.compile(r"^(?:the )?(?:song|track) (?P<q>.+)$"), "track"),
    (re.compile(r"^(?:the )?(?P<q>.+) (?:song|track)$"), "track"),
    (re.compile(r"^(?:the |my )?playlist (?P<q>.+)$"), "playlist"),
    (re.compile(r"^(?:the |my )?(?P<q>.+) playlist$"), "playlist"),
    (re.compile(r"^(?:the )?(?:radio station|station) (?P<q>.+)$"), "radio"),
    (re.compile(r"^(?:the )?(?P<q>.+) (?:radio station|station)$"), "radio"),
]
_LEADING = re.compile(r"^(?:me |us |some )+")
_TRAILING = re.compile(r"(?: please| for me| for us| on music assistant)+$")


def clean(text: Any) -> str:
    """What was heard, lowercase, without punctuation or politeness."""
    value = re.sub(r"[^\w' &-]+", " ", str(text or "").lower())
    value = re.sub(r"\s+", " ", value).strip()
    value = _TRAILING.sub("", value).strip()
    return _LEADING.sub("", value).strip()


def generic(text: Any) -> bool:
    """"play some music": nothing in particular."""
    return clean(text) in GENERIC or not clean(text)


def queries(text: Any, media_type: str | None = None) -> list[Query]:
    """The readings of what was asked for, most literal first. "So what by
    Miles Davis" is both a title and a title by an artist ("Stand by Me" is
    only the first)."""
    value = clean(text)
    if not value:
        return []
    types = [media_type] if media_type in SEARCH_TYPES else None
    if types is None:
        for pattern, kind in _CUES:
            match = pattern.match(value)
            if match and match.group("q").strip():
                value, types = match.group("q").strip(), [kind]
                break
    readings = [Query(value, value, None, types)]
    if " by " in value and (types is None or set(types) & {"track", "album"}):
        name, artist = value.rsplit(" by ", 1)
        if name.strip() and artist.strip():
            readings.append(Query(value, name.strip(), artist.strip(), types or ["track", "album"]))
    return readings


def items(response: Any) -> list[Item]:
    """Search results (``music_assistant.search``'s response), in order."""
    out = []
    groups = {"artists": "artist", "albums": "album", "tracks": "track", "playlists": "playlist", "radio": "radio"}
    for key, kind in groups.items():
        for found in (response or {}).get(key) or []:
            artists = [a.get("name") for a in found.get("artists") or [] if a.get("name")]
            out.append(
                Item(
                    str(found.get("name") or ""),
                    str(found.get("uri") or ""),
                    str(found.get("media_type") or kind),
                    ", ".join(artists) or None,
                )
            )
    return [i for i in out if i.name and i.uri]


_URI_TYPE = re.compile(r"^[^:]+://([a-z]+)/")


def browse_items(results: Any, media_types: list[str] | None = None) -> list[Item]:
    """``media_player.search_media`` results, in order. Music Assistant
    titles them "Artist - Name", and its URI says the type."""
    out = []
    for found in results or []:
        uri = str(found.get("media_content_id") or "")
        match = _URI_TYPE.match(uri)
        kind = match.group(1) if match else str(found.get("media_class") or "")
        if kind not in SEARCH_TYPES or (media_types and kind not in media_types):
            continue
        title = str(found.get("title") or "")
        artist = None
        if kind in ("album", "track") and " - " in title:
            artist, title = title.split(" - ", 1)
        if title and uri:
            out.append(Item(title, uri, kind, artist))
    return out


def best(readings: list[tuple[Query, list[Item]]]) -> Item | None:
    """The result closest to what was heard, over every reading."""
    scored: list[Item] = []
    for query, results in readings:
        positions: dict[str, int] = {}
        for item in results:
            position = positions.get(item.media_type, 0)
            positions[item.media_type] = position + 1
            item.score = _similarity(query, item) + TYPE_PRIOR.get(item.media_type, 0.0) + (0.02 if position == 0 else 0.0)
            scored.append(item)
    if not scored:
        return None
    top = max(scored, key=lambda i: i.score)
    return top if top.score >= MATCH_AT_LEAST else None


def _similarity(query: Query, item: Item) -> float:
    name = _norm(item.name)
    if query.artist:
        artist = _ratio(_norm(query.artist), _norm(item.artist)) if item.artist else 0.0
        return (_ratio(_norm(query.name), name) + artist) / 2
    heard = _norm(query.text)
    forms = [name]
    if item.artist:
        forms += [_norm(f"{item.name} by {item.artist}"), _norm(f"{item.artist} {item.name}")]
    return max(_ratio(heard, form) for form in forms)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


# --------------------------------------------------------- Home Assistant side

STATE_TEMPLATE = """[
{%- for e in ids -%}
{"entity_id": {{ e | tojson }}, "state": {{ states(e) | tojson }},
 "name": {{ state_attr(e, 'friendly_name') | tojson }},
 "title": {{ state_attr(e, 'media_title') | tojson }},
 "artist": {{ state_attr(e, 'media_artist') | tojson }},
 "album": {{ state_attr(e, 'media_album_name') | tojson }},
 "volume": {{ state_attr(e, 'volume_level') | tojson }},
 "muted": {{ state_attr(e, 'is_volume_muted') | tojson }}}{{ "," if not loop.last }}
{%- endfor -%}
]"""


class Music:
    """Music Assistant through Home Assistant: players' states, searching, and
    what voice paused on each satellite."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self._entry_id: str | None = None
        self._paused: dict[str, tuple[float, list[str]]] = {}
        self._search_media = False
        """``music_assistant.search`` failed: search through a player instead."""

    async def entry_id(self) -> str | None:
        """Music Assistant's config entry (searching needs it)."""
        if self._entry_id is None:
            entries = await self.hass.config_entries("music_assistant")
            loaded = [e for e in entries if e.get("state") == "loaded"] or entries
            self._entry_id = loaded[0]["entry_id"] if loaded else None
        return self._entry_id

    async def players(self, ids: list[str]) -> dict[str, Player]:
        if not ids:
            return {}
        result = await self.hass.render_value(STATE_TEMPLATE, {"ids": ids})
        if isinstance(result, str):
            result = json.loads(result)
        out = {}
        for state in result or []:
            out[state["entity_id"]] = Player(
                state["entity_id"],
                state.get("name") or state["entity_id"],
                str(state.get("state") or "unknown"),
                state.get("title"),
                state.get("artist"),
                state.get("album"),
                state.get("volume"),
                state.get("muted"),
            )
        return out

    async def find(self, text: str, media_type: str | None = None, player: str | None = None) -> Item | None:
        """The best match for what was heard, searching every reading of it.

        ``music_assistant.search`` gives each result's artists; when it fails
        (Home Assistant rejects its own response when Music Assistant leaves an
        album's ``favorite`` empty), ``player``'s ``media_player.search_media``
        is used from then on."""
        readings = queries(text, media_type)
        if not self._search_media:
            try:
                return best(list(await asyncio.gather(*(self._search(q) for q in readings))))
            except HassError as err:
                if player is None:
                    raise
                _LOGGER.warning("music_assistant.search failed (%s); searching through the player from now on", err)
                self._search_media = True
        if player is None:
            raise LookupError("No music player to search with")
        return best(list(await asyncio.gather(*(self._search_player(q, player) for q in readings))))

    async def _search(self, query: Query) -> tuple[Query, list[Item]]:
        entry_id = await self.entry_id()
        if entry_id is None:
            raise LookupError("Music Assistant isn't set up in Home Assistant")
        data: dict[str, Any] = {"config_entry_id": entry_id, "name": query.name, "limit": 5}
        data["media_type"] = query.media_types or SEARCH_TYPES
        if query.artist:
            data["artist"] = query.artist
        response = await self.hass.call_service("music_assistant", "search", data, None, return_response=True)
        return query, items(response)

    async def _search_player(self, query: Query, player: str) -> tuple[Query, list[Item]]:
        # Music Assistant reads "artist - name" as a name by an artist.
        data: dict[str, Any] = {"search_query": f"{query.artist} - {query.name}" if query.artist else query.name}
        classes = [t for t in query.media_types or [] if t != "radio"]
        if classes and len(classes) == len(query.media_types or []):
            data["media_filter_classes"] = classes
        response = await self.hass.call_service(
            "media_player", "search_media", data, {"entity_id": player}, return_response=True
        )
        found = ((response or {}).get(player) or {}).get("result")
        return query, browse_items(found, query.media_types)

    async def current(self, entity_id: str) -> Item | None:
        """What a player is playing, as an item to play more like."""
        response = await self.hass.call_service(
            "music_assistant", "get_queue", {}, {"entity_id": entity_id}, return_response=True
        )
        queue = (response or {}).get(entity_id) or {}
        media = ((queue.get("current_item") or {}).get("media_item")) or {}
        if not media.get("uri"):
            return None
        artists = [a.get("name") for a in media.get("artists") or [] if a.get("name")]
        return Item(str(media.get("name") or ""), media["uri"], str(media.get("media_type") or "track"), ", ".join(artists) or None)

    def remember_paused(self, satellite: str, ids: list[str]) -> None:
        self._paused[satellite] = (time.monotonic(), list(ids))

    def paused_by_voice(self, satellite: str) -> list[str]:
        when, ids = self._paused.get(satellite, (0.0, []))
        return ids if time.monotonic() - when < PAUSED_FOR else []

    def forget_paused(self, satellite: str) -> None:
        self._paused.pop(satellite, None)


def level_examples() -> dict[str, dict[str, str]]:
    """operation -> level -> what done says, for the UI (status answers;
    it has no levels)."""
    player = Player("media_player.kitchen", "Kitchen Speaker", "playing", "So What", "Miles Davis", volume=0.4)
    out: dict[str, dict[str, str]] = {}
    for operation in OUTCOMES:
        if operation == "status":
            continue
        if operation == "move":
            kitchen = Player("media_player.kitchen", "Kitchen Speaker")
            out[operation] = moved_said(Player("media_player.living_room", "Living Room Speaker", "playing", "So What", "Miles Davis"), kitchen, "to the Kitchen")
        elif operation in FIND:
            item = Item("Kind of Blue", "", "album", "Miles Davis") if operation == "play" else Item("So What", "", "track", "Miles Davis")
            out[operation] = found_said(operation, item, [player])
        else:
            out[operation] = control_said(operation, {"percentage": 30}, [player])
    return out
