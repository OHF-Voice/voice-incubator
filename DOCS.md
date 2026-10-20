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

Without the integration the app still works, but satellites have no timers and
their areas are kept by the app.

## The Home and its satellites

The **Home** page starts with a tile for each satellite -- its name, area,
wake word, and whether it takes the Home's commands -- then the Home's
**commands and skills**, grouped by what they're for (your own first, under
**Custom**), and the **Unknown command** at the bottom.

Satellites on the network are found and connected to automatically. Click a
satellite's tile for its page:

- **Settings**: its name, icon and area (with the integration, that is the
  area of its device in Home Assistant, and can be changed in either place),
  its music player, and how it listens and speaks. The area is what "the
  lights" means there. The floor comes from the area.
- **Commands and skills**: its own, and whether it takes the Home's too
  (**Include the Home's**, ticked unless you untick it).
- **Test it**: type a sentence as if said there, listen to it live, and
  debug it.

A command or skill is **the Home's** -- run on every satellite that takes the
Home's -- or **one satellite's own**. A satellite that doesn't take the
Home's runs only its own. Where a satellite's own command and one of the
Home's take the same sentence, its own goes first: to have a command do
something else on one satellite, duplicate it there and change the copy.

### Wake words

Each satellite listens for one wake word: the home's **preferred** one, or
one of its own. Both are picked on the **Wake words** page, which is also
where each wake word is tuned.

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
in `wake_words.yaml`.

### Listening and speaking

The app listens and speaks with models of its own, or with Home Assistant's
**speech-to-text** and **text-to-speech** entities: chosen for every satellite
on the Settings page, and under **Input** and **Output** on each satellite's
page.

- **Speech-to-text.** Commands are always matched on the satellite's audio
  first. The STT entity writes what the built-in TDT model would otherwise:
  example sentences said word for word, free text ("play …", timer names),
  and hints. It is only asked when one of those is needed, so "turn on the
  lights" never waits for it. Should it fail, the built-in model stands in.
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

## Commands

The Home page lists the Home's commands as the library has them -- Basics,
Covers and locks, Music, Timers -- with the ones you made from scratch first,
under **Custom**, and the skills under **Questions**; a satellite's page lists
its own the same way. A command from the library stays in its section when
you change it, rename it or duplicate it.

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
sentence goes to the one that takes what was said in it. Adding one from the
library copies it into your commands, with any shared value list it uses
that you don't have yet; one you already have is marked **added**, by its
id, so a command you renamed counts as your own and the library's can be
added again. A command has:

- **Icon**: to tell commands apart at a glance; by default, one for what it
  does (a timer, music, a script ...).

- **What it does**: a verb phrase, "turn on the lights". It is also how a near
  miss is described: "It looks like you're trying to turn on the lights, but I
  didn't hear which room."
- **Target**: entities, areas or floors the command acts on. They reach Home
  Assistant as slots, as if they had been heard, so the phrase can be anything
  ("cooking mode") while the target stays fixed.
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
  - **Free text**, kept bounded so it can be heard reliably: between two
    keywords ("add … to the shopping list"), from a keyword to the end
    ("remind me to …"), or right before or after a keyword ("the … timer").
    It is spelled from the TDT transcript.
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
- **What happens**: only respond, run a script, activate a scene, run an
  action (Home Assistant script syntax), a timer operation or a music
  operation (below), plus an
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
- **Where**: the Home's, or one satellite's own.
- **Duplicate** (at the top of its page) makes a copy to change, on the Home
  or a satellite: to have a command do something else on one satellite, put
  the copy there. The copy says the same example sentences and keywords; on
  that satellite its own goes first, everywhere else the Home's.

A command another satellite has, or the Home's on a satellite that doesn't
take them, is not run there: it says "It looks like you're trying to …, but
that isn't set up here."

### What Home Assistant gets

The action, the script and the response template all see:

| variable | |
|---|---|
| `slots` | what was heard plus the fixed target: `entity`, `area`, `floor` (lists of ids), the number (by preset name), value lists, free text |
| `satellite` | `id`, `name`, `area_id`, `area_name`, `floor_id`, `floor_name`, `device_id`, `timer_list`, `media_player` |
| `speaker` | `name` of the enrolled voice (or none) and `score` |
| `target` | `entity_id` from the slots (a room said with a device only picks it out), else `area_id`/`floor_id`, else the satellite's area: `target: "{{ target }}"` |
| `command` | `id`, `description`, `text` (what was heard) |
| `place` | where it acted: `said` ("in the Kitchen", "upstairs"; empty when unknown), `here` (only the satellite's area), `areas` (ids; a floor's every area) |
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
   weak. Typed text (a satellite's Test it) is read as said, word for word.
2. **Skills** (Questions, on the Home page or a satellite's): spelling,
   dates, the clock, units and arithmetic answer exactly ("what's twelve
   times eight").
3. **Meaning**, for the commands chosen for it (below).
4. **A small model**, for the commands chosen for it (below).
5. **The conversation agent** (Settings page), when there is one.
6. **The Unknown command** (below).

Choose commands for meaning and the model on the command's page, or many at
once with **Choose commands** under "When nothing matches" on the
**Settings** page, where each step can also be turned off for every command
(a command's page then says "off in Settings", and a call's trace says why
the step was skipped). Commands from the library are not chosen until you
choose them.

- **Meaning** picks the chosen command a sentence is very close to -- its
  example sentences and description: "kill the lights in the office" for
  "turn off the lights". What was said in it -- the room, a number, a name --
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
  everyday requests that are no command ("thank you").
- A command's **versions** (on this satellite, a device by name, an area or
  floor by name) are one choice to both: what was said picks the version --
  "lights out in the bedroom" is Turn off the lights in an area or floor,
  "lights out" the one for this satellite. So are any commands you describe
  the same way.
  It compares the chosen commands with others close in meaning, chosen or
  not, and runs nothing when it is not sure (**Threshold**, 0.3:
  lower runs more, and more by mistake), when the closest is one you did not
  choose, or when the sentence is not a command at all.

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

Every skill is the Home's to begin with. Take one off the Home, or a
satellite, on its page (opened from there); add one with **Add**.

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
`commands.yaml` may name anything.

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
- **Who spoke**: a kept session's voice can be added to a speaker's -- one
  already known, or a new one -- from its Speaker step, so they are
  recognized better from then on (and their name reaches commands as
  `speaker`).

## YAML

Everything the UI edits is in `/data/commands.yaml` (Settings page, "Advanced"),
and you may edit it directly; comments survive saves from the UI.

```yaml
names:
  entities:
    climate.ecobee: {name: EcoBee, aliases: [Echo Bee, Thermostat]}
  areas:
    kitchen: {name: Kitchen}
lists:
  colors: {red: [red, crimson], blue: blue}
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
    satellite: d83bdaa3d5f4  # only this satellite's; without it, the Home's
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
```

## Options

- `language`: what the app hears and says -- its speech models, Piper voice,
  command library and skills (default: `en`, English; or `de`, German, `nl`,
  Dutch, `fr`, French, `es`, Spanish, `it`, Italian, `pl`, Polish, `ru`,
  Russian, or `uk`, Ukrainian). One at a time: commands are in one language
  (`commands.yaml` `language:`), and the Home page says when they are not the
  app's. The web UI is in English. In another language than English, skills
  are asked and answered in it, with its units (a German Pfund, a Dutch pond
  and a French livre are 500 g; metric spoons and cup), its country's holidays
  (Russian and Ukrainian Easter is the Orthodox one) and a 24-hour clock;
  there is no GLiNER step in the fallback, and no second opinion on the wake
  word (Kaldi alone decides). Its models come with the app: NVIDIA's
  FastConformer hybrid for the language
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
