# Voice Call

Voice satellites (ESPHome `voice_call`) with commands you make yourself,
recognized on this machine. A command says what to listen for, where, and what
happens in Home Assistant: speak a response, run a script, activate a scene,
or run an action.

Open **Voice Call** in the sidebar to make commands, name things, set up
satellites and look at sessions.

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

It also runs the commands' **intents** for the app, as Assist does (see
**Intents** below).

Without the integration the app still works, but satellites have no timers and
their areas are kept by the app; intents still run, through Home Assistant's
REST API, but can't tell an unavailable device apart.

## The Home and its satellites

The **Home** page starts with a tile for each satellite -- its name, area
and wake words -- then the **commands and skills**, grouped by what they're
for (your own first, under **Custom**), and the **Unknown command** at the
bottom. Type in the search box above the list to find commands and skills
by their title, description, example sentences or keywords, and pick a
satellite to see only what runs there.

Satellites on the network are found and connected to automatically. Click a
satellite's tile for its page:

- **Settings**: its name, icon and area (with the integration, that is the
  area of its device in Home Assistant, and can be changed in either place),
  its music player, and how it listens and speaks. The area is what "the
  lights" means there. The floor comes from the area.
- **Test it**: type a sentence as if said there, listen to it live, and
  debug it.

The home's **weather entity**, **thermostat**, **shopping list** and
**to-do list** are on the **Settings** page (Home): the library's commands
for them use these unless one is given its own implied target (`home:
weather`, `climate`, `shopping_list` or `todo_list` on a command). With
neither, the satellite says "That isn't set up yet." (`not_set`).

A command or skill **runs on** every satellite, on some, or on none (turned
off), as set on its page. Where two take the same sentence, the one on some
satellites goes first on them: to have a command do something else on one
satellite, duplicate it, put the copy on that satellite and change it.

### Wake words

Each satellite listens for the home's **preferred** wake word, or for up to
five of its own (three on older firmware). Both are picked on the **Wake
words** page, which is also where each wake word is tuned.

- **Okay Nabu, Hey Jarvis, Hey Mycroft** are built in. Every time the
  satellite thinks it heard one, the app checks again against **confusables**
  -- phrases that sound like it ("hey travis", "hey microsoft") -- so the
  satellite can listen eagerly and still hear you across the room.
- **Custom wake words**: a microWakeWord model (`.tflite`) and its manifest
  (`.json`), as ESPHome and the Tater Wake Words catalog share them. Upload
  both together on the Wake words page, or copy them into `wake_words/` in
  the app's configuration folder (`addon_configs/..._voice_call` over Samba).
  They run at the cutoff in their manifest -- the one they were trained for
  -- and are not checked a second time until you give them confusables. A
  model the satellite cannot run is refused when uploaded, and marked "can't
  run" when copied.

**Train** one of your own on the Wake words page (in English): type the
phrase ("hey computer") and the app makes the model. Piper says it 2,000
ways, in many voices, rooms and noise, and a model learns it against
speech, music and noise, starting from the base model the built-in words
come from. It takes about 20 minutes on a Raspberry Pi 5, every core of it
and about 2 GB of memory (a Pi without a fan slows down by a third or more),
and about 70 minutes on a Home Assistant Green; the app goes on answering
meanwhile, more slowly. The phrase field suggests what has been recorded on
the People page -- everyone's takes of a phrase, or one person's ("okay nabu
(Mike)") -- and a name of its own ("Okay Nabu (Mike)") keeps it apart from the
others of the same phrase; without one, it is called by its phrase. The new word is added as a
custom one, at a cutoff of 0.8 and not checked a second time. How well it
does can't be told before it is tried: try it from where people stand, tune
its cutoff, give it confusables, and train it again if it disappoints (two
trainings of the same phrase can differ). Training needs the **training
kit** -- the base model and the speech, music and noise it trains against,
about 3 GB -- in `wake_training/` in the app's configuration folder; the page
says when it is missing.

Open a wake word on the Wake words page to change:

- **Its cutoff** on the satellite: how sure the satellite's own model must be
  before it wakes. Lower hears it from further away, and fires more often on
  other speech: for a checked wake word the app refuses those, but the ring
  lights and the music ducks while it checks.
- **Its confusables.** Add a phrase people say near the satellite that wakes
  it by mistake ("okay nadine"); remove one that refuses the real wake word
  too. A word the checker doesn't know ("nabu") needs a pronunciation: the
  page guesses one, which you can correct, in the phones it lists. Built-in
  wake words can go back to their own list.
- **Its thresholds**: how close a confusable may come, how well each part of
  the wake word must fit, and when the speech model may overturn a refusal.
  They were measured for Okay Nabu on a Voice PE: change them a little at a
  time, and test from where people stand.

Saving rebuilds the wake word's checker, a few seconds on a Raspberry Pi;
**Restore its defaults** undoes everything changed. What you change is kept
in `wake_words.json`.

### Listening and speaking

The app listens and speaks with models of its own, or with Home Assistant's
**speech-to-text** and **text-to-speech** entities: chosen for every satellite
on the Settings page, and under **Input** and **Output** on each satellite's
page.

- **Speech-to-text.** Commands are always matched on the satellite's audio
  first. The STT entity writes what the built-in TDT model would otherwise:
  example sentences said word for word, free text ("play …", timer names),
  and hints. It is only asked when one of those is needed, so "turn on the
  lights" never waits for it. Should it fail, or hear nothing, the built-in
  model stands in.
- **Runs.** **When needed** (the default): the command is sent to the
  speech-to-text entity after you finish speaking, and only when its words
  are needed. **Always, as you speak**: it hears every command while you say
  it, streamed from just after the wake word. Every session then has its
  transcript (in the list, and the Recognition step: what it heard, even
  when it wasn't needed), and one that is needed is ready as soon as the
  entity answers, with nothing left to send. Everything else is the same:
  commands are still matched on the audio first. Every command goes to the
  entity, which matters for a cloud one. For every satellite on the Settings
  page, or one satellite's own under Input.
- **Audio.** What the speech-to-text entity hears: **AGC** (the default), the
  microphone with automatic gain that commands are matched on; or **AEC**,
  echo-cancelled, with the satellite's own playback (its music, its voice)
  taken out. AEC is recorded about 25 dB quieter: send it as captured to an
  entity that levels its audio itself, or **AEC, boosted** (to a -3 dBFS
  peak) to one that doesn't. The same stretch of audio is sent either way,
  from where the wake word ended (as the wake word check placed it): the AEC
  capture runs 56.5 ms ahead of the AGC one, and is cut to match.
  Sound from other speakers, such as music on another player, is in both.
  For every satellite on the Settings page, or one satellite's own under
  Input; a session's Recognition step says which was sent.
- **Language.** Each entity is asked in the language you pick from the ones it
  supports, or automatically in its best match for the app's language.
- **Built-in speech model.** Without a speech-to-text entity, the app's own
  model writes that text: the app's language, or **multilingual** (25
  European languages, found by itself) at about three times the time and 600
  MB more memory. Commands are still matched by the app's language's model
  first, so in another language a command is matched by its example
  sentences, said word for word.
- **Text-to-speech.** Responses are spoken with the TTS entity (and the voice
  you pick) on the satellite's own media player -- Home Assistant sends it the
  audio's address and it plays it -- or, per satellite, on **another media
  player**. On its own player, the satellite finishes speaking before the
  call ends, and music turns down meanwhile. A command with nothing
  to say still plays its short sound on the satellite itself.

### End of speech

A satellite stops listening once you have started speaking and then gone
quiet long enough. **End of speech**, on the Settings page, sets how:

- **Detector**: **Silero VAD** (the default) judges 32 ms of audio at a
  time and notices the end of speech soonest. **microVAD**, microWakeWord's
  voice activity detector, judges 10 ms at a time and is smoother, but
  notices the end about half a second later, so give it a shorter silence;
  it says nothing for its first 0.76 s.
- **Speech at** (0.5) and **Silence below** (0.35): audio at least this
  likely to be speech is speech, and speech lasts until the likelihood falls
  below the second; in between, nothing changes.
- **Speech to start** (0.2 s): speech before the command has begun. Until
  then, silence ends nothing.
- **Silence to end** (the app's `silence_seconds` option, 1 s): silence
  after speech that ends the command.
- **Speech to resume** (0.1 s): speech in a row that starts that silence
  over; a shorter sound (a cough, a click) only pauses it.
- **Longest command** (15 s, from the wake word): it stops listening then,
  whatever it heard.

A session's **End of speech** step says which detector ended it, and with
what. A session that reaches the longest command says why: the audio stopped
arriving, the wake word was still being checked, no speech was heard, or
speech never ended in enough silence.

## People

The **People** page lists who lives here: someone with things of their own,
an enrolled voice, or both. Add someone by name; their page sets:

- **Their own**: a to-do list, a calendar and a Music Assistant playlist.
  They are what "my" means when they speak: **Add to my to-do list** ("add
  call mom to my to-do list"), **Remind me**, **What's on my calendar** and
  **Play my playlist** act on the speaker's own. Where nobody has one of
  their own, those act on the command's own (the to-do list it was given);
  where someone does, the speaker must be known -- "Sorry, I don't know
  who's speaking." otherwise -- and someone without one gets the command's
  own. "The to-do list" and "the shopping list" are always the command's.
- **Replies**: the text-to-speech entity and voice replies to them are said
  in, in place of the satellite's.
- **Wake words**: wake words that are theirs, such as one trained on their
  recordings ("Okay Nabu (Mike)").
- **Voice**: enrolled on the People page, as below.

**Who is speaking**: the person their voice is recognized as; else the one
whose wake word started it; else, when the home has only one person, them --
unless their voice was heard and it wasn't theirs. With one person, no voice
is needed: enroll it to tell guests apart.
A session's Speaker step says who it was and how.

The library's "my" commands act on the speaker's own with `mine`
(`todo_list`, `calendar` or `playlist`): their to-do list or calendar
replaces its `slots.entity` (else the command's own, else the home's), their
playlist its `slots.query`. Its response
may say something of its own when nobody is known (`no_speaker`). The "my"
commands are in the English library for now; **Remind me** is personal in
every language.

**Voices**: on the People page, pick a name and a satellite and read the
passage aloud in your normal voice, walking slowly around the room, for about
a minute; the ring blinks red while it listens. Enrolling again on another
satellite adds to the same voice. Each voice has a ring color, shown while
its commands are processed. Someone enrolled can also record a wake phrase
there, for training a wake word that hears them better.

## Commands

The Home page lists the commands as the library has them -- Basics, Covers
and locks, Music, Timers -- with the ones you made from scratch first, under
**Custom**, and the skills under **Questions**. A command from the library
stays in its section when you change it, rename it or duplicate it.

**Add**, on the Home page or a satellite's, adds to that place: the
library's commands and skills -- search them, or show one group (Basics,
Covers and locks, Music, Timers, Questions) -- and, always at the top, a
custom command made from scratch. On a satellite's, what it already has from
the Home is marked. Most of the
library's commands come in **versions**, for what they act on; open one
("Play music") to pick:

- **on this satellite** -- nothing named: the satellite's own player, or the
  lights in its area ("play jazz", "turn on the lights");
- **on a device by name** ("play jazz on the Mark 2", "turn on the desk
  lamp");
- **in an area or floor by name** ("play jazz in the kitchen", "turn on the
  lights upstairs").

Each version is a command of its own (`play_music_here`,
`play_music_device`, `play_music_place`): add the ones you want, and a
sentence goes to the one that takes what was said in it.

A command added from the library **follows it**, as an automation follows
its blueprint in Home Assistant: the library's changes reach it -- fixes,
better keywords -- and it is in the app's language, whichever that is.
Yours to set on its page: where it runs, how much it says, the steps when
nothing matches, example sentences of your own (beside the library's), its
implied target, and its icon. The rest is shown, not changed: **Take
control** (at the top of its page) makes it yours to change all of it, and
the library's changes no longer reach it. Commands added from the library
before this version were made to follow it, with what you had set kept; the
file as it was is `commands.before-library.json`.

One you already have is marked **added**, and can be added again. Tick several to add them together; in a group,
**Select the … not added** ticks each command you have no version of (the
version for the satellite it's said to). Under each section of the Home page
you have commands from, **Not added** names the rest of it, to add one or
all. A command has:

- **Icon**: to tell commands apart at a glance; by default, one for what it
  does (a timer, music, a script ...).

- **Description**: a verb phrase, "turn on the lights". It is also how a near
  miss is described: "It looks like you're trying to turn on the lights, but I
  didn't hear which room." Meaning and the model (When nothing matches) tell
  commands apart by it. Its id is made from it, and never shown.
- **Runs on**: every satellite, some, or none (turned off).
- **Listen for**:
  - **Example sentences**, said word for word. An example always matches when
    it is heard, ahead of keyword matches -- but only as all that was said:
    the example "pause" is not "pause the timer", which is the timer's.
    An example may be a template, as in Home Assistant's sentences: `(a|b)`
    is either, `[a]` may be left out, inside a word too --
    `(turn|switch) on the light[s]` is four sentences, each matched word for
    word. The editor shows what each template stands for. Examples have no
    placeholders (`{area}`): slots come from keywords. Each sentence costs a
    little on every call, so a template may stand for 512 at most.
    **Also sentences that mean the same**, under them, lets meaning run it
    when nothing else matched (When nothing matches).
  - **Keywords**: groups that must be heard (any one alternative of each), in
    any order, plus optional groups and words that must not be heard ("is",
    so "the door is locked" isn't "lock the door"). Write `turn ... on` for
    words that may be split: "turn the lights on".
  - **Names**: a device, area or floor, from your names (see below), limited
    to some of them or (for devices) to some domains.
  - **Number**: a preset (`percentage`, `temperature`, `duration`, `count`)
    or a range. A duration is heard as "ten minutes" or "an hour and a half"
    and arrives as `slots.duration` with `hours`, `minutes`, `seconds`,
    `total_seconds` and `text` (`HH:MM:SS`).
  - **Value lists**: "set the lights to red" gives `slots.color: red`. A
    value may be said several ways (`red: red, crimson`); lists can be shared.
    Every home has its language's own -- `colors` (red, orange, yellow, green,
    blue, purple, pink, white, turquoise, magenta), `color_temperatures`
    (English: warm white and the rest, in kelvin), `on_off`, `open_closed`,
    `locked_unlocked`, `cover_classes` and `hvac_modes` (heat, cool,
    heat_cool, auto, dry, fan_only, off) -- and editing one on the Names
    page makes it yours, in the app's language. A value's phrases in another
    language than your commands' go under the language's code
    (`red: {en: [red], de: [rot]}`); only the app's language's are heard.
  - **Free text**, kept bounded so it can be heard reliably: between two
    keywords ("add … to the shopping list"), from a keyword to the end
    ("remind me to …"), or right before or after a keyword ("the … timer").
    It is spelled from the TDT transcript. **Also when the model hears its
    free text or values**, under it, lets the model run it when nothing else
    matched (When nothing matches); it is offered on a command with free text
    or a value list.
  - **A device in a room**: "turn on the fan in the office" picks the Fan
    that Home Assistant puts in the office -- names may repeat, one Fan per
    room. When none is there, nothing runs and the satellite says so ("…but
    there's no Fan in the Kitchen"). Without a room, a name several devices
    share is the one in the satellite's own room. The room only picks the
    device out: Home Assistant is asked to turn on the fan, not the office.
    The library's device, cover, lock, brightness and thermostat commands
    take one.
  - **Rooms**: a room or floor you say must be one the command takes.
    "Pause the music upstairs" isn't pausing it here, and "what time is it in
    the kitchen" isn't the time: neither runs, and the satellite says
    "…but I can't pick a floor for it". A room inside free text is part of
    it ("play the kitchen sink"); at its end, it is where ("play jazz in the
    kitchen"). So is a device at its end ("play jazz on the Mark 2"): a
    command that takes that device runs, with "jazz" as the text, and one
    that can't take it doesn't swallow it.
  - **Ways to say it**: what is heard is never optional. Instead a command
    lists the combinations that may be said, each whole: "turn off the
    lights" with nothing named (the satellite's area), with an area, or with
    a floor. Without any, everything it listens for is said together. A
    command never runs with something you said left out: "turn off the
    lights in the bedroom" is the area combination, never the satellite's
    own area.
- **Implied target**: entities, areas or floors the command acts on without
  them being said. They reach Home Assistant as slots, as if they had been
  heard, so the phrase can be anything ("cooking mode") while the target
  stays fixed. A device, room or floor that is said takes the place of the
  implied one of its kind; a room said with an implied device, or one
  implied with a device said, only picks out which device.
- **What happens**: only respond, run a script, activate a scene, run an
  action (Home Assistant script syntax), run an **intent** (below), a timer
  operation or a music operation (below), plus an
  optional **response** template that is spoken. Nothing to say plays a short
  sound. Or **nothing** at all: the library's **Never mind** ("never mind",
  "cancel", "forget it") only ends the conversation, silently.
- **How much it says** when it's done, one of five levels:
  0. **Silent**: nothing, not even a sound -- what a command that does
     nothing says by default. A question still answers.
  1. **Beep**: the acknowledgement sound. A question still answers.
  2. **Brief**: a fixed sentence, "Turned on the lights." -- the same every
     time, so text-to-speech caches it.
  3. **Specific**: what was heard, "Turned on the lights in the Kitchen.",
     "The 5 minute timer started."
  4. **Verbose**: everything it did, "Turned on Ceiling, Lamp and Strip in
     the Kitchen.", "The 5 minute timer started, finishing at 10:05 PM."

  A beep is for what you can see happen. A command set to beep that acted
  somewhere else -- another area, a floor, a device in no area -- says what
  the **Responses** panel on the Settings page says instead: the specific
  sentence unless you change it (Home Assistant's own says the brief one).
  The satellite's center button, pressed during a call, cancels it and what
  it is saying (the firmware's `voice_call.cancel`). Piper says a reply a
  sentence at a time, starting as soon as the first is ready, without the
  markdown a conversation agent writes (`**bold**`, bullets).

  While a reply is slow to come -- nothing said back a second after you
  stopped speaking: Home Assistant's speech, the conversation agent, a slow
  machine -- the satellite plays a short **thinking sound**, stopped as the
  reply begins; a quicker reply plays none. Turn it off in the same panel,
  or set how long a reply may take before it plays (0 to 10 seconds).

  The done response can be written once, for every level, or once for each;
  a level without one says the closest written, less before more. Timers
  and music have built-in sentences for each level. Library commands come
  with theirs: lights, devices, scenes and music controls beep; timers,
  music to play, lists, the thermostat, covers and locks say what they did.
  Only "done" has levels: what went wrong is always said.
- **Responses by outcome**: what is said can depend on how it went. Every
  command has **done** (the response above) and **failed** (Home Assistant
  refused; "Sorry, that didn't work." unless you say otherwise). Timer
  commands have their own outcomes (below), and a script can pick one by
  returning `outcome`: `stop` with `response_variable` holding
  `{outcome: nobody_home}`. An outcome without a response of its own says the
  built-in sentence (a timer's, or the script's `speech`), never the done
  one.
- **Runs on**: every satellite, some (ticked), or none (turned off).
- **Duplicate** (at the top of its page) makes a copy to change: to have a
  command do something else on one satellite, put the copy on it. The copy
  says the same example sentences and keywords; on that satellite the copy
  goes first, everywhere else the first.

A command that doesn't run on a satellite is not run there: it says "It
looks like you're trying to …, but that isn't set up here."

### Intents

A command can run one of Home Assistant's intents -- HassTurnOn, HassLightSet,
HassClimateSetTemperature … -- instead of an action, and the library's
commands for lights, the thermostat, covers and locks do. An intent runs as
Assist would, on what was heard: each area or floor named, else the
satellite's area -- there, **only devices exposed to Assist** (Settings →
Voice assistants → Expose) -- or each device named (by its entity id), which
is yours on the Names page, exposed or not. An unavailable device is said,
not counted as done. Its other slots are the command's
(`domain: light`, `brightness: "{{ slots.percentage }}"`: a template sees
the same variables as a response). `anywhere: true` means the whole home
when nothing of the kind is in the satellite's area ("open the garage door"
from the kitchen); `home: true`, the whole home whenever nothing is named
("lock up").

```yaml
intent:
  name: HassLightSet
  slots: {brightness: "{{ slots.percentage }}"}
```

Besides done and failed, an intent's outcomes say why it acted on nothing:

| outcome | when | says |
|---|---|---|
| `no_devices` | nothing of the kind there | "There's nothing like that in the Kitchen." |
| `not_exposed` | some there, none exposed to Assist | "Nothing like that in the Kitchen is exposed to Assist." |
| `unavailable` | exposed, all unavailable | "Everything like that in the Kitchen is unavailable right now." |
| `no_area` | nothing named, and the satellite has no area | "I don't know which area I'm in. Give this satellite an area." |

When it acted on some, it is done on those: its response sees `result.count`,
`result.names` and `result.ids` (what it acted on), `result.unavailable`
(what it skipped) and `result.speech` (Home Assistant's own sentence, if
any). An **action** with `target: "{{ target }}"` reaches every device in the
area, exposed or not.

The library's questions answer the same way: "are any lights on in the
kitchen", "are the blinds open", "is everything locked" count only what is
exposed to Assist (their templates filter by `exposed`), while "is the desk
lamp on" asks about the device named.

### What Home Assistant gets

The action, the script and the response template all see:

| variable | |
|---|---|
| `slots` | what was heard plus the fixed target: `entity`, `area`, `floor` (lists of ids), the number (by preset name), value lists, free text |
| `satellite` | `id`, `name`, `area_id`, `area_name`, `floor_id`, `floor_name`, `device_id`, `timer_list`, `media_player` |
| `speaker` | who is speaking (People): `name` (or none), `score` (by their voice), `by` (`voice`, `wake_word`, `only_person`), and their own `todo_list`, `calendar` and `playlist` |
| `wake_word` | `id`, `name` and `phrase` of the wake word that started it (none each when typed) |
| `target` | `entity_id` from the slots (a room said with a device only picks it out), else `area_id`/`floor_id`, else the satellite's area: `target: "{{ target }}"` |
| `command` | `id`, `description`, `text` (what was heard) |
| `place` | where it acted: `said` ("in the Kitchen", "upstairs"; empty when unknown), `here` (only the satellite's area), `areas` (ids; a floor's every area) |
| `exposed` | the entity ids exposed to Assist (not in the `voice_call_command` event) |
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

An **alarm** rings at a clock time: "set an alarm for seven thirty", "wake me
up at 6 am", "an alarm for half past six" (without am or pm, the next time it
comes round). In English it is an event on the app's calendar (see **Alarms
and reminders** below); in the other languages it is a timer named "alarm",
cancelled like any timer.

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

### Alarms and reminders

In English, alarms and reminders with a time are events on a **Local
Calendar** in Home Assistant named **Voice Call**, which the app creates when
it connects (Home Assistant's Local Calendar integration, built in). They
outlive restarts, can be for another day or repeat, and show -- and can be
changed -- in Home Assistant's calendar. Add **Set an alarm**, **Cancel an
alarm** and **Say the alarms** from the **Timers** group, and **Remind me**,
**Cancel a reminder** and **Say the reminders**:

- "set an alarm for 7 pm tomorrow", "wake me up at half past six", "an alarm
  for every weekday at 6:45", "set an alarm tomorrow at 8", "an alarm in 20
  minutes";
- "cancel my alarm", "delete the 7 o'clock alarm" (without am or pm, either),
  "cancel my alarm for tomorrow";
- "cancel my reminder to call mom", "delete the reminder for tomorrow",
  "cancel the reminder at 5 pm": by what it is about (loosely: "call my
  mom" is "Call your mom"), its time or its day -- what is said after
  "reminder"; with one reminder, the only one;
- "remind me at 5 pm to call my mom", "remind me in 10 minutes to check the
  oven", "remind me to take the bins out every tuesday at 7 pm";
- "what alarms do I have", "what are my reminders".

Without am or pm, a time today is the next one to come round; on another day
an alarm is in the morning, and a reminder in the morning from 7 to 11, else
the afternoon. A reminder without a time ("remind me to buy milk") goes on the
to-do list among its entities (`todo.reminders` in the library's command),
calendar or not.

When one starts, the satellite that set it rings: an alarm says the time and
rings like a timer, until "stop"; a reminder sounds the alarm once and is said
("Reminder: call your mom."). An event added in Home Assistant is a reminder
said on every satellite. The description of each event the app adds marks it
as an alarm or a reminder and names the satellite
(`[voice_call:alarm:<satellite>]`); keep that when editing one. One missed by
up to five minutes (the app was down) still goes off. The calendar is
followed live where Home Assistant can send its changes, else read every five
minutes. If the calendar can't be made, the Home page says why, alarms
are timers again, and a reminder with a time says so.

| outcome | when |
|---|---|
| `done` | set, cancelled, or (for the status commands) said |
| `listed` | a reminder without a time went on the to-do list |
| `missing_time` | an alarm without a time, or a reminder with a day but no time (or no to-do list) |
| `missing_reminder` | a reminder with a time but nothing to remind of |
| `no_alarms` / `no_reminders` | none set on this satellite |
| `not_found` | no alarm or reminder like the one asked for |
| `ambiguous` | several fit; it says them |

Automations can follow timers with the `voice_call.timer_created`,
`timer_finished`, `timer_paused`, `timer_unpaused`, `timer_time_changed` and
`timer_cancelled` triggers, and act on them with the `voice_timer_list.*`
actions (`create_timer`, `cancel_timer`, `get_timers` ...).

## Music

Music plays on **Music Assistant**'s players. A satellite running a Sendspin
client (the Voice Call firmware does) shows up in Music Assistant by itself,
and the Voice Call integration finds that player from the satellite's MAC
address; the satellite's page shows it under **Music** and can pick another. Add the commands from the
library's **Music** group:

- "play Miles Davis", "play the album Kind of Blue", "play my chill
  playlist", "play So What by Miles Davis": Music Assistant is searched for
  what was heard and the closest match plays ("Playing Kind of Blue by Miles
  Davis."). "Add … to the queue", "play more like this".
- "pause", "resume", "stop the music", "next", "go back", "louder", "set the
  volume to 30 percent", "mute", "shuffle", "repeat", "what's playing".
- "move the music to the kitchen", "move the music upstairs", "send the music
  to the office speaker": what plays here -- the satellite's own player, else
  one in its area, else where it sent the music, else the one player playing
  anywhere -- moves there, queue
  and all, and stops where it was ("Moved the music to the Kitchen."). It
  goes to one player: on a floor with several, one that isn't already
  playing.

Each comes in versions: **on this satellite**, **on a different device**
("pause the music on the Mark 2", "play jazz on the Mark 2") and **in an area
or floor** ("play jazz in the kitchen", "turn it up upstairs"). Moving the
music has only the last two. Name the players, rooms and floors first (Names
page); a player must be a Music Assistant player.

The player is the satellite's own, unless a command names another (Target),
a room or a floor. Pausing, skipping, the volume and "what's playing" act on
whichever Music Assistant player is playing in the satellite's area when its
own is not, and "resume" resumes what was paused by voice. When nothing plays
there, they follow the music the satellite sent elsewhere -- "play jazz on the
Mark 2", "move the music to the kitchen" -- while it plays there (or, to
resume it or ask what it is, is paused there), and say where: "Volume at 40%
on Mark II Sendspin." "Play ..." always plays here. A player grouped with
others pauses and skips with its group.

Like timers, every music command has outcomes, each with a sentence you can
replace: `done` (controls play a short sound), `no_player`,
`nothing_playing`, `not_found`, `missing_query`, `missing_volume`, `already`
and `failed`. Templates also see `player` (the one it acted on: `entity_id`,
`name`, `state`, `title`, `artist`, `volume` ...), `players`, when it
searched `media` (`name`, `artist`, `media_type`, `uri`), and when it moved
the music `source` (the player it came from).

On the satellite, music is turned down while it listens to you or a timer
rings, never paused, and the center button pauses and resumes it.

## Lights

The library's **Basics** group turns lights on and off and sets their
brightness and color, each in the satellite's area ("turn on the lights"),
in an area or floor named ("dim the lights upstairs"), or a light by name
("set the bed light to red"):

- **Brighter** and **Dimmer**: a fifth at a time ("make the lights
  brighter", "turn the lights down", "dim the bed light", "brighter"),
  never down to off. In an area, only the lights that are on change, of
  those exposed to Assist; a light by name that is off comes on to be
  brightened. With none on, the satellite says so.
- **Set the color temperature**: the white a light shines, by name -- warm
  white, soft white, neutral white, cool white, daylight ("set the lights to
  warm white"). They are the `color_temperatures` list, in kelvin (2700 to
  6500): change it on the Names page to name others.

These are in the English library for now.

## Lists

The library's **Basics** group keeps a shopping list and a to-do list: the
home's (Settings page), or the to-do list a command is given as its implied
target:

- **Add to the shopping list**: "add milk to the shopping list", "put eggs
  on the list". Several at once are items of their own: "add oranges and
  bananas" is two, "eggs, milk and bread" three (split at "and" and commas,
  in every language; "macaroni and cheese" is two as well).
- **Check off the shopping list**: marks it done, and keeps it on the list
  as such -- "cross the milk off the shopping list", "check off eggs on the
  list", "mark bread as done on the list".
- **Take off the shopping list**: removes it -- "remove milk from the
  shopping list", "take eggs off the list".
- **What's on the shopping list**: says what is still to get -- "what's on
  the shopping list", "read me the shopping list", "what do we need from
  the store" -- the first five and how many more, with more than eight.
- **Add to the to-do list**, and **Add to my to-do list** (People).

Checking off and removing take an item that is on the list: what was heard
is matched to it as spoken, so "test three" is the item "Test 3", "the
milk" is "Milk", and a near spelling ("paper towel") is the closest. One
that isn't on the list says so ("Bread isn't on the list."). Checking off,
removing and reading are in the English library for now.

## Weather

The library's **Weather** group answers from the home's weather entity --
set it on the **Settings** page, under Home -- with its daily forecast. A
command given its own implied target uses that one instead.

- **What's the weather**: now -- "It's partly cloudy and 15 degrees."
- **The weather on a day**: "what's the weather tomorrow", "what will be the
  weather tomorrow", "the forecast for Saturday", "the day after tomorrow",
  "this weekend" (both days) -- "Saturday looks cloudy, with a high of 18 and
  a low of 10, and a 20% chance of rain." With no day, today's: "what will the
  weather be like", "what's the forecast".
- **The weather this week**: "what's the forecast for the week", "for the
  next few days" -- "Mostly sunny, with highs from 15 to 21 degrees. Rain is
  likely on Thursday."
- **Will it rain**: "is it going to rain", "will it rain tomorrow", "will it
  snow on Saturday", "do I need an umbrella", "will it rain this weekend" --
  "Yes, there's an 80% chance of rain tomorrow." or "Probably not. Saturday
  looks cloudy, with a 20% chance of rain." Rain is likely at 50% or more,
  or when the forecast says rainy.

A day is today, tomorrow, the day after tomorrow, a day of the week (the
next one) or this weekend: the `forecast_days` list. The forecast is in
English for now.

## Climate

The library's **Climate** group (English) asks and sets the home's
thermostat -- set it on the **Settings** page, under Home -- or one a command
is given as its implied target:

- **Ask the thermostat**: "what's the thermostat set to", "what's the
  temperature inside", "how warm is it in the house" -- "It's 68 degrees
  inside. The thermostat is set to 70." (or the two it keeps between, in heat
  and cool mode).
- **Set the thermostat**: "set the thermostat to 70", "turn the thermostat
  up to 72". In heat and cool mode the thermostat keeps two temperatures, and
  Home Assistant refuses one alone.
- **Ask the thermostat's mode**: "what mode is the thermostat in", "what is
  the thermostat doing" -- "The thermostat is set to heat. It's heating now."
- **Set the thermostat's mode**: "set the thermostat to heat", "switch the
  thermostat to air conditioning", "turn off the thermostat". The modes are
  the `hvac_modes` list's: off, heat, cool, heat and cool, auto, dry, fan only.
- **Ask the thermostat's fan**: "what's the fan mode" -- "The thermostat's
  fan is on auto."
- **Set the thermostat's fan**: "set the fan mode to auto", "turn the
  thermostat fan on". A fan alone is a fan's ("turn on the fan"). The modes
  are the `fan_modes` list's -- auto, on, off, low, medium, high, circulate,
  Home Assistant's words for them; add a thermostat's others there (Names
  page).

A thermostat by name or in an area is **Set the temperature** (Basics),
through Home Assistant's intent.

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
- Unlocking works for anyone the satellite hears: put the command on the
  satellites that should have it, not the Home.

Opening, closing and "is it open" come in versions: by type in the
satellite's area ("open the blinds"), a cover by name ("open the skylight"),
and by type in an area or floor ("close the shades upstairs"). Locks are
only by name, and "lock up" and "is everything locked" are every lock.

## When nothing matches

In order, each only when the one before found nothing:

1. **Keywords and example sentences**: in the audio, then -- when nothing
   matched there -- in the transcript the speech model wrote (the built-in
   TDT, or your Home Assistant STT), so "unlock the front door" heard plainly
   by the speech model runs even when the keyword evidence in the audio was
   weak. A doubtful match in the audio -- one that left a word it heard as
   something else unexplained ("resume the chicken ..." as resuming the
   music), or was heard only poorly -- runs only when the transcript says the
   same command, surely; when it says another, that one runs, and when it
   says none, or is as unsure ("what day is Christmas" as What's the date),
   nothing does, and the steps below go on. A name counts for more than none: when the audio matched a
   command naming nothing, with a word left over, and the transcript names
   a device, area or floor for another version of it, that version runs
   ("set bed light to green"). A session's Recognition step says so. Typed text (a
   satellite's Test it) is read as said, word for word.
2. **Skills** (Questions, on the Home page or a satellite's): spelling,
   dates, the clock, units and arithmetic answer exactly ("what's twelve
   times eight").
3. **Meaning**, for the commands chosen for it (below).
4. **A small model**, for the commands chosen for it (below).
5. **The conversation agent** (Settings page), when there is one.
6. **The Unknown command** (below).

Choose a command for meaning on its page with **Also sentences that mean the
same** (under its example sentences), and for the model with **Also when the
model hears its free text or values** (under its free text), or many at
once with **Choose commands** under "When nothing matches" on the
**Settings** page, where each step can also be turned off for every command
(a command's page then says "off in Settings", and a call's trace says why
the step was skipped). Commands from the library are not chosen until you
choose them.

- **Meaning** picks the chosen command a sentence is very close to -- its
  example sentences and description: "kill the lights in the office" for
  "turn off the lights". Example sentences match far better: on the
  benchmark's sentences, the right command scored 0.86 against one of its
  examples, and 0.80 against its description, where meaning runs a command
  at 0.80. What was said in it -- the room, a number, a name --
  the model below hears, and that picks the way of saying it (the office, or
  nothing named: the satellite's area). A command that hears nothing runs at
  once. With the model turned off, meaning runs only commands that hear
  nothing.
- **A small model** picks one of its chosen commands, or "not a command",
  and fills in what it needs: "throw on my workout playlist", "take milk off
  the list", "give me ten minutes for the pasta", "have you got the time". It is
  GLiNER2.5 small, which comes with the app, and adds about a
  third of a second on a Raspberry Pi 5, however many commands are chosen.
  When it hears nothing in the sentence ("lights out", "how late is it"), its
  pick runs only if meaning puts the same command first, ahead of the
  everyday requests that are no command ("thank you"). When its pick takes
  none of what it heard, the likeliest chosen command that does takes its
  place: with both chosen, "I need cookies to be on the shopping list" is
  closest to What's on the shopping list, which takes no item, and is Add
  to the shopping list.
- A command's **versions** (on this satellite, a device by name, an area or
  floor by name) are one choice to both: what was said picks the version --
  "lights out in the bedroom" is Turn off the lights in an area or floor,
  "lights out" the one for this satellite. So are any commands you describe
  the same way.
  It compares the chosen commands with others close in meaning, chosen or
  not, and picks the likeliest chosen one. It runs nothing when it is not
  sure of that one (**Threshold**, 0.3: lower runs more, and more by
  mistake), or when the sentence is not a command at all. Each command is
  scored on its own, so a sentence meant for another leaves the chosen ones
  unsure: to it, "give me ten minutes" is How long is left on a timer (0.82)
  far more than Start a timer (0.21).

  It only runs commands the satellite may run, and never
  with a name, value or number you didn't say. Commands are told apart by
  their descriptions, so describe what each does in full: "play an artist,
  album, song, playlist or genre", not "play music". A call's trace says what
  was picked, how sure it was, and why it was not run.

Both are on, and do nothing until a command is chosen for them. A call's trace
shows what each step tried.

### The Unknown command

When nothing understood a sentence -- no command, no skill, nothing above --
the satellite says the closest command ("It looks like you're trying to turn
on the lights, but I didn't hear which room."), or "Sorry, I didn't
understand that." The **Unknown command**, always at the top of the Commands
page, says something else instead: a Home Assistant template, such as

```
Sorry, I couldn't understand the sentence {{ transcript }}.
```

It sees `transcript` (what the speech model heard), `heard` (what the keyword
matcher heard), `hint` (the near miss, empty when nothing came close),
`closest` (that command's `id` and `description`), `response` (what it says
without the template), and `satellite`, `speaker` and `place` as a command's
response does. A template that renders to nothing says the default.
Something understood but impossible as said ("the fan in the office" where
there is none) still says why.

## Skills

Skills (under **Questions** on the Home page, or a satellite's) answer
questions no command takes, exactly and at once, without an LLM. Each is
computed, never guessed -- a question a skill can't read whole is left to
the next step:

- **Spelling**: "how do you spell necessary".
- **Dates**: "what day is Christmas", "is 2028 a leap year", "how many days
  until Halloween".
- **Clock arithmetic**: "what time is 90 minutes after 2 pm".
- **Unit conversions**: "how many cups in a quart", "100 Fahrenheit in
  Celsius", "5 miles in kilometers".
- **Math**: "what's twelve times eight", "15 percent of 80".

They read questions however they are written -- "What's 50°F in Celsius?",
"12 × 8", "¼ cup", "$80" -- as well as spoken. A question about now or here
-- the weather, news, scores, prices, what's open -- is no skill's: it goes
to the conversation agent, or else the Unknown command.

Every skill runs on every satellite to begin with. Its page sets where it
**runs on**: every satellite, some, or none (turned off).

A skill's page sets its **level** -- how much it says, as a command's does:

- **Brief**: the answer -- "96."
- **Specific** (the default): the answer as a sentence -- "It's 96."
- **Verbose**: the question said back with it -- "Twelve times eight is 96.",
  "There are 4 cups in a quart."

and, for any level, a **template** of your own: a Home Assistant template
seeing `answer` ("96"), `question`, `response` (the built-in sentence at
that level), `satellite` and `speaker`. It's the same wherever the skill is
added.

Its page has the skill's **settings** too, the same wherever it's added:

- **Unit conversions**: its units, by what they measure. Each has a name, a
  plural, other names ("mm"), and a size in its measure's base unit: a cup
  is 236.6 milliliters. A temperature scale is set by where water freezes
  and boils on it. Change a unit, take one out, or add your own ("nautical
  mile", 1852 meters). **Measures**, under Volume: US customary (the
  default) or imperial -- the UK fluid ounce, pint, quart and gallon, and the
  metric cup and spoons (250, 15 and 5 mL) UK recipes use. "An imperial
  pint" or "a US cup" in a question is that one, whichever is set. **Precision**:
  significant digits in an answer that isn't whole, 4 unless set ("8.047
  kilometers"); whole numbers are said in full.
- **Spelling**: words spelled your way. Asked to spell "color", it can say
  C, O, L, O, U, R; a name the speech-to-text writes differently; or a
  phrase, such as "my last name". Spaces, hyphens and apostrophes are said.
- **Dates**: its holidays, each with a name, other names, and when it is: a
  date ("December 25"), a weekday in a month ("fourth Thursday of November",
  "last Monday of May"), Easter or days from it ("2 days before Easter"), or
  a date with its year for one that happens once ("June 20 2027"). The
  built-in ones are US holidays; change them, take them out, or add your own
  ("my birthday").
- **Clock arithmetic**: a 12-hour ("4:45 PM") or 24-hour ("16:45") clock.
- **Math**: its precision, as Unit conversions'.

Built-in units and holidays you haven't changed follow updates to the app.
**Restore built-in** puts back all of them.

## Conversation agent

What no command, skill or step under "When nothing matches" took can go to a
Home Assistant conversation agent (Settings page, "Conversation agent"): Home
Assistant's own, or an LLM's (OpenAI, Google, Ollama ...). Its answer is said,
and what it doesn't understand either is the Unknown command's.

Each satellite keeps a conversation with it: for 5 minutes after its last
answer there (a setting, as the Voice PE's `conversation_timeout`), the next
sentence continues it, so "who wrote The Odyssey?" ... "when did he live?"
follows on. Skills still answer first.

A dry run (Test it without "Really run it", or a dry-run debug session)
never asks the agent, since it can act in your home.

## Names

What things are called when spoken, each pointing at one Home Assistant id.
Nothing is taken from Home Assistant automatically: search for an entity,
area or floor and its name and aliases are offered as a starting point. Add
the spellings the speech model actually uses as aliases ("Echo Bee" for
ecobee). Devices are limited to lights, switches, fans, covers, locks,
climate, media players, scenes, scripts, input booleans, valves and vacuums;
an implied target may be any entity.

A name is in the language it was given in. In another language than your
commands', a thing's name and aliases go under the language's code,
beside its others; only the app's language's are heard and shown:

```yaml
names:
  areas:
    kitchen: {name: Kitchen, de: {name: Küche, aliases: [Kochecke]}}
```

## Testing, sessions and debugging

- **Test it**, on a satellite's page, recognizes a sentence as if said there
  and heard word for word -- no speech recognition, so commands, skills and questions read
  exactly what you typed -- without running anything unless you ask. Tick
  "Say it first" to have the app's voice speak it and recognize the audio
  instead, to test speech recognition too.
- **Sessions** lists recent sessions -- everything from the wake word to the
  answer: what was heard, what it ran and what it said. A session's page
  puts that first, then its **steps** in order, each with its time since the
  session started and whether it succeeded: the wake word (with the
  probability the satellite's model came in with, against its cutoff; new
  voice_call firmware), its verification, the end of speech, the speaker,
  where the command starts, recognition, the action or answer, and what was
  said -- and how long it took: verification's decode, speaker
  identification, the acoustic model, recognition with the part TDT or the
  speech-to-text entity took, the action, a template, the conversation agent,
  and speaking (Piper's synthesis, or a text-to-speech entity's time until
  the satellite started saying it, and how long it spoke). Open a step for
  everything recorded about it -- every command that
  matched or was rejected and why, what was sent to Home Assistant -- and
  **Audio** for the waveforms.
- **Listen live** (a satellite's page, under Test it) plays what the satellite's
  microphone hears, as it hears it, and draws it: the level, the last few
  seconds, and what its voice activity and wake word models make of it --
  each one's probability against its cutoff, with a mark where a wake word
  was detected. The satellite streams only while you listen, its ring blinks
  red the whole time, and it doesn't answer a wake word meanwhile. It needs
  the voice_call firmware from this version.
- Tick **Keep every session** on the Sessions page to keep every session's audio,
  or debug one satellite (its page, under Test it). That keeps
  the satellite's audio and can be a **dry run**: nothing is executed, and
  you hear what would have been said. Kept sessions show their
  waveform with the wake word, the cut and the recognized words marked, and
  can be **replayed** through today's commands.
- **Who spoke**: a kept session's voice can be added to a person's -- one
  already known, or someone new -- from its Speaker step, so they are
  recognized better from then on (and their name reaches commands as
  `speaker`).

## What is kept

Everything the UI edits is in `/data`, as JSON the UI alone reads and writes:
`commands.json` (commands, names and value lists), `satellites.json`,
`settings.json`, `wake_words.json` and `people.json` (each person's own
things). They are not for editing by hand.
Files from before this version (`commands.yaml` and the others) are copied
to JSON once, and left as they were. The commands, written as YAML (the file
is JSON), look like this; each is edited on its page, and names and lists on
the Names page. One that follows the library is saved as `from` and what
you set (`from: basic/turn_on_here`, `verbosity: brief`).

```yaml
language: en  # what the commands are said in, unless one says its own
names:
  entities:
    climate.ecobee: {name: EcoBee, aliases: [Echo Bee, Thermostat]}
  areas:
    kitchen: {name: Kitchen}
lists:
  colors: {red: [red, crimson], blue: blue}  # yours: in place of the built-in colors
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
    satellites: [d83bdaa3d5f4]  # only on these; without it, on every satellite
    require: [[turn on, turn ... on], [light, lights]]
    names: {area: {count: 1}, floor: {count: 1}}
    combinations: [[], [area], [floor]]  # nothing named: the satellite's area
    action: {action: light.turn_on, target: "{{ target }}"}
    verbosity: beep  # silent, beep, brief, specific or verbose (or 0 to 4)
    response:
      done:
        brief: Turned on the lights.
        specific: Turned on the lights {{ place.said }}.
  pause_timer:
    description: pause a timer
    require: [[pause], [timer]]
    lists: {timer_name: {list: running_timers}}
    combinations: [[], [timer_name]]
    timer: pause
    response:
      done: Paused.
      no_timers: There's nothing to pause.
      already: "{{ result.speech }} Say resume to start it again."
  gute_nacht:
    language: de  # made in German: heard and shown only while the app is
    description: gute Nacht sagen
    examples: [gute nacht]
    script: script.good_night
```

## Options

- `language`: what the app hears and says -- its speech models, Piper voice,
  command library and skills (default: `en`, English; or `de`, German, `nl`,
  Dutch, `fr`, French, `es`, Spanish, `it`, Italian, `pl`, Polish, `ru`,
  Russian, or `uk`, Ukrainian). One at a time: a command or a name is in the
  language it was made in (the commands' `language:`, or its own: see
  Names), and only the app's are heard and shown -- the others stay in the
  file, hidden, until the app is in theirs again. The web UI is in English. In
  another language than English, skills are asked and answered in it, with its
  units (a German Pfund, a Dutch pond and a French livre are 500 g; metric
  spoons and cup), its country's holidays (Russian and Ukrainian Easter is the
  Orthodox one) and a 24-hour clock; there is no GLiNER step in the fallback,
  and no second opinion on the wake word (Kaldi alone decides). Its models
  come with the app: NVIDIA's FastConformer hybrid for the language
  (`stt_<language>_fastconformer_hybrid_large_pc`, CC-BY-4.0, about 350 MB)
  and a Piper voice (Thorsten, Ronnie, Siwis, Davefx, Serena, Gosia, Denis,
  and for Ukrainian Tetiana, a slower "high" voice: the medium one drops
  digits); without them the app says which are missing.
- `wake_cutoff`: the satellite detector's probability cutoff for the
  checked wake words (default: each its own -- 0.2 for Okay Nabu, 0.5 for Hey
  Jarvis, 0.7 for Hey Mycroft). Every detection of these is verified again
  here, so it can be generous. A cutoff set on the Wake words page wins.
- `speaker_threshold`: how closely a voice must match to be identified.
- `silence_seconds`: silence that ends a command. **Silence to end**, on the
  Settings page, wins.
- `debug_logging`: verbose logs.
