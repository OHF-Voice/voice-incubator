# Changelog

## 0.2.0

- A satellite's settings are in **Music** (its player), **Input**
  (speech-to-text and its language) and **Output** (text-to-speech, its
  language and voice, and where responses are spoken): collapsed, each
  saying what it uses.
- The UI says less: each page and panel is a title and, at most, a sentence.
  The rest is behind a **?** beside the title or label -- shown on hover, on
  focus, or with a click or tap, and read by screen readers -- and labels
  and titles no longer repeat the page's ("Preferred", not "Preferred wake
  word", on the Wake words page). Field labels are now real labels of
  their controls.
- A **skill's page** sets how much it says -- brief ("96."), specific
  ("It's 96.", the default) or verbose ("Twelve times eight is 96.") -- and a
  response of your own for any level, a Home Assistant template. It takes
  the skill off the Home, or the satellite it was opened from; Add adds it.
- **Can't look it up** is gone: a question about now or here goes to the
  conversation agent, or else the Unknown command.
- **New command** is now **Add**, on the Home and a satellite's page, and
  offers skills too.
- **Home** replaces the Commands, Skills, Satellites and Test pages: a tile
  for each satellite (name, area, wake word, what it runs), then the Home's
  commands and skills grouped by what they're for (your own first), and the
  Unknown command. A satellite's tile opens its page: its settings and icon,
  its own commands and skills, and testing it (a typed sentence, listening
  live, debugging). The Home's listening and speaking moved to Settings.
- **Command groups are gone.** A command or skill is the Home's or one
  satellite's own; a satellite takes the Home's unless told not to. Where a
  satellite's own and the Home's take the same sentence, its own goes first:
  duplicate one of the Home's onto a satellite to do something else there.
  A command that was in groups loads as the Home's, and the Home page says
  which.
- A skill turned off for one satellite (before) no longer takes it away: a
  satellite either takes all the Home's or none.
- **Sessions** show the probability each wake word came in with -- the
  satellite model's window average that met the cutoff, its highest single
  inference, and the cutoff -- in the list and on a session's page. Needs
  new voice_call firmware (`CALL_START` carries them).
- **Wake words** has a page of its own. Each satellite listens for one wake
  word: the home's **preferred** one, or one of its own, both picked there
  (the Satellites page says which and links to it). A satellite given two
  before listens for the first.
- Each wake word's **cutoff** on the satellite, **confusables**, and the
  verifier's **thresholds** and second opinion can be changed there, kept in
  `wake_words.yaml`. Saving rebuilds that wake word's verifier while the app
  runs; **Restore its defaults** undoes it.
- A **custom wake word** given confusables is verified like the built-in
  ones, and can then run at a lower cutoff.
- A word the verifier doesn't know needs a pronunciation: the page guesses
  one with espeak-ng, to keep or correct.
- Example sentences may be templates, as in Home Assistant's sentences:
  `(turn|switch) on the light[s]` is four sentences, each matched word for
  word. `{slots}` and `<rules>` aren't supported. The editor shows what each
  template stands for, and the library's examples use them.
- An example sentence matches only as all that was said: the example
  "pause" (Pause the music) took "pause the timer" ahead of the timer's
  keywords, and "resume" "resume the timer" (adapt-ctc `examples_whole`).
- **Never mind** in the library ("never mind", "cancel", "forget it",
  "nothing"): a command that does nothing, and by default says nothing --
  not even a sound. A command may now do nothing (choose **Nothing** under
  What happens), and **Silent** is a level of how much a command says (0;
  the numbers 1 to 4 keep their levels). Said over a ringing timer, it stops
  the timer.
- "Learn a new voice for …" is gone for now: add a session's voice to a
  speaker on its page (Sessions) instead.
- When nothing matches, GLiNER picks among every command -- those that hear
  nothing too, "have you got the time" -- or "not a command". Its pick with
  nothing heard in it runs only when meaning puts the command first, ahead
  of everyday requests that are no command ("thank you").
- A command's versions are one choice to meaning and the model step, and
  what was said picks the version: "kill the lights" ran nothing, too close
  to its own version for an area or floor.
- A step turned off on the Settings page, for a command chosen for it: the
  command's page says "off in Settings", and a call's trace says it was
  skipped.
- The library's commands come in **versions** for what they act on: on this
  satellite (nothing named), on a device by name, in an area or floor by
  name -- "Play music" is Play music on this satellite, on a different
  device, in an area or floor. Each is a command of its own; New command
  shows each command as one row that opens to its versions. New versions:
  every music control on a device or in a place, "is the desk lamp on", a
  light's color by name. "Turn on the lights" and "Turn on a device" are now
  one command, Turn on (also Turn off, Open covers, Close covers, Are covers
  open), and commands added before keep their library section.
- **Listen live** to a satellite on the Test page: its microphone, played
  and drawn as it comes (level, waveform, and each wake word and voice
  activity model's probability against its cutoff). The satellite streams
  only while someone listens, the Voice PE's ring blinks red meanwhile, and
  it doesn't answer a wake word then (new voice_call firmware: `MONITOR`,
  `AUDIO_MONITOR`, `WAKE_REPORT`).
- Speaker colors on the ring no longer look red (orange and pink did, after
  the LEDs' gamma correction); a speaker who had one gets a new color.
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
  Mark 2". Said to a command that can't take the room, the satellite says
  so ("…but I can't pick a room for it"), free text or not.
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
