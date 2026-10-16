# Changelog

## 0.2.0

- Timers, on the satellite that set them: start, cancel, pause, resume, add or
  take off time, how long is left. A finished timer rings until "stop".
- The Voice Call integration for Home Assistant (HACS): each satellite gets a
  device with its own area and a `voice_timer_list` entity for its timers.
- A command's response can differ by how it went: `done`, `failed` (Home
  Assistant refused), a timer's `no_timers`, `not_found`, `ambiguous`,
  `already` or `missing_duration`, or an `outcome` a script returns. A plain
  response is only for `done`; the `voice_call_command` event carries the
  outcome.
- Pausing a paused timer (or resuming a running one) says it already is,
  instead of that there's no such timer.
- Music, on Music Assistant: "play …" searches and plays the closest match,
  plus pause, resume, stop, next, previous, volume, mute, shuffle, repeat,
  "play more like this", "add … to the queue" and "what's playing". A
  satellite's player is its Sendspin client's, found by MAC. The volume
  commands moved from Basics to the new Music group.
- Commands have icons, and the library marks the ones you already have.
- Try reads the typed text as heard word for word, without speech
  recognition; "Say it first" speaks it and recognizes the audio, as before.
- Library: covers and locks (by name, or by type and area or floor), "are any
  lights on", "is the garage door open", "is the front door locked", "is
  everything locked". Adding a command adds the shared value lists it uses.
- The VPE's light ring flashes while a timer rings and otherwise shows the time
  left on the timer that finishes soonest. The timer alarm is the stock VPE's
  sound, and "stop" -- or the center button -- now stops it at once.
- Wake words per satellite, several at once: "Hey Jarvis" and "Hey Mycroft"
  join "Okay Nabu", each verified against its own confusables and run at its
  own (low) cutoff. Custom microWakeWord models (Tater, ESPHome) can be
  uploaded or put in `wake_words/`; they run unverified at their manifest's
  cutoff. Firmware: six model slots, reported in `HELLO`.
- Command groups: sets of commands given to the whole home or to a
  satellite, which then runs only the commands in its groups (its own, else
  the home's). Made and filled on the Commands page; a command's editor ticks
  its groups too.
- A multilingual built-in speech model (parakeet-tdt-0.6b-v3, 25 European
  languages) in place of the English one, switched while running. Home
  Assistant STT and TTS entities get a language, for every satellite or per
  satellite.
- When nothing matches: the closest command by meaning runs when it is very
  close and needs nothing said, and a small LLM (Granite 4.0 350M, or Hammer
  2.1 0.5B) can pick a command and fill in its slots. Both only run the
  commands chosen for them (`fallback: [meaning, llm]`, ticked per command or
  many at once on the Commands page); none are chosen to begin with.
- Questions no command takes are answered: exactly by skills (spelling,
  dates, clock arithmetic, unit conversions, math), "I can't look that up"
  for live ones, and the rest by a small LLM (Granite 4.0 350M, or Gemma 3
  1B), downloaded on first start. Each skill is turned on or off on the new
  Skills tab, for the whole home or per satellite.
  Skills read questions as typed or formatted ("50°F", "12 × 8", "½ cup",
  "$80").
- Library: set an alarm (a timer that rings at a clock time: "wake me up at
  half past six"), take something off the shopping list, add to the to-do
  list.
- Every model in the Tater Wake Words catalog (851) runs on the satellite;
  `scripts/check_wake_words.py` checks a collection. A manifest cutoff of 1.0
  is run as 0.99.
- Home Assistant STT and TTS entities, for every satellite or per satellite.
  STT writes what TDT would (examples, free text, hints) and only when needed;
  TTS speaks on the satellite's own media player or on another one.

## 0.1.0

- Commands are the app's own: made in the web UI (or YAML), recognized with
  adapt-ctc on Parakeet's CTC output, and run in Home Assistant as scripts,
  scenes, actions or spoken responses. Sentence triggers are no longer read.
- Satellites can be given an area; commands can be scoped to areas or
  satellites and overridden there.
- Calls are traced stage by stage, and with debugging on, kept with their audio.
