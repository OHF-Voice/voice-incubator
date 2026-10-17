# Changelog

## 0.2.0

- **Listen live** to a satellite on the Test page: its microphone, played
  and drawn (level, waveform, spectrogram) as it comes. The satellite streams
  only while someone listens, and the Voice PE's ring blinks red meanwhile
  (new voice_call firmware: `MONITOR`, `AUDIO_MONITOR`).
- Debugging a satellite moved from the Satellites page to the Test page,
  where a select picks the satellite.
- A kept session's voice can be added to a speaker's, known or new ("Who
  spoke" on its page).
- What nothing here understood can go to a Home Assistant **conversation
  agent** (Settings page), in a conversation kept on each satellite for 5
  minutes after its last answer, as on the Voice PE. It takes the place of
  the small question LLM of earlier builds (`questions_llm`,
  `questions_model`).
- An **Unknown command**, always at the top of the Commands page: a response
  template for when nothing understood a sentence ("Sorry, I couldn't
  understand the sentence {{ transcript }}."), with the near miss as `hint`
  (`unknown:` in commands.yaml).
- The Commands page lists commands by the library's sections (Basics, Covers
  and locks, Music, Timers), under Custom for your own. A command added from
  the library remembers its section (`library:`).
- Overrides are gone. **Duplicate** a command instead, into the command group
  of the satellites where it should do something else: the copy can take the
  original's place in that group, and says the same example sentences.
- A command has no "Where" (`scope`) any more: command groups say where a
  command is available. A `scope` left in commands.yaml is an error that says
  so.
- A **Settings** page holds what the Commands page had besides commands and
  command groups: when nothing matches, the conversation agent, responses -- and, under
  Advanced, commands.yaml (the YAML tab is gone).
- The web UI's Try page is now **Test**, and Calls is now **Sessions**. A
  command's page no longer has its own "Try it"; per-satellite choices that
  follow every satellite's read "Default".
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
- When nothing matches in the audio, keywords and names are matched in the
  speech model's transcript (TDT or Home Assistant STT) before anything
  else: "unlock front door" ran from Home Assistant Cloud's transcript where
  the audio's evidence fell just short.
- "Turn on the fan in the office": a device named with a room is the one
  of that name there (names may repeat); none there, and the satellite says
  so. Without a room, a shared name is the one in the satellite's room.
- A room or floor you say must be one the command takes: "pause the music
  upstairs" no longer pauses it here, "play music in the office" no longer
  resumes it here. "Play jazz in the kitchen" and "play more like this
  upstairs" play there, and "play jazz on the Mark 2" plays jazz on a
  command that takes that device, instead of searching for "jazz on the
  Mark 2".
- "Move the music to the kitchen / upstairs / the office speaker": what
  plays here moves there, queue and all (Music Assistant's transfer_queue).
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
- When nothing matches: meaning picks the command a sentence is very close
  to, and a small model hears what was said in it -- or picks the command
  itself when meaning isn't sure -- and fills in its slots: GLiNER2.5 small
  (4-bit, without PyTorch, about a quarter of a second on a Pi 5), or a small
  LLM (Granite 4.0 350M, or Hammer 2.1 0.5B). Both only run the commands
  chosen for them (`fallback: [meaning, llm]`, ticked per command or many at
  once on the Commands page); none are chosen to begin with.
- Slots are never optional: a command lists the ways it may be said
  (`combinations: [[], [area], [floor]]`), each heard whole, so it never
  runs with something said left out ("kill the lights in the office" no
  longer turns off the satellite's own area). The editor's "Ways to say it".
- New command opens the library in a dialog -- search it, or show one group
  -- with a custom command always at the top. The Library page is gone.
- How much a command says when it's done: a beep, a brief fixed sentence
  (cached by text-to-speech), a specific one, or a verbose one -- set per
  command (and per area or satellite), with a response for each level.
  Library commands come with theirs: lights beep, timers say what they did.
  A command that beeps but acted in another area says the specific sentence
  instead (Settings page, "Responses").
- Questions no command takes are answered exactly by skills (spelling,
  dates, clock arithmetic, unit conversions, math), and "I can't look that
  up" for live ones. Each skill is turned on or off on the new Skills tab,
  for the whole home or per satellite.
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
