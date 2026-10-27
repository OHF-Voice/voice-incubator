"""What a command needs its target to be able to do: its ``supports``.

Not every light changes color, not every cover can be set to a position, not
every thermostat has a fan setting. A command says what it needs, and for a
setting of a value list, the slot whose value the device must have::

    light_color:
      supports: color
    set_thermostat_mode:
      supports: {hvac_mode: mode}     # and have the mode said

Before it runs (voice_call/actions.py), what it acts on is checked against
what Home Assistant says each entity can do -- its state's
``supported_features``, ``supported_color_modes``, ``hvac_modes`` and
``fan_modes``, kept with the catalog (voice_call/hass.py):

* a device named, or the command's own or the home's: one that can't is left
  out, and with none left nothing runs (the outcome ``not_supported``);
* an area, a floor or the satellite's area: only what can is acted on, and
  with nothing there that can, ``not_supported``.

What was left out is said at the verbose level. What the app can't tell (an
entity it has no state for) is tried anyway: Home Assistant decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

COLOR_MODES = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww"})
BRIGHTNESS_MODES = COLOR_MODES | {"brightness", "color_temp", "white"}


@dataclass(frozen=True)
class Capability:
    domains: tuple[str, ...]
    """The entities that may have it."""
    features: int = 0
    """The ``supported_features`` bit that says so."""
    color_modes: frozenset[str] = frozenset()
    """A light's: any of these ``supported_color_modes``."""
    values: str | None = None
    """The attribute listing what it may be set to (``fan_modes``)."""
    now: str | None = None
    """The attribute with what it is set to (``fan_mode``; None: its state)."""


CAPABILITIES = {
    "brightness": Capability(("light",), color_modes=BRIGHTNESS_MODES),
    "color": Capability(("light",), color_modes=COLOR_MODES),
    # Home Assistant makes a white of a color light's colors.
    "color_temp": Capability(("light",), color_modes=COLOR_MODES | {"color_temp"}),
    "position": Capability(("cover", "valve"), features=4),
    "tilt": Capability(("cover",), features=128),
    "temperature": Capability(("climate",), features=1),
    "fan_mode": Capability(("climate",), features=8, values="fan_modes", now="fan_mode"),
    "hvac_mode": Capability(("climate",), values="hvac_modes"),
    "volume_set": Capability(("media_player",), features=4),
}

ATTRIBUTES = ("supported_features", "supported_color_modes", "hvac_modes", "fan_modes", "device_class")
"""What the catalog keeps of an entity's state: what it can do, and its device
class (which intents pick by)."""


def can(entity: Any, capability: str, value: Any = None) -> bool | None:
    """Whether the entity can (and has ``value``, when one is given); None
    when that isn't known: no state for it."""
    attributes = getattr(entity, "attributes", None)
    if attributes is None:
        return None
    need = CAPABILITIES[capability]
    if entity.domain not in need.domains:
        return False
    if need.color_modes:
        modes = attributes.get("supported_color_modes")
        if modes is None:
            return None
        if not need.color_modes & set(modes):
            return False
    if need.features and not int(attributes.get("supported_features") or 0) & need.features:
        return False
    if need.values:
        if value is not None:
            return own_value(entity, capability, value) is not None
        return bool(attributes.get(need.values))
    return True


def own_value(entity: Any, capability: str, value: Any, now: Any = None) -> str | None:
    """``value`` as the entity has it ("auto" may be its "Auto"); None when
    it has no such value. Of several it may mean, the one most like what it
    is set to ``now``: "low" is "auto_low" from "auto_high", "on_low" from
    "on_high" -- else the first it lists."""
    found = choices(entity, capability, value)
    if len(found) < 2:
        return found[0] if found else None
    words = set(_words(now)) if now is not None else set()
    return max(found, key=lambda v: len(set(_words(v)) & words))


def choices(entity: Any, capability: str, value: Any) -> list[str]:
    """The entity's values ``value`` may mean: the same, however it is
    written, else every one with all its words ("low": "on_low" and
    "auto_low")."""
    have = [str(v) for v in (getattr(entity, "attributes", None) or {}).get(CAPABILITIES[capability].values or "") or []]
    wanted = _words(value)
    same = [v for v in have if _words(v) == wanted]
    return same[:1] or [v for v in have if wanted and set(wanted) <= set(_words(v))]


def _words(value: Any) -> list[str]:
    return str(value).strip().lower().replace("-", " ").replace("_", " ").split()


@dataclass
class Check:
    """What of a command's target can do what it needs."""

    entities: list[str] | None = None
    """What to act on, each by name, in place of its target; None: as it is."""
    cannot: list[str] = field(default_factory=list)
    """What can't (entity ids): with ``entities`` empty, nothing can."""
    slots: dict[str, Any] = field(default_factory=dict)
    """The slots, with values as the entity has them and, of the devices
    named, those that can."""
    found: int = 0
    """How many it looked at: none, when nothing of the kind is there."""

    @property
    def nothing(self) -> bool:
        return self.entities == []


def check(
    needs: dict[str, str | None],
    slots: dict[str, Any],
    catalog: Any,
    areas: list[str] | None,
    kind: dict[str, Any] | None = None,
    now: dict[str, Any] | None = None,
) -> Check:
    """What of the target can: the devices in ``slots.entity``, else what is
    exposed to Assist in ``areas`` (None: the whole home) of the ``kind``
    (``domain``, ``device_class``: what the command's intent picks by).
    ``now``: what the devices named are set to, by entity id, for a value
    that could mean several of theirs."""
    entities = getattr(catalog, "entities", None)
    if not needs or entities is None:
        return Check(slots=slots)
    named = [str(e) for e in slots.get("entity") or []]
    if named:
        found = [(e, entities.get(e)) for e in named]
    else:
        domains = set(_listed((kind or {}).get("domain"))) or {d for c in needs for d in CAPABILITIES[c].domains}
        classes = set(_listed((kind or {}).get("device_class")))
        exposed = getattr(catalog, "exposed", None)
        found = [
            (e.entity_id, e)
            for e in entities.values()
            if e.domain in domains
            and (areas is None or e.area_id in areas)
            and (exposed is None or e.entity_id in exposed)
            and (not classes or (e.attributes or {}).get("device_class") in classes)
        ]
        if not found:
            return Check(slots=slots)  # nothing of the kind there: the intent says so
    able, cannot, spelled = [], [], dict(slots)
    for entity_id, entity in found:
        verdicts = [can(entity, c, slots.get(slot) if slot else None) for c, slot in needs.items()]
        if False in verdicts:
            cannot.append(entity_id)
        else:
            able.append(entity_id)
    if len(able) == 1:
        # Set as the device has it: "auto" may be its "Auto".
        for capability, slot in needs.items():
            if slot and slots.get(slot) is not None:
                spelled[slot] = own_value(entities.get(able[0]), capability, slots[slot], (now or {}).get(able[0])) or slots[slot]
    if not cannot:
        return Check(slots=spelled, found=len(found))
    if named:
        if able:
            spelled["entity"] = able
        return Check(entities=None if able else [], cannot=cannot, slots=spelled, found=len(found))
    return Check(entities=able, cannot=cannot, slots=spelled, found=len(found))


def unsure(needs: dict[str, str | None], slots: dict[str, Any], catalog: Any) -> dict[str, str | None]:
    """The devices named whose value said could mean several of theirs, each
    with the attribute saying what it is set to now (None: its state)."""
    entities = getattr(catalog, "entities", None) or {}
    out: dict[str, str | None] = {}
    for entity_id in [str(e) for e in slots.get("entity") or []]:
        for capability, slot in needs.items():
            if slot and slots.get(slot) is not None and len(choices(entities.get(entity_id), capability, slots[slot])) > 1:
                out[entity_id] = CAPABILITIES[capability].now
    return out


def _listed(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
