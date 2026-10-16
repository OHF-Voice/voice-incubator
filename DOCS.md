# Voice Call

Voice satellites (ESPHome `voice_call`) with commands you make yourself,
recognized on this machine. A command says what to listen for, where, and what
happens in Home Assistant: speak a response, run a script, activate a scene,
or run an action.

Open **Voice Call** in the sidebar to make commands, name things, set up
satellites and look at calls.

## The Voice Call integration

Install the **Voice Call** integration in Home Assistant with HACS (it lives in
this app's repository, under `custom_components/voice_call`) and restart Home
Assistant; the app sets it up the next time it connects. It gives each
satellite:

- a **device** of its own, with its own area. A satellite that is also an
  ESPHome device appears twice: Home Assistant no longer merges devices across
  integrations. The new device starts in the ESPHome device's area, and has its
  own from then on.
- a **timer list** (`voice_timer_list.<satellite>_timers`) holding its timers.

Without the integration the app still works, but satellites have no timers and
their areas are kept by the app.

## Satellites

Satellites on the network are found and connected to automatically. On the
**Satellites** page, give each one a name and an area (with the integration,
that is the area of its device in Home Assistant, and can be changed in either
place). The area is what "the lights" means there, and what commands scoped to
an area follow. The floor comes from the area.

### Wake words

Each satellite listens for one or more wake words: picked for every satellite
at the top of the Satellites page, and per satellite below it. A satellite
holds up to four (two with firmware from before this version).

- **Okay Nabu, Hey Jarvis, Hey Mycroft** are built in. Every time the
  satellite thinks it heard one, the app checks again against words that
  sound like it ("hey travis", "hey microsoft"), so the satellite can listen
  eagerly and still hear you across the room.
- **Custom wake words**: a microWakeWord model (`.tflite`) and its manifest
  (`.json`), as ESPHome and the Tater Wake Words catalog share them. Upload
  both together on the Satellites page, or copy them into `wake_words/` in
  the app's configuration folder (`addon_configs/..._voice_call` over Samba).
  They are not checked a second time, so they run at the cutoff in their
  manifest -- the one they were trained for. A model the satellite cannot run
  is refused when uploaded, and marked "can't run" when copied.

### Listening and speaking

The app listens and speaks with models of its own, or with Home Assistant's
**speech-to-text** and **text-to-speech** entities: chosen for every satellite
at the top of the Satellites page, and per satellite below it.

- **Speech-to-text.** Commands are always matched on the satellite's audio
  first. The STT entity writes what the built-in TDT model would otherwise:
  example sentences said word for word, free text ("play …", timer names),
  and hints. It is only asked when one of those is needed, so "turn on the
  lights" never waits for it. Should it fail, the built-in model stands in.
- **Language.** Each entity is asked in the language you pick from the ones it
  supports, or automatically in its best match for English.
- **Built-in speech model.** Without a speech-to-text entity, the app's own
  model writes that text: English, or **multilingual** (25 European
  languages, found by itself) at about three times the time and 600 MB more
  memory. Commands are still matched by the English model first, so in
  another language a command is matched by its example sentences, said word
  for word.
- **Text-to-speech.** Responses are spoken with the TTS entity (and the voice
  you pick) on the satellite's own media player -- Home Assistant sends it the
  audio's address and it plays it -- or, per satellite, on **another media
  player**. On its own player, the satellite finishes speaking before the
  call ends, and music turns down meanwhile. A command with nothing
  to say still plays its short sound on the satellite itself.

## Commands

Start from the **Library** (adding one copies it into your commands, with any
shared value list it uses that you don't have yet; one you already have is
marked **added**, by its id, so a command you renamed counts as your own and
the library's can be added again) or make a new one. A
command has:

- **Icon**: to tell commands apart at a glance; by default, one for what it
  does (a timer, music, a script ...).

- **What it does**: a verb phrase, "turn on the lights". It is also how a near
  miss is described: "It looks like you're trying to turn on the lights, but I
  didn't hear which room."
- **Where**: every satellite, or only some areas or satellites.
- **Target**: entities, areas or floors the command acts on. They reach Home
  Assistant as slots, as if they had been heard, so the phrase can be anything
  ("cooking mode") while the target stays fixed.
- **Listen for**:
  - **Example sentences**, said word for word. An example always matches when
    it is heard, ahead of keyword matches. Examples have no placeholders.
  - **Keywords**: groups that must be heard (any one alternative of each), in
    any order, plus optional groups and words that must not be heard ("is",
    so "the door is locked" isn't "lock the door"). Write `turn ... on` for
    words that may be split: "turn the lights on".
  - **Names**: a device, area or floor, from your names (see below), optional
    or required, limited to some of them or (for devices) to some domains.
  - **Number**: a preset (`percentage`, `temperature`, `duration`, `count`)
    or a range. A duration is heard as "ten minutes" or "an hour and a half"
    and arrives as `slots.duration` with `hours`, `minutes`, `seconds`,
    `total_seconds` and `text` (`HH:MM:SS`).
  - **Value lists**: "set the lights to red" gives `slots.color: red`. A
    value may be said several ways (`red: red, crimson`); lists can be shared.
  - **Free text**, kept bounded so it can be heard reliably: between two
    keywords ("add … to the shopping list"), from a keyword to the end
    ("remind me to …"), or right before or after a keyword ("the … timer").
    It is spelled from the TDT transcript.
- **What happens**: only respond, run a script, activate a scene, run an
  action (Home Assistant script syntax), a timer operation or a music
  operation (below), plus an
  optional **response** template that is spoken. Nothing to say plays a short
  sound.
- **Responses by outcome**: what is said can depend on how it went. Every
  command has **done** (the response above) and **failed** (Home Assistant
  refused; "Sorry, that didn't work." unless you say otherwise). Timer
  commands have their own outcomes (below), and a script can pick one by
  returning `outcome`: `stop` with `response_variable` holding
  `{outcome: nobody_home}`. An outcome without a response of its own says the
  built-in sentence (a timer's, or the script's `speech`), never the done
  one.
- **Overrides**: what happens differently in some areas or on some
  satellites, or "not available here". A satellite's override wins over its
  area's, which wins over the command.
- **Command groups**: any number of the groups the command is in (below).

### Command groups

A command group is a set of commands -- "Kids", "Guests", "Night" -- made on
the **Commands** page, where you tick the commands in it. A command can be in
any number of groups; its editor has the same ticks.

Groups are given on the **Satellites** page, to the whole home or to one
satellite:

- A satellite with groups of its own runs **only** the commands in them.
- A satellite without its own uses the groups given to the whole home.
- With no groups given anywhere, every command is available, as before.

A command outside a satellite's groups is not run there: it says "It looks
like you're trying to …, but that isn't set up here." `where` and overrides
still apply inside a group. Deleting a group takes every command out of it and
takes it away from the home and the satellites.

### What Home Assistant gets

The action, the script and the response template all see:

| variable | |
|---|---|
| `slots` | what was heard plus the fixed target: `entity`, `area`, `floor` (lists of ids), the number (by preset name), value lists, free text |
| `satellite` | `id`, `name`, `area_id`, `area_name`, `floor_id`, `floor_name`, `device_id`, `timer_list`, `media_player` |
| `speaker` | `name` of the enrolled voice (or none) and `score` |
| `target` | `entity_id`/`area_id`/`floor_id` from the slots, or else the satellite's area: `target: "{{ target }}"` |
| `command` | `id`, `description`, `text` (what was heard) |
| `result` | in the response only: what a script returned (`stop` with `response_variable`), plus `outcome`, `speech` (what would be said without your response) and, when it failed, `error` |

A script is called with these as its data, so its fields see them. If it
returns `speech`, that is spoken when the command has no response of its own.
Every command that runs also fires a `voice_call_command` event with the same
data plus `outcome` and `response` (what was said), failed ones included.

## Timers

Add the commands from the library's **Timers** group. Timers belong to the
satellite that set them: "set a pizza timer for ten minutes", "how long is
left", "add two minutes to the pizza timer", "pause the timer", "cancel all
timers". A timer is picked out by:

- its **name**, recognized from the names of the timers that are running (the
  `running_timers` list, which the app keeps current), or else heard as free
  text and matched loosely ("lasaga" is the lasagna timer);
- its **length** when it was set: "the 5 minute timer";
- or it is the **only one**.

An **alarm** is a timer that rings at a clock time: "set an alarm for seven
thirty", "wake me up at 6 am", "an alarm for half past six" (without am or pm,
the next time it comes round). It is named "alarm" and can be cancelled like
any timer.

When several timers fit, the satellite asks which ("There's the pizza timer and
the 5 minute timer"). Timer commands say how it went, and each outcome can have
a response of your own (the editor shows what is said otherwise); templates
also see `timer` (the timer it was about) and `timers`:

| outcome | when |
|---|---|
| `done` | it was done (or, for "how long is left", said) |
| `no_timers` | nothing is running on this satellite |
| `not_found` | the timer asked for, by name or length, isn't running |
| `ambiguous` | several fit; it asks which |
| `already` | pausing a paused timer, or resuming a running one |
| `missing_duration` | starting a timer or adding time with no length heard |
| `missing_time` | an alarm with no time heard |
| `failed` | Home Assistant refused, or the integration isn't set up |

Between calls the satellite's light ring shows the time left on the timer that
finishes soonest, and it flashes while one rings. A finished timer says which
it was ("The pizza timer is done.") and then **rings** until someone says
**"stop"** -- the satellite listens for it while
ringing -- or "Okay Nabu" anything, or presses the center button, or five
minutes pass. Timers are held in
memory by Home Assistant, so a restart loses them.

Automations can follow timers with the `voice_call.timer_created`,
`timer_finished`, `timer_paused`, `timer_unpaused`, `timer_time_changed` and
`timer_cancelled` triggers, and act on them with the `voice_timer_list.*`
actions (`create_timer`, `cancel_timer`, `get_timers` ...).

## Music

Music plays on **Music Assistant**'s players. A satellite running a Sendspin
client (the Voice Call firmware does) shows up in Music Assistant by itself,
and the Voice Call integration finds that player from the satellite's MAC
address; the Satellites page shows it and can pick another. Add the commands from the
library's **Music** group:

- "play Miles Davis", "play the album Kind of Blue", "play my chill
  playlist", "play So What by Miles Davis": Music Assistant is searched for
  what was heard and the closest match plays ("Playing Kind of Blue by Miles
  Davis."). "Add … to the queue", "play more like this".
- "pause", "resume", "stop the music", "next", "go back", "louder", "set the
  volume to 30 percent", "mute", "shuffle", "repeat", "what's playing".

The player is the satellite's own, unless a command names another (Target)
or an area. Pausing, skipping, the volume and "what's playing" act on
whichever Music Assistant player is playing in the satellite's area when its
own is not, and "resume" resumes what was paused by voice. A player grouped
with others pauses and skips with its group.

Like timers, every music command has outcomes, each with a sentence you can
replace: `done` (controls play a short sound), `no_player`,
`nothing_playing`, `not_found`, `missing_query`, `missing_volume`, `already`
and `failed`. Templates also see `player` (the one it acted on: `entity_id`,
`name`, `state`, `title`, `artist`, `volume` ...), `players` and, when it
searched, `media` (`name`, `artist`, `media_type`, `uri`).

On the satellite, music is turned down while it listens to you or a timer
rings, never paused, and the center button pauses and resumes it.

## Covers and locks

The library's **Covers and locks** group opens and closes covers and locks and
unlocks locks, and answers "is the garage door open?", "are the blinds closed
in the bedroom?", "is the front door locked?" and "is everything locked?". The
**Basics** group answers "are any lights on (upstairs)?".

- **By name** ("open the garage door", "lock the front door"): add a name for
  the cover or lock on the Names page. A binary sensor of the lock class can
  answer "is it locked" too (on means unlocked); type its entity id there.
- **By type** ("close the blinds", "open the garage door", "close all the
  curtains upstairs"): no names needed. The cover's device class says what it
  is; the area or floor you name, else the satellite's area, says which. When
  the satellite's area has none of that type, every one in the home is meant.
  When there are none, it says so.
- Unlocking works for anyone the satellite hears: scope the command to the
  satellites that should.

## When nothing matches

Keywords and example sentences come first. When they find nothing, two more
steps can run a command -- but only a command you chose for them: tick
"meaning" or "LLM" on the command's page, or many at once with **Choose
commands** under "When nothing matches" on the **Commands** page. Commands
from the library are not chosen until you choose them.

- **Meaning** runs the chosen command a sentence is very close to, when it
  needs nothing said: "kill the lights" for "turn off the lights". A command
  that needs a name, number, value or text can't be run this way.
- **A small LLM** picks one of its chosen commands and fills in what it
  needs: "throw on my workout playlist", "take milk off the list", "wake me
  up at six". Granite 4.0 350M by default -- the same model that answers
  questions, so nothing more to download -- or Hammer 2.1 0.5B, usually the
  more careful of the two but licensed for non-commercial use only. With
  more than 8 commands chosen (the number is a setting) it is given the 8
  closest in meaning to the sentence. On a Raspberry Pi 5 it adds about a
  second to what nothing else understood with a few commands chosen, several
  with many. It can pick the wrong command, so choose the ones it would do
  little harm to run by mistake. It only runs commands the satellite may
  run, close enough in meaning to what was said, and never with a name,
  value or number you didn't say.

Both are on, and do nothing until a command is chosen for them. A call's trace
shows what each step tried.

## Skills

The **Skills** tab: questions no command takes, answered exactly and at once,
without an LLM. Each is computed, never guessed -- a question a skill can't
read whole is left to the next step:

- **Spelling**: "how do you spell necessary".
- **Dates**: "what day is Christmas", "is 2028 a leap year", "how many days
  until Halloween".
- **Clock arithmetic**: "what time is 90 minutes after 2 pm".
- **Unit conversions**: "how many cups in a quart", "100 Fahrenheit in
  Celsius", "5 miles in kilometers".
- **Math**: "what's twelve times eight", "15 percent of 80".
- **Can't look it up**: questions about now or here -- the weather, news,
  scores, prices, what's open -- get "Sorry, I can't look that up" (unless a
  command of yours answers them, like the library's weather command).

Each is on unless you turn it off, for the whole home or for one satellite:
a satellite set "on" or "off" for a skill keeps that whatever the home's
setting.

## Questions

What no command or skill answers, when it reads as a question, goes to a
small LLM (Commands page, "Questions"), answered in one sentence: Granite 4.0
350M (downloaded on first start, 370 MB; about half a second on a Raspberry
Pi 5), or Gemma 3 1B on a faster machine. It answers about 9 in 10 everyday
questions right, and can be confidently wrong. Questions about now or here
are never asked.

## Names

What things are called when spoken, each pointing at one Home Assistant id.
Nothing is taken from Home Assistant automatically: search for an entity,
area or floor and its name and aliases are offered as a starting point. Add
the spellings the speech model actually uses as aliases ("Echo Bee" for
ecobee). Devices are limited to lights, switches, fans, covers, locks,
climate, media players, scenes, scripts, input booleans, valves and vacuums;
`commands.yaml` may name anything.

## Trying, calls and debugging

- **Try** speaks a sentence with the app's voice and recognizes it as if said
  on a satellite, without running anything unless you ask.
- **Calls** lists recent calls with every stage: wake word verification, where
  the command was cut, the speaker, every command that matched or was
  rejected and why, what ran and what was said.
- Turn on **debugging** on the Calls page to keep every call's audio, or start
  a **debug session** on one satellite (Satellites page). A session keeps that
  satellite's audio and can be a **dry run**: nothing is executed, and you hear
  what would have been said. Kept calls show their waveform with the wake word,
  the cut and the recognized words marked, and can be **replayed** through
  today's commands.

## YAML

Everything the UI edits is in `/data/commands.yaml` (shown on the YAML page),
and you may edit it directly; comments survive saves from the UI.

```yaml
names:
  entities:
    climate.ecobee: {name: EcoBee, aliases: [Echo Bee, Thermostat]}
  areas:
    kitchen: {name: Kitchen}
lists:
  colors: {red: [red, crimson], blue: blue}
groups:
  kids: {name: Kids}
commands:
  ecobee_warmer:
    description: warm up the house
    slots: {entity: [climate.ecobee]}
    examples: [it's cold in here]
    require: [[warmer, warm up]]
    action:
      action: climate.set_temperature
      target: "{{ target }}"
      data: {temperature: 22}
    response: Warming up.
  lights_on:
    description: turn on the lights
    groups: [kids]
    require: [[turn on, turn ... on], [light, lights]]
    names: {area: {count: 1, optional: true}}
    action: {action: light.turn_on, target: "{{ target }}"}
    overrides:
      - scope: {satellites: [aabbccddeeff]}
        action: {action: light.turn_on, target: {entity_id: light.desk_lamp}}
  pause_timer:
    description: pause a timer
    require: [[pause], [timer]]
    lists: {timer_name: {list: running_timers, optional: true}}
    timer: pause
    response:
      done: Paused.
      no_timers: There's nothing to pause.
      already: "{{ result.speech }} Say resume to start it again."
```

## Options

- `wake_cutoff`: the satellite detector's probability cutoff for the built-in
  wake words (default: each its own -- 0.2 for Okay Nabu, 0.5 for Hey Jarvis,
  0.7 for Hey Mycroft). Every detection of these is verified again here, so it
  can be generous. Custom wake words keep their own.
- `speaker_threshold`: how closely a voice must match to be identified.
- `silence_seconds`: silence that ends a command.
- `debug_logging`: verbose logs.
