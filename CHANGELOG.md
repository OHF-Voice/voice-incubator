# Changelog

## 0.2.0

- **The Home page is by area.** The whole home and each area are listed down
  the side (by floor; a select on a phone), each with its satellites and its
  **Commands**, **Names** and **Settings** -- the Names and Settings pages
  are now Whole Home's tabs.
  - A command is the whole home's, heard from every satellite, or an area's
    own (`area:`), heard only there and first there where both take a
    sentence. The whole home's commands and skills can be turned off in an
    area (`off:`), a group at a time too; a satellite there that hears one
    says "That command isn't available in the Kitchen." Satellites in no
    area have the whole home's only. Per-satellite lists (`satellites:`) are
    gone; nothing stored used them.
  - An area may have its own thermostat, shopping and to-do lists, built-in
    voice (downloaded when first used, two kept loaded besides the whole
    home's), sounds and instrument, silence to end, and **Response Level**
    (brief, or a beep; a question still answers) -- the whole home may cap
    it too. The whole home's settings say which areas have their own.
  - An area's Names say what it is called and hold its devices (one added
    there goes there); Whole Home's list every name heard everywhere, the
    devices in no area, the floors and the value lists.

- **Home Assistant's voice assistants can use the app** (Wyoming 1.11, on
  port 10800; option `wyoming_port`, 0 for none). The Supervisor announces it
  to Home Assistant, and only Home Assistant may connect (this machine and
  the Supervisor's network).
  - **Speech-to-text** hears as the app's satellites do -- with the TDT head,
    or the STT entity chosen on the Settings page: asked when its words are
    needed, or hearing every command as Home Assistant streams it in
    ("Always, as you speak"); the TDT head stands in when it fails -- and
    writes what it heard as the command it heard: the names and numbers the
    command was heard with are written
    over the TDT words said there ("Turn on the Nanoleaf." for "Turn on the
    Neno leave"), as the Names page has them -- kept only when that reads
    back as the same command, else the words as heard, else the command's
    own words. A name is written as it was said ("Unlock Garage Side Door."
    for the side door by its name in the garage, not its whole house name
    after "Garage"), and what is written must leave no more words unexplained
    than the audio did. An STT entity's words, when it heard them, are tried
    as they are first. What isn't a command is the words as heard. The conversation agent, given the same
    words from the same satellite within 30 seconds, recognizes their audio
    again (in the satellite's area) rather than the words.
  - **Text-to-speech** is Piper with every voice in the catalog, each
    downloaded the first time it is asked for and removed once unused for 30
    days (never one the app speaks with); text in pieces is said a sentence
    at a time.
  - **The conversation agent** is the app's commands, skills, fallback and
    agent, as on one of its satellites in the asking satellite's area (its
    Home Assistant device's). An LLM's answer is streamed; a question back (a
    command's confirm, the agent's) keeps the conversation going, and the
    answer comes in it. A command that beeps says its brief response, as Home
    Assistant can't be asked for the sound with Wyoming 1.11; a timer goes to
    Home Assistant's own agent, to ring on that satellite. Sessions shows
    each request.
  - **An STT entity called Voice Labs** -- the app's own -- isn't offered in
    Settings, and one chosen before is never used: the TDT head hears
    instead, on every satellite.
- **"Stop" ends a reply**, said alone while the satellite says it, as on the
  stock firmware. Firmware that says so (HELLO `stop_word`) keeps the "stop"
  model loaded, pushed with the wake words, and runs it only while a reply is
  said or a timer rings; older firmware still has it pushed for each ring. A
  "stop" heard just as what it stops ended is hung up on, not answered. The
  firmware also starts a call from the center button (press to talk), shows
  the mute switch on the ring, keeps Sendspin's delay for a speaker on the
  headphone jack, and plays the stock firmware's sounds for both.
- **The acknowledgement sound waits a beat** after the heard sound (half a
  second) when a command is done at once, rather than running straight on
  from it.
- **The sounds are played on any instrument, from any pitch.** The app
  plays them itself with FluidSynth from the MuseScore General SoundFont
  (Debian's `musescore-general-soundfont-small`, about 40 MB, MIT), when
  first wanted: its 181 melodic instruments, by General MIDI's families, in
  place of the two played beforehand. The Settings page picks the
  instrument and the note they start from (`pitch`; each instrument has its
  own, the electric piano G5, the rest C5), and **Try** plays them in the
  browser; an area may have its own of both. `electric_piano` in settings is
  the Tine Electric Piano, as it was.
- **Brighter and Dimmer go 30 percent at a time**, not a fifth. **Set the
  brightness** also hears the lights turned on, up or down, brightened or
  dimmed to a number ("dim the lights to 20", "turn on the kitchen lights to
  50 percent", "turn the desk lamp up to 80 percent"), and the lights given
  a number alone ("set the lights to 50", which set the thermostat). **Set
  the volume** likewise hears it turned up or down to a number ("turn the TV
  up to 50", "turn down the volume to 20 percent"), and **Set your volume**
  "turn yourself down to 20".
- **Turn up the heat and Turn down the heat** in the library (English): the
  home's thermostat a degree Celsius warmer or cooler, two in Fahrenheit, both
  ends in heat and cool mode ("turn up the heat", "turn the thermostat down",
  "raise the temperature"), where "turn up the thermostat" turned up the
  volume. **Set the thermostat** also hears the heat ("turn the heat up to
  72").
- **Do not disturb, in each area.** The integration gives each area with a
  satellite a switch (`switch.bedroom_do_not_disturb`), for automations; the
  area's page has it too. On, announcements from elsewhere skip the area (and
  say so), the intercom doesn't connect there, and music doesn't start,
  move, resume, skip or get louder there; what is said in the area still
  works there. A command keeps out with `do_not_disturb: true` (**Kept out of
  areas on do not disturb** on its page), and an announcement, call or music
  command gets through with `false`. With an older integration, nothing is
  kept out.
- **An LLM's answer is said as it comes.** The integration streams a
  conversation agent's answer to the app as the agent writes it
  (`voice_labs/conversation/stream`, the agent asked in its chat log as
  Assist asks it, so the log keeps the whole turn), and the app says it a
  sentence at a time while the rest is still coming: Piper's, or a TTS
  entity's through a TTS stream played on the satellite, given a sentence at
  a time (`voice_labs/tts/stream`). "Stop" over it ends the agent's turn too.
  Home Assistant's own agent, and every agent with an older integration, is
  asked as before. The conversation is now the area's, shared by every
  satellite there, not each satellite's.
- **The agent's question is answered without the wake word.** When the
  conversation agent asks something back (`continue_conversation`), a new
  sound -- the octave above, twice -- follows its answer, and the satellite's
  microphone opens again: firmware that can (HELLO `listen`) is hung up on
  with `listen`, and starts a call with the wake word `follow_up` and no
  pre-roll, its ring spinning slowly, unless muted. What is said goes to the
  agent as said, in the same conversation, for as long as it asks; "cancel",
  "never mind" or "no thanks" alone ends it, and nothing said within the
  satellite's no-speech time (End of speech: 8 seconds unless changed) ends
  it quietly, kept nowhere. Sessions show a follow-up and its answer.
  An answer counts as a question with only an emoji, markdown or a quote
  after its question mark ("What do you love? 🌟"), which Home Assistant's
  `continue_conversation` doesn't look past.
- **With Both VADs, a pause after the wake word no longer ends the call.**
  The wake word was given to microVAD as well as Silero, and microVAD carried
  it on for over a second: a pause after "Okay Nabu" -- or the quiet before
  answering a follow-up -- was speech that had ended, and the call ended
  with nothing said (a follow-up after 1.25 s). Only Silero hears it now.
- **A timer says its length when it starts**, even at brief ("Started a 15
  minute timer.", "Started the pizza timer for 15 minutes."): it is what says
  the timer was heard right.
- **English says the verb first**: "Cancelled the 10 minute timer.", "Set an
  alarm for 7:30 AM.", "Paused the timer.", "Turned up the volume.",
  "Skipped to the next track.", "Brightened." -- not "The 10 minute timer
  cancelled.", "Alarm set.", "Volume up.", "Next track.", "Brighter."
- **A command can ask first.** Any command may ask yes or no before it acts
  (`confirm`: always, or when a template renders true; **Asks first** on its
  page, with the question), and the answer is heard the same way, without
  the wake word. Yes does it; no says "Okay."; anything else is "Sorry, I
  didn't catch that." and nothing is done; another command said instead
  runs instead. The answer is read by its words first (each language's yes
  and no), and by meaning only when they leave it open. **Cancel every
  timer** asks with two or more running ("Cancel all 3 timers?"). Firmware
  that can't hear the answer isn't asked.
- **"It" is what was last spoken of.** Each area remembers the devices its
  last command acted on or asked about by name, for as long as its
  conversation is kept (five minutes). A sentence with "it", "them" or
  "that" in it is heard again with their names in its place -- "is the
  front door locked?" ... "lock it" -- and taken when that is a command
  acting on them, in place of what it matched itself ("turn it up" after the
  desk lamp is the lamp's brightness, not the music's volume). Anything else
  ("what time is it") is as it was heard; with nothing here to refer to, it
  goes on as before -- to the conversation agent, which keeps its own
  conversation. Sessions show what it was heard as.
- **An area added is named.** Added on the Home page, an area is called
  what Home Assistant calls it -- its name and aliases, under **Area Name** --
  unless it is named already or another area has that name.
- **A whole house name that is a command word is flagged** on the Names
  page ("command word"): a device called "Light" from every area takes
  "turn off the light" for itself alone. In its area only, it is much the
  same as the lights there, and isn't flagged.
- **Whole house names emptied stay empty.** Clearing "In the whole house"
  on a device and saving left it with its area's names everywhere, as if
  none had been given: it is heard only in its area now. Save such a device
  again to keep it there.
- **A name not heard as said gives way to a match as good.** "Turn off
  utility room light", with the Exercise Room's light called "Light", was
  that light with the Utility Room said -- and refused, there being none
  there -- where it is as well the Utility Room's lights: those are turned
  off now. The refusal is said only when no match as good runs.
- **Home Assistant Cloud is used when found.** The first time the app sees
  Home Assistant Cloud (someone signed in), its speech-to-text writes what
  the built-in models would -- always running, so it hears every command as
  it is said and its words are ready when needed -- and its voice says the
  replies, for every satellite, unless one was chosen already.
  Once: choose another in Settings, the built-in models too, and it stays.
- **Meaning and the model can run Home Assistant's sentence triggers**,
  chosen for them on **When nothing matches** (Settings) -- what was said
  is no sentence of the trigger's, and would run nothing in Home Assistant,
  so it is sent one that is:
  - **meaning**, for one with sentences of its own: "time to hit the hay"
    for a "good night" trigger sends the sentence closest to what was said.
    Sessions say which.
  - **the model** (GLiNER), for one with free text: "put on Kind of Blue by
    Miles Davis" for `play {album} by {artist}` hears each wildcard by its
    name and sends "play Kind of Blue by Miles Davis" -- once Home Assistant
    would hear it back as that trigger, the same words in each, else
    nothing runs. It knows a trigger by its automation's description, else
    its name, and is given it among the closest commands in meaning (its
    templates, said with their wildcards' names).
- **The conversation agent can have a voice of its own** (Settings,
  Conversation agent, **Its voice**): a built-in voice and speaker, or a
  text-to-speech entity and its voice, so its answers sound unlike the
  commands'. By default they're said as everything else is.
- **A sentence trigger has a page.** Open it from the Home page: its
  sentences, where it's heard, and whether meaning or the model may run it
  when nothing matches, with **Edit in Home Assistant** for its automation
  -- which now opens Home Assistant's own page with the app run on its own
  (it went nowhere). Lists show a trigger's title only, and a skill's
  description in place of its examples.
- **The Home page's words.** An area's Commands are under its name and
  **Whole Home**, and an area with no satellite has no Commands tab (unless
  it has its own). Its Names are **Area Name** and **Device Names**; its
  Settings say **Home Default** for the whole home's. **Say at most** is
  **Response Level**, and its "each command's own" is **Command default**.
- **The Wake words page's words.** **Tuning** is **Models**, each wake word
  marked built in or custom by its icon, and a satellite without its own
  listens for **Preferred**. **Upload** comes before **Train**.
- **Set the thermostat is a version of Set the temperature.** The two set
  the same thermostat when nothing is named: now one command, for the
  thermostat (the home's or the area's own, as the other thermostat
  commands), hearing "set the temperature to 70" too; by name is its other
  version, through Home Assistant's intent as before. One added as Set the
  thermostat follows it. It says "Thermostat set to 70 degrees." (not "Set
  the thermostat …", which sounds like an order), and the mode and fan alike
  ("Thermostat set to heat."). **Set the temperature in an area or floor is
  gone** (every language).
- **Add a command shows related commands together** (English): Thermostat,
  Blinds and covers, Locks, Date and time, On and off, Brightness and color,
  Shopping list, To-do list, Play music, Pause and skip, Volume, Shuffle and
  repeat, Reminders, Satellite volume, Timers, Alarms and Weather -- a row
  each, opened to its commands (a command's versions still open from it).
  The library gives each file's `topics` and each command's `topic`.
- **A skill has a switch on Whole Home's Commands**, as a sentence trigger
  has, and **Delete** on its page: off the list (and off) until added again.
  Time from now is "How long until a future time."
- **A command the library no longer has is kept, not heard**: the others
  still are (before, none were), and the Home page says to delete it.
- **A standalone image**, `ghcr.io/ohf-voice/voice-labs` (amd64 and
  aarch64): the app without Home Assistant's Supervisor, given Home
  Assistant's address and a long-lived access token (`HASS_URL`,
  `HASS_TOKEN`), with no wake word training and no login (README.md,
  "Standalone"). Built and published by a workflow in this repository, on
  demand or for a `voice_labs-v*` tag, each architecture on its own runner.
- **The image is a quarter of the size**: 3.8 GB was 2 GB of TensorFlow
  (the standalone image has none; the app keeps it, for training wake
  words), 300 MB of compilers -- now only in a build stage, where
  adapt-ctc's Viterbi and the wake verifier's decoder are compiled -- 200 MB
  of graphics libraries FluidSynth brought through SDL -- it is built
  without its audio drivers, which playing the sounds never used -- and 130
  MB of Kaldi programs the verifier never runs (only its 36 are kept,
  `wyoming_wake_kaldi/prune.py`). The standalone image is 950 MB.
- **A satellite's device is made only when it connects.** The others the
  app remembers -- away, disabled, or not the one `--host` names -- are
  looked up, not registered: a device removed in Home Assistant stays
  removed until its satellite connects again.
- **An icon**, a flask, for the app and the integration (Home Assistant
  shows the integration's from 2026.3), and the logo in the web UI's header.
- **A caption page**, `static/caption.html` beside the web UI: the last
  command's area, words and answer, as large as the window allows, for a
  screen recording (`?satellite=<id>` for one satellite's, `?theme=light`).
- **A new home tells the time and the date.** It starts with the library's
  What time is it, What's the date and Never mind, following it, in the
  app's language.
  Deleted, they stay deleted; a home that has commands already is left as
  it is.
- **A sentence trigger's slot can be a value list's.** One named as a value
  list (`start the {program} cycle`, with a list `program` on the Names
  page, or a built-in one) is heard as one of its phrases -- in the audio,
  as example sentences, when that keeps it within 512 of them, else on the
  transcript -- and Home Assistant is sent the list's value in its place:
  "start the economy cycle" as "start the eco cycle". The trigger's page
  says which lists it hears.
- **A built-in value list can be turned off** on the Names page (`lists:
  {colors: false}`): the home has no list by that name, for commands or
  sentence triggers, until it is turned on again. A command that uses it
  can't be saved meanwhile, and says which.
- **Free text doesn't start with a keyword the transcript misspelled.** "add
  pick up groceries to my to-do list" added "and pick up groceries": the TDT
  head wrote "add" as "and", a few frames late, inside the text's time. A
  word one letter off the keyword the text follows (or comes before), at
  that end, is the keyword's -- unless the CTC head heard such a word in the
  text too ("put pot roast on the list").
- **Take off my to-do list** and **Check off my to-do list**, in the
  library's Lists: "take fix the gate off my to-do list", "check off call
  the plumber on my to-do list" act on the speaker's own list (People),
  else the home's, as Add to my to-do list does. Before, the home's
  versions took them, and said "That isn't set up yet." without a home
  list.
- **A command is heard as one of the home's sentences first.** Every
  sentence the commands say -- a command's words in any order, its names and
  numbers, fillers between, its examples as written -- is one graph, and the
  CTC output is searched for the best of them from just before the wake
  word's end (a native search, ~40 ms on a Pi 5; the graph 13 MB for 41
  commands). One close enough to what was heard, and like what the model
  heard on its own, is read as typed; anything else -- free text, two
  commands together, a sentence too far from the audio -- goes to the
  keywords, examples and transcript as before. On the office-multicap
  corpus (four speakers, 3,244 commands): 79.4% -> 86.0% right, the wrong
  thing done 3.3% -> 4.6% of the time (adapt-ctc's `grammar_max_cost`, 8).
  In talk overheard after a false wake, a command ran 6 times in 400, as
  before. Sessions show the sentence and its cost, taken or not. Answers to
  a question aren't searched for.
- **Kaldi can hear commands instead of Parakeet** (Settings, **Commands
  heard by**; Parakeet stays the default), as Speech-to-Phrase did, with its
  English model. It hears only example sentences and Home Assistant's
  sentence triggers, word for word -- free text only from a value list named
  as its slot -- and refuses what is too far from a background of everyday
  words. A command it can't hear says so on the Commands list and its page.
  The **Pronunciations** page (in Kaldi mode) has every word's, guessed by
  phonetisaurus where the dictionary lacks one, and yours in place of any.
  On the office-multicap corpus, its own sentences the examples: 79.3% of
  commands right, 8.1% wrong, 81.4% of the rest refused; talk overheard ran
  a command 7 times in 400. Wyoming speech-to-text and answers to a question
  are heard the same way. The image carries the model's G2P and
  phonetisaurus (43 MB).
- **What's on the list confirms a doubtful command on it.** "Check coffee
  filters off the shopping list", heard as "to check cffee filters off the
  shopping list" -- a word left over, in the transcript too -- did nothing.
  When what either head heard names something on the list it acts on, it
  runs (Coffee filters, checked off). Sessions show the step.
- **After "okay now" the command starts where Kaldi ends the wake word.**
  The CTC head writes "okay Naboo" as "okay now" in about half of all calls,
  and then the rest of "Naboo" came before the command ("group what's twelve
  times eight"). Starting at the later of the two ends, a stray first word is
  rarer (the CTC transcript's 70% -> 40% of those calls), and 87.2% -> 88.6%
  of them came out right on the office-multicap corpus, none worse.
- **A question after a stray word is answered**: "group what's twelve
  times eight" -- the end of "Naboo" heard after the cut -- is "It's 96."
  A skill may leave out a heard transcript's first word or two when the rest
  is a whole question. And "twelve times a" -- "eight" misheard across the
  room -- is no longer "It's 12.": a bare "a" is no number to reckon with.
- **Never mind instead of "It looks like you're trying to never mind"**:
  when Never mind is the closest to what was said, it runs. And when not a
  word was heard after the wake word -- woken by mistake, most likely -- it
  runs rather than "Sorry, I didn't understand that.": in talk played into
  the room, that was 222 of 400 false wakes, and no command said to a
  satellite (of 900). Sessions say why.
- **"Announce in the kitchen dinner is ready"**, as well as "announce
  dinner is ready in the kitchen" and "... in the kitchen that dinner is
  ready": free text to the end may come after a place said with "in", "on"
  or "to" ("play in the kitchen some jazz" too). "Announce the kitchen is
  clean" is still the message.
- **Play music like it needs "play" or "more"** with "like" or "similar":
  "more like this", "play something similar", "play songs like Adele".
  "Like" alone -- "something like that", overheard -- played music.
- **The speech model hears a command from before the wake word's end.**
  Started at the cut, it got the first word wrong ("Get groceries" for "add
  get groceries"); now it decodes from half a second before, on the same
  encoder run as the keywords (30 ms rather than 174), and keeps the words
  from 0.2 s past the cut. On the office-multicap corpus: the command words'
  error rate 19.4% -> 16.7%, the first word right 70.8% -> 76.2%, commands
  word for word 32.7% -> 36.6%. A session's transcript no longer starts with
  the end of the wake word as often ("now we ...").
- **Sessions call a sentence trigger by its automation's name**
  ("Good night"), not its command's id (`trigger_1`): in the list, the
  session's summary and its Recognition step.
- **Who am I**, in the library's new People section: "who am I", "who's
  speaking" or "what's my name" says "You are Mike." -- who it takes the
  speaker to be, as "my" means them -- or "Sorry, I don't know who's
  speaking."
- **The README is the app's front page**: what it does, screenshots and
  diagrams, how to install it, and how other satellites use it through
  Wyoming. How it works, what was measured and how to work on it moved to
  DESIGN.md. OHF-Voice/voice-labs has the same front page; its guide is
  GUIDE.md there.
- **No paths from one machine.** Run from source, the app needs `HASS_TOKEN`
  or `--hass-token-file`: there is no default token file. The scripts take
  their tokens (`HASS_TOKEN` or `--token-file`), datasets and checkouts as
  arguments, and the embedding model is looked for in Hugging Face's cache
  where `HF_HUB_CACHE` or `HF_HOME` puts it.
- **Sessions say whether the speech-to-text entity ran.** The recognition
  step has a row for it -- not asked (its words weren't needed); asked, what
  it heard and how long it took; heard as you spoke, and how soon after you
  stopped; or failed or heard nothing, and the TDT's words instead -- and
  the log has a line each time it answers ("stt.home_assistant_cloud heard
  'is it going to rain on Saturday' (0.63 s)"). The conversation agent's
  sessions from Home Assistant say "From Home Assistant" for the words it
  was sent, which weren't the entity's.
- **The music's versions for "here" are "in this satellite's area"**
  ("Pause the music in this satellite's area", every language): they act on
  whatever plays there, not only the satellite's player. Their ids are the
  same; only the titles changed.
- **An area may have a music player** (its Settings, Music): where music
  asked for there plays when the satellite has no player of its own -- Home
  Assistant's satellites, through Wyoming, have none -- and before any other
  Music Assistant player there for pausing and the volume. The whole home has
  none.
- **Adding a device to an area offers the area's first.** Its Names search
  lists the area's devices before those in no area and then the rest, and
  before anything is typed only the area's not added yet (Whole Home's: those
  in no area). Devices already in its list are left out.
- **Add a command lists titles only.** A command's description and what it
  needs ("Then: …") are gone from the list; search still finds them, and
  what it needs is said on its page once added.
- **"How many more days until Christmas"** is answered as "how many days
  until Christmas" is (weeks and sleeps too).
- **The home's weather, thermostat and shopping list are found.** Where
  none is set, the app takes Home Assistant's weather entity, its
  thermostat, and its to-do list called "Shopping list" (the Shopping List
  integration's) -- when it has just one of each; with several, you choose.
  Once each: one you clear or change afterwards stays so.
- **Audio and Runs show only with a speech-to-text entity.** They were shown
  with the built-in model too, which always hears AGC and was never sent
  either: choosing AEC there changed nothing.
- **Someone's own voice is used.** The voice set on a person's page was
  picked for their replies, then not used: the satellite's said them.
- **The sounds are on a grand piano** unless you choose another instrument
  (from C5). Before, they were on the Tine Electric Piano, which is still
  there (from G5); a home that never chose one hears the piano now.
- **A satellite disabled in Home Assistant is left to another app.** A
  satellite takes one app at a time, and the app reconnects to one it loses
  within seconds, so another app (on a PC, against a test Home Assistant)
  could never have it. Disable its Voice Labs device in Home Assistant and
  the app lets go of it and leaves it alone; enable it (in Home Assistant,
  or **Enable** on its page here) and the app takes it back. Its tile and
  page say "disabled". `--host` still serves its device whatever Home
  Assistant says.
- **Alarms and reminders belong to the area**, as its timers do: set on one
  satellite, they ring on every satellite in the area -- "stop" on one stops
  them all -- and any of them lists and cancels them. Before, only the
  satellite that set one rang, and one turned off at the time lost it. Those
  set before are their satellite's area's now.
- **A long reminder runs.** "Remind me to brush my teeth at eleven forty
  four" matched, but its eight words of free text cost as much as a word left
  unexplained, so it was doubtful -- and as doubtful in the transcript that
  should have confirmed it, so nothing ran. Free text no longer counts
  toward doubt; it costs a little a word only so that a name is worth taking
  over it.
- **The command lists say less.** A command, listed or in the Add dialog, no
  longer has a summary of what it listens for ("turn on + a device"), and a
  library section on the Home page no longer lists the commands you haven't
  added from it (the Add dialog still says which are added).
- **The intercom connects where someone was.** Of several satellites in the
  area named, "connect to the living room" takes the one that last answered
  someone there, else the first by name as before -- and one busy or on
  older firmware is passed over for the next, not said to be busy.
- **The nearest satellite's "no" stands.** When the satellite that heard the
  wake word clearly best (6 dB or more above the next) refuses it, the
  others don't answer in its place: one in the next room, hearing it worse,
  took "turn off the basement light" for "turn off the light". Say it
  again; the nearest answers. Sessions show who refused it.
- **Two commands at once** (English): "turn on the lights and turn off the
  TV", "turn off the desk lamp and the floor lamp", "turn off the kitchen
  and bedroom lights", "turn on the TV and set its volume to 50%". Split at
  "and", "then" or "and then" when the whole sentence isn't one command,
  each part heard on its own audio, one that is no command alone borrowing
  the other's words; "its" is what the first acted on. They run in order,
  and are said as one sentence ("Turned off the lights and the TV."). Two at
  most; both or neither -- before, "turn off the lights and the TV" turned
  off the TV alone -- and one that asks first is asked on its own.
- **Timers belong to the area.** Every satellite in an area shares the
  area's timer list (the integration's `voice_timer_list.<area>_timers`):
  a timer set on one shows on each in the same color, is asked about or
  cancelled from any, and rings on all of them when it finishes -- "stop"
  on one stops them all. A satellite in no area keeps its own list. Needs
  the integration updated; automations on a satellite's own list follow the
  area's now.
- **Every running timer on the ring, each in its own color.** The whole ring
  is the longest timer's length, and each timer's color reaches as far as it
  has left, the soonest on top: where the colors change is when each
  finishes. Colors come from a fixed list, the first free one (the first is
  the LED Ring light's own, so a timer alone looks as before), round again
  once all are taken; each keeps its own while it runs. They are yellow,
  magenta, green, cyan and white -- each channel full or off, as the ring's
  gamma takes any between down to nearly nothing (orange showed red) -- each
  LED is one timer's, and with two or more running the ring is at 50%
  brightness at least by day. Every timer has an LED at least; paused ones
  aren't shown; at night, all amber. Needs the firmware updated; older
  firmware shows the soonest alone, as before.
- **Night mode**, in each area: its switch from the integration
  (`switch.<area>_night_mode`, at the top of the area's page too). On, a
  command that acts says nothing -- the ring shows how it went, one slow
  green pulse or two red blinks -- an answer or a command's question is said
  quietly (15 dB down, with Piper on the satellite), none of the sounds play,
  and the area is on do not disturb. The ring is dim and amber, and dark
  between calls. Timers and alarms ring as ever. Needs the integration and
  the firmware updated; commands see `satellite.night`.
- **A timer rings on the satellite's instrument**: the stock timer sound's
  perfect fifth (the area's pitch, then the fifth above), every 3.25 seconds,
  as loud as that sound. It rings once before saying which timer finished,
  then on until stopped -- for up to 15 minutes (was 5), as the stock
  firmware does. A calendar alarm rings the same way; a reminder rings once.
  The Settings page's Try plays the ring after the sounds. Without the
  SoundFont, the stock sound as before.
- **The Home Assistant connection no longer drops on every start.** A timer
  list reporting its timers before the models had loaded raised in the
  connection's reader, which closed it (back 5 seconds later). The timers
  now wait for the models, and a listener's mistake is logged rather than
  closing the connection; a connection lost says what lost it.
- **Home Assistant's sentence triggers are heard.** An automation that
  starts on a sentence is a command of the whole home's, listed on the Home
  page under **Home Assistant** -- by the automation's name, opening it in
  Home Assistant's editor, a blueprint's too -- and kept current as
  automations are saved, turned on or off, or deleted. The copy of a
  trigger the automation editor listens for while it is open isn't a
  second one. Its sentences are listened for like
  example sentences (with free text, `{album}`, matched on the transcript as
  Home Assistant matches them), and run by Home Assistant's own agent with
  the satellite's device, whatever agent answers questions: its response is
  said, and "Done" is the acknowledgement sound. A trigger goes first, as in
  Home Assistant: a command saying the same sentence runs only where the
  trigger is turned off (each has a switch, and an area turns it off like
  the whole home's commands); its row says "goes first". Needs `hassil`.
- **"Is today Halloween?"** The dates skill answers whether today or
  tomorrow is a holiday or a date ("is it Christmas tomorrow", "is today a
  holiday": "No, Christmas is in 55 days.", "No, Halloween was yesterday."),
  and a holiday that is today is "It's today." -- not "It's 0 days." Under a
  week, "how many weeks until" says the days.
- **The image is built from this folder alone.** wyoming-wake-kaldi (the
  wake verifier, with its resident decoder's source), microWakeWord and
  sentence-stream are vendored beside `voice_labs/`. The speaker model, the
  microWakeWord models, and the verifier's Kaldi model and tools are fetched
  as the image is built, from pinned revisions of
  [rhasspy/voice-labs-models](https://huggingface.co/rhasspy/voice-labs-models)
  and OpenVoiceOS/redimnet-b2-vox2-onnx, each checked by SHA-256; the decoder
  is compiled there, on x86 as on aarch64. `script/stage_build` is gone; a
  working copy fetches the same with `python -m voice_labs.downloads fetch
  models`.
- **The firmware moved to
  [OHF-Voice/voice-pe-alt-firmware](https://github.com/OHF-Voice/voice-pe-alt-firmware)**,
  which builds it and publishes its install page (`voice-pe-voice-labs`):
  Wi-Fi given over improv (Bluetooth or USB), a name from the satellite's
  MAC. `voice-labs-local.yaml` there flashes satellites in development from
  a checkout. `firmware/` here keeps the set-aside experiments.
- **The integration and the guide moved to
  [OHF-Voice/voice-labs](https://github.com/OHF-Voice/voice-labs)**: install
  the integration from there with HACS; its README is the user guide, and the
  app's documentation tab says how to get started. The app's `url` points
  there.
- **Piper's voices are downloaded when first used**, from
  [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) at a
  pinned revision (its catalog for US and British English is in the app,
  each file checked by MD5): Kristin by default, in place of Ellie, or another
  picked on the Settings page's **Built-in voice** -- with its sample, its
  speaker for a voice of several, and Piper's inference settings (length,
  noise and noise W scales, volume) to try before saving. The voice wake
  words are trained with is downloaded when training first needs it. The
  image carries no Piper voice.
- **The speech models are downloaded the first time the app starts** --
  Parakeet, EmbeddingGemma 2 and GLiNER, about 470 MB, from
  [rhasspy/voice-labs-models](https://huggingface.co/rhasspy/voice-labs-models)
  and onnx-community, each file checked -- into the app's own storage, kept
  across updates; its page shows how far it got. The image carries English
  only: the `language` option, the multilingual model, the other languages'
  models and the wake word training kit are gone from it (the app is
  English for now).
- **Voice Call is Voice Labs**, everywhere: the app (`voice_labs`, a new
  app to Home Assistant), the integration (domain `voice_labs`, and its
  `voice_labs/...` websocket commands), the `voice_labs_command` event, the
  Python package, and the firmware -- its `voice_labs` component, the
  `_voice-labs._tcp` service the app finds satellites by, and their names
  (`voice-labs-vpe`...). From an install before: install the new app and
  copy the old one's data to it, re-add the integration (automations on
  the event use its new name), and flash every satellite, which the app
  only finds once they run the new firmware.
- **The satellite's own sounds, on an electric piano or a koto** (General
  MIDI's from the Fluid R3 SoundFont, and MuseScore General's): two notes up
  the moment the command ends, in place of the thinking sound; on up to
  finish the chord when it's done with nothing to say (the
  acknowledgement); down a minor third before it says it didn't understand
  or went wrong; up the chord as the conversation agent is asked. Each
  plays whole, the reply after it. The Settings page picks the instrument
  (`instrument`) and turns them off (`sounds`; `thinking_sound: false` from
  before still does); `thinking_delay` is gone.
- **One wake word, one answer**: heard by several satellites within a
  second, whatever their areas, it is answered by the one that heard it best
  (the wake word furthest above the background, on the echo-cancelled
  audio); the others end their call (`answered_elsewhere`), and new firmware
  flashes their ring dimly. Sessions record each one's measure.
- **Piper speaks when Home Assistant can't reach the media player** a TTS
  entity would speak on (unavailable: an ESPHome device it can't connect to),
  rather than ten silent seconds waiting for it.
- **The Names page decides what voice acts on**: only the devices added
  there, each under its area (Home Assistant's, unless set there for voice).
  A device has **names in its area**, heard from a satellite there -- and
  from elsewhere with the area said ("the lamp in the office"), unless turned
  off -- and **names in the whole house**, heard from every satellite; each
  one device's where it is heard (a name two share is refused when saved,
  flagged on the page and never guessed). Added from Home Assistant, "Office
  Lamp" in the Office is "Lamp" there and "Office Lamp" everywhere. A light,
  cover, lock, thermostat, media player or door sensor is also **part of its
  area** unless turned off: "turn on the lights", "close the blinds", "lock
  up", pausing what plays and the questions about an area act on the
  devices added there, each by name; Home Assistant's exposure to Assist no
  longer counts. Templates see `devices` in place of `exposed`. A device is
  spoken of as it is heard where you are ("the lamp" in the office, "the
  office lamp" elsewhere). A commands file from before is read as version
  3, the file as it was kept beside it (`commands.before-names.json`).
- **Devices are said by their names on the Names page**, not Home
  Assistant's ("Outdoor Humidity is 54 percent.", not "GW2000B Humidity"):
  the library's replies, the app's own ("Ecobee can't …"), what an intent
  acted on, music players, and rooms and floors (`place.said`). Templates
  see `names`, by entity id.
- **Okay Nabu's cutoff is 0.4** (was 0.2): at 0.2 the satellite fired before
  the wake word was over, which is how the command came to begin inside it.
  A cutoff set on the Wake words page still wins.
- **A satellite's own silence that ends a command**: under Input on its page,
  over every satellite's (End of speech, Settings).
- **The wake word stays out of the command when Kaldi's refusal is
  overturned**: Kaldi refuses a wake word it aligns badly, and that
  alignment's end can fall inside it; overturned on the ratio, the command
  started there ("okay na|bu, turn on overhead light" heard "Able to turn on
  overhead light?", and not recognized). It starts no earlier than the
  satellite's detection now.
- **A name in several rooms isn't guessed**: "turn on the lamp" from a room
  with none, where others have one, does nothing and says where they are
  ("…but there's no Lamp here, only in the Office and in the Bedroom"); it
  used to turn one on. **Only from its room** (Names): a device that answers
  to its name only from a satellite in its area, or with its room said
  (`local: true`). Home Assistant's suggested aliases leave out one that is
  the name again.
- **The lists don't take what they have already**: adding to the shopping
  list or a to-do list leaves an item that is on it as it is ("Apples is
  already on the list.") and checks a checked-off one back on, rather than
  adding it again. Items are told apart by their words, apart from emoji,
  case, an article, a number and the plural ("two apples" is "🍎 Apples"; not
  by spelling: "pears" aren't "peas"). Commands: `adds_to_list`, the outcome
  `already_on_list`, the variables `back`, `added` and `put_back`; each
  language's `PLURALS`. **Emoji are never said**: a list item's or a reply's.
- **The app reaches "started"**: its health check asked `/api/status` from
  the machine itself, which only the ingress proxy may reach, so the
  Supervisor waited on it forever. It asks `/health` now, which says "ok" and
  nothing else, and which the machine may ask too.
- **learn-tv's app side set aside**, as its firmware was: no check for a
  talker in the null (two Silero passes on every wake word), no HELLO
  `aec_lead_samples` (the captures are the XMOS's 905 samples apart), no
  CALL_START `detector` or `swapped`. Calls recorded with it still replay as
  they were (`aec_lead`). `firmware/experiments/learn-tv/APP.md` and
  `app.patch` bring it back.
- **Commands grouped by what they're for**: Basics is split into Date and
  time, Lights and switches, Lists, Reminders and calendar, and Scenes and
  scripts; Set the temperature joins Climate; Never mind and a satellite's
  own volume join announcements and the intercom as Satellites; Timers is
  Timers and alarms. The Home page and the library list the same groups, in
  the same order (by title), the Home page a panel for each. Commands added
  from Basics follow it where it went (`from: basic/what_time` is Date and
  time's).
- **Time from now** in place of clock arithmetic ("what time is 45 minutes
  after 3 pm", seldom asked at home): "what time will it be in 45 minutes"
  ("It'll be 5:05 PM.") and "how long until 5 pm" ("40 minutes from now."),
  the next 5 o'clock to come, in all nine languages. The question skill keeps
  its id (`clock`) and its 12- or 24-hour setting. The library's **What time
  is it** and **How long is left** leave these to it ("hoe lang nog tot 17
  uur" was a timer's).
- **Pause, skip and the volume on any media player**: with nothing named,
  whatever plays in the satellite's area (its own music first), a TV among
  them; "the music" is Music Assistant's players only, "the TV" the TVs
  (`kind`, the `media_kinds` list). A TV that only steps its volume is
  stepped, and says it can't be set to one. New: **Turn yourself up / down**
  and **Set your volume** ("speak up", "set your volume to 40 percent"), on
  the satellite's own speaker (`satellite.device_media_player`).
- **Sensors** (English): "what's the outside temperature", "is the front door
  open" -- a sensor or binary sensor by its name (Names page), its value in
  its unit, a binary sensor in its device class's words. Sensors and binary
  sensors can be named now. New list: `binary_states`.
- **Announcements** (English): "announce dinner is ready" says it after a
  chime on every other satellite, or those in the area or floor named,
  turning their music down; a satellite busy with a call says it after.
  Commands can announce anything (`announce:`, a template).
- **Intercom** (English): "connect to the kitchen" joins this satellite and
  the kitchen's: each one's microphone on the other's speaker, wake words off,
  both rings blinking, until the button on either is pressed (or half an
  hour). Half duplex, as a speakerphone: the voice's band only, and the side
  not speaking turned down, so the two satellites don't squeal. Needs the new firmware (MONITOR's `intercom`, `voice_labs.end_intercom`,
  `voice_labs.is_intercom`).
- **Several items checked off or removed at once**: "check apples and
  bananas off the shopping list", "remove eggs milk and bread from the list"
  -- each one on the list, unless one item is called that whole ("mac and
  cheese"); one that isn't there is said after doing the others ("Kiwis
  isn't on the list."). Commands see them as `items`. New: **Check off the
  to-do list** and **Take off the to-do list** (English).
- **Play a genre** (English): "play some jazz music", "play the jazz genre".
  Music Assistant plays it by name; one it doesn't have is searched for
  like anything else. "Skip to the next track" and "go back to the previous
  track" match (the "to" was left over).
- **Scenes and scripts by name**: a command may listen for a scene name or a
  script name (Names page) and activate or run the one heard. The library's
  Activate a scene and Run a script are by name now, in every language --
  "make it cozy", "movie time", "good night" -- in place of a fixed scene and
  script to take control of. One in each room may share a name: "make it
  cozy" is the satellite's area's, "make it cozy in the bedroom" the
  bedroom's. A near miss asks which scene or script, not which device. Run a
  script says the speech the script returns (a mapping: `{speech: ...}`),
  else plays the acknowledgement sound, not "Done.".
- **Commands act only on what can do it** (`supports` on a command:
  brightness, color, color_temp, position, tilt, temperature, fan_mode,
  hvac_mode). A device named that can't is told ("Desk Lamp can't change
  color."); in an area, only the devices that can are acted on, and with none
  that can, "Nothing in the Kitchen can change color." (`not_supported`, also
  Home Assistant's own refusal for a missing feature). What was left out is
  said at the verbose level. The library's light, temperature and climate
  commands declare theirs: setting a color no longer turns on a plain light
  and says it is red. The catalog keeps what each entity can do; the command
  and Settings pages warn when an implied target or the home's can't.
- **A thermostat's own modes**: "set the thermostat fan mode to low" sets the
  thermostat's "on_low" or "auto_low", whichever keeps the rest of what it
  is set to; "Auto" is "auto". Before, Home Assistant refused "low". "The
  thermostat's fan is on on high" is "on high".
- **Set covers to a position** ("set the blinds to 50%", "lower the shades
  to 20 percent", "set the garage door to 30%"), in its three versions
  (English).
- **The home's weather entity, thermostat, shopping list and to-do list**,
  on the Settings page (Home): the library's commands for them use these
  unless one is given its own implied target (`home: weather`, `climate`,
  `shopping_list` or `todo_list` on a command), in every language. With
  neither, the satellite says "That isn't set up yet." (`not_set`). The
  library no longer assumes `weather.home`, `todo.shopping_list` or
  `todo.to_do`; "my" to-do list is the speaker's, else the home's.
- **Climate** (English): ask the thermostat ("what's the temperature
  inside"), set it ("set the thermostat to 70"), ask and set its mode ("set
  the thermostat to heat", "what is the thermostat doing"), and ask and set
  its fan ("set the fan mode to auto"). New list: `fan_modes`; fan only, in
  `hvac_modes`, is "fan only" (a fan alone is the thermostat's fan).
- **Today's forecast with no day said**: "what will the weather be like",
  "what's the forecast".
- **GLiNER picks among the commands chosen for it**, with the others close
  in meaning there to tell them apart; before, a likelier command not chosen
  refused the sentence. "I need cookies to be on the shopping list" was
  What's on the shopping list (0.44, not chosen, and no item) and ran
  nothing; it is Add to the shopping list (0.39) now. On the benchmark
  nothing else changed: each command is scored on its own, and a sentence
  meant for another leaves the chosen ones below the threshold. A call's
  trace names the likelier ones.
- **GLiNER's pick takes what it heard**: of the chosen ones, a pick that can
  take none of it gives way to the likeliest one that can.
- **Weather**, a library group of its own (English): the weather now ("partly
  cloudy", not "partlycloudy"), on a day ("tomorrow", "Saturday", "this
  weekend"), this week, and whether it will rain or snow ("do I need an
  umbrella", "will it rain tomorrow"), from the weather entity's daily
  forecast. New lists: `forecast_days`, `weather_kinds`. A command that
  follows the library finds one moved to another section by its id.
  Asked as a question too: "what will be the weather tomorrow", "what is the
  weather going to be like on Friday", "how does the weather look this week"
  ("what" was left over, so it was heard twice and not run).
- **Commands follow the library**, as Home Assistant automations follow
  their blueprints: one added from it is saved as a reference with what you
  set (where it runs, how much it says, the steps when nothing matches, your
  own example sentences, its implied target, its icon), so the library's
  fixes reach it. **Take control** on its page makes it yours to change all
  of it. Commands added before were made to follow it, with your settings
  kept (`commands.before-library.json` is the file as it was).
- **No YAML editing of commands**: a command is edited on its page alone.
- The library's shopping, to-do and weather commands take their entity from
  the implied target.
- **Several items added at once** are items of their own: "add oranges and
  bananas to the shopping list" adds two (split at the language's "and" and
  at commas), in every language's library.
- **An item on the shopping list is one the list has**: checking off and
  taking off match what was heard to the list's items as spoken ("test
  three" is "Test 3"), else the closest in spelling, else say it isn't on
  the list (`on_list` on a command).
- **Check off the shopping list** (done, and kept on it) apart from **Take
  off the shopping list** (removed), and **What's on the shopping list**,
  which says what is still to get. "Cross … off" is checking off now. In
  the English library.
- **Find a command** on the Home page: a search box over your commands and
  skills, by title, description, example sentences or keywords.
- **"What day is Christmas"** went to What's the date ("what day", with
  "is Christmas" left over), whose doubtful match the transcript "agreed"
  with by leaving the same words over. A transcript's match now counts only
  when it is sure; the dates skill answers, and "what day is …" a holiday
  says the weekday and the date ("It's Friday, December 25.").
- **Brighter, Dimmer and Set the color temperature** in the library (English),
  each in the satellite's area, in an area or floor named, or for a light by
  name: a fifth brighter or dimmer, never to off, of the lights that are on;
  and warm white, soft white, neutral white, cool white or daylight (a new
  `color_temperatures` list, in kelvin).
- **A name heard counts for more than none**: a match that named nothing,
  with a word left over, gives way to the version of it that takes a
  device, area or floor when the transcript names one. "Set bed light to
  green", heard as "set and light to green", turned the satellite's area
  green.
- **An STT entity that hears nothing** (no text, no error, as Home Assistant
  Cloud now and then answers) is stood in for by the built-in TDT model, as
  one that fails is: "lock the front door", heard by the CTC head as "move
  offck the front door", had nothing to be matched on again.
- **Several library commands at once**: tick them in Add, or select the
  ones in a group you have none of. Under each section of the Home page you
  have commands from, **Not added** names the rest of it ("Pause a timer"
  beside Resume a timer), to add one or all.
- **A doubtful match is heard again**: one that cost as much as a word it
  left unexplained runs only when the speech model's transcript says the
  same command (or the one the transcript says runs instead; none, nothing).
  "Resume the chicken timer", heard as "we resume the chicken pder", resumed
  the music; "pause the chicken timer" resumed it.
- **The command page**, most changed first: description and where it runs,
  example sentences, what happens, keywords, then the **implied target**
  (was Target). "What it does" is **Description**, and the id is no longer
  shown: a new command's is made from its description. Meaning and the
  model are chosen where they apply: **Also sentences that mean the same**
  under the example sentences, **Also when the model hears its free text or
  values** under the free text, offered only with free text or a value list.
- **People** (the Speakers page, renamed): someone with things of their own,
  a voice, or both. Their own to-do list, calendar and playlist are what "my"
  means when they speak -- **Add to my to-do list**, **Remind me**, **What's
  on my calendar**, **Play my playlist** (`mine` on a command) -- and replies
  to them can be in a voice of their own. Where nobody has their own, the
  command's own; where someone does, the speaker must be known ("Sorry, I
  don't know who's speaking."). The new commands are in English for now.
- **Who is speaking**: their voice, else whose wake word started it (a wake
  word can be someone's), else the only person -- unless a voice was heard
  and it was nobody's. With one person, no voice is needed. `speaker` says
  how (`by`).
- adapt-ctc: at the same cost, the match whose keywords say more of it comes
  first ("my to do list" over "to do list" with "my" a filler).
- **A command or skill runs on every satellite, on some, or on none**, set on
  its page (Runs on): `satellites: [key, ...]` in `commands.json`, and
  `skills: {id: [key, ...] | false}` in `settings.json`. A satellite no longer
  has commands or skills of its own, nor **Include the Home's**: its page is
  its settings and Test it. The Home page shows what runs on one satellite
  when it is picked above the list.
- **Several wake words per satellite**: up to five of its own (three on
  older firmware), else the home's preferred one, which is still one.
- **`wake_word`** for actions, scripts and responses: the `id`, `name` and
  `phrase` of the wake word that started the command.
- **Embeddings kept between starts** (`embeddings.npz` in the data folder):
  with EmbeddingGemma, a start no longer embeds every example again (91 s on
  an HA Green).
- **The center button cancels a call** and what it is saying (firmware:
  `voice_labs.cancel`).
- **Piper speaks a reply a sentence at a time**, starting with the first,
  and without a conversation agent's markdown (`sentence-stream`): a long
  answer read "asterisk", and began only once all of it was synthesized.
- The thinking sound an octave higher.
- **A thinking sound** while a reply is slow to come: when nothing has been
  said back a second after the command ended (or what is set, 0-10 s), the
  satellite plays a short sound, stopped as the reply begins. On by default
  (Settings, Responses).
- **A trained wake word's own name** ("Okay Nabu (Mike)"), beside others of
  the same phrase; the phrase field suggests each recorded phrase, with
  everyone's takes or one speaker's.
- **GLiNER without an embedding model** is given the commands chosen for it
  first, then others to tell them from: it was given the first eight by id,
  so a command sorted after them (a shopping list) was never offered.
- **English speaks with Ellie** (`en_US-ellie-medium`, Hi-Fi TTS reader 92,
  CC BY 4.0) in place of Jenny. It needs Piper 1.5 or later (merged vowel
  ids), which the image has.
- **The UI's files are JSON**: `commands.json`, `satellites.json`,
  `settings.json` and `wake_words.json` in `/data`, read once and written
  whole. Only the UI edits them: the Settings page's commands.yaml editor is
  gone, and a hand edit on disk is no longer looked for (a command's YAML tab
  stays). On an HA Green, reading the commands went from 386 ms to under a
  millisecond, and the command library is read and checked once rather than
  on every opening and every command added (1.6 s each time). Files from
  before are copied to JSON once and left as they were.
- **Intents**: a command can run a Home Assistant intent instead of an
  action, as Assist would -- in the area or floor named, or the satellite's,
  only on devices exposed to Assist; a device named (Names page) exposed or
  not; the unavailable said -- with outcomes for why it acted on nothing
  (nothing there, not exposed, unavailable, no area). The library's lights,
  thermostat, covers and locks use them, in every language, and its
  questions about a place ("are any lights on in the kitchen") count only
  what is exposed (`exposed`, a new template variable). The Voice Labs
  integration (0.2.0) runs them (`voice_labs/intent/handle`,
  `voice_labs/exposed`); without it, Home Assistant's REST API and exposure
  settings do.
- **Train a wake word** on the Wake words page (English): type the phrase,
  and Piper says it 2,000 ways (LibriTTS-R's 2,306 speakers, in synthetic
  rooms and noise) for a microWakeWord model warm-started from the base
  backbone, trained 10,000 steps against the training kit's negatives,
  temperature-scaled (T = 3, so its cutoff can be placed) and saved as a
  custom wake word at 0.8. About 20 minutes on a Raspberry Pi 5. The kit
  (`scripts/make_wake_training_kit.py`, about 3 GB) goes in
  `wake_training/` in the app's configuration folder; TensorFlow and
  microWakeWord's training (without its audio stack) are in the image.
- **Alarms and reminders on a calendar** (English): an alarm, or a reminder
  with a time, is an event on a Local Calendar named "Voice Labs" that the
  app creates in Home Assistant. It outlives restarts, may be for another day
  or repeat ("every weekday at 6:45"), shows in Home Assistant's calendar, and
  rings the satellite that set it: an alarm until "stop", a reminder said
  once ("remind me at 5 pm to call my mom"). New commands: Cancel an alarm,
  Cancel a reminder (by what it is about, its time or its day), Say the
  alarms, Say the reminders. A reminder without a time still goes on
  the to-do list. Other languages keep alarms as timers.
- **Language packs**: what the app hears and says is one language's pack
  (`voice_labs/languages/<code>`): its speech models, Piper voice, the
  matcher's filler words and name cues, the command library, and which
  skills and fallback steps it has, every sentence the app makes itself
  (timers, music, places, hints, apologies) and what it reads itself (an
  alarm's time, "the album …", "in the kitchen" after free text). The
  `language` option picks one: English, or **German** (`de`): NVIDIA's
  FastConformer hybrid for matching and writing, Piper's Thorsten, and the
  whole library in German -- names heard with German's endings ("des
  Wohnzimmers", "den kleinen Ventilator") and a room joined to what is in
  it ("das Küchenlicht"), durations ("eine halbe Stunde"), clock times
  ("halb sieben"), and "in der Küche" said with the room's gender. On 47
  sentences spoken by five voices, 210 of 235 do what was said; typed, all
  47. Its skills are German's: "wie schreibt man notwendig", "was ist zwölf
  mal acht", "wann ist Ostern", "wie viele Gramm hat ein Pfund", "was ist
  45 Minuten nach 15 Uhr" -- German units (a Pfund is 500 g; metric spoons
  and cup), German holidays (written in German or English on the page), a
  24-hour clock, decimal commas. No GLiNER (it doesn't understand German)
  or wake word second opinion in German yet. Timer names and music titles
  are compared with their accented letters ("Beyoncé"), not without.
  `commands.yaml` says what its commands are in (`language:`, new files the
  app's), and a command made in another language says its own: only the
  app's language's commands are heard and shown, the others kept, hidden,
  for when the app is in theirs again (the Home page counts them). Names
  too: a thing named in another language than the file's has that name
  under the language's code (`kitchen: {name: Kitchen, de: {name: Küche}}`),
  and the Names page names things in the app's language. And value lists:
  every home has its language's own -- `colors` (with turquoise and
  magenta), `on_off`, `open_closed`, `locked_unlocked`, `cover_classes`,
  and `hvac_modes` for a climate mode command -- in place of the library's,
  which a home kept in the language it was added in; a list of yours by the
  same name, in the app's language, takes its place, and a value's phrases
  in another language go under its code (`red: {en: [red], de: [rot]}`).
  The
  built-in speech model's "English" is now "the language's own" (`own`;
  `en` still read).
- **Dutch** (`nl`): NVIDIA's FastConformer hybrid and Piper's Ronnie, the
  whole library and every skill in Dutch ("doe het licht in de keuken
  aan", "de keukenlamp", "zet een timer van vijf minuten", "maak me wakker
  om half zeven", "hoeveel gram is een pond", "wanneer is Koningsdag"). A
  particle the model writes onto the place before it is heard as one ("op
  de begane gronduit"). On 39 sentences spoken by four voices, 123 of 156
  do what was said; typed, all 39.
- **French** (`fr`): NVIDIA's FastConformer hybrid and Piper's Siwis, the
  whole library and every skill in French ("allume la lumière dans
  l'entrée", "mets un minuteur de cinq minutes", "réveille-moi à sept
  heures et demie", "combien de grammes dans une livre", "quand tombe
  Pâques"). A name is heard with "l'" or "d'" joined to it ("l'entrée"), and
  "mets" as the model often writes it ("mais"). On 38 sentences spoken by
  four voices, 135 of 152 do what was said; typed, all 38.
- **Spanish** (`es`): NVIDIA's FastConformer hybrid and Piper's Davefx, the
  whole library and every skill in Spanish ("enciende la luz del salón",
  "baja las persianas", "pon un temporizador de media hora para la pizza",
  "despiértame a las siete y media", "¿cuántos días faltan para Navidad?").
  Numbers are heard as said before a noun ("veintiún grados", "treinta y una
  horas"; French "vingt et une minutes" too), and a count and a half after
  its unit ("dos minutos y medio", "deux heures et demie"). "Pon un
  temporizador" or "pon una alarma" alone asks how long or when, rather than
  playing music called that. On 39 sentences spoken by four voices, 144 of
  156 do what was said; typed, all 39.
- **Italian** (`it`): NVIDIA's FastConformer hybrid and Piper's Serena, the
  whole library and every skill in Italian ("accendi la luce in cucina",
  "nell'ingresso", "alza le tapparelle", "metti un timer di mezz'ora",
  "svegliami alle sette e mezza", "quanti grammi ci sono in un etto", "quando
  è Pasqua"). "Metti" plays music but also sets things: "metti un timer",
  "metti la sveglia" or "metti il volume" alone ask how long, when or how
  loud. On 39 sentences spoken by four voices, 149 of 156 do what was said;
  typed, all 39.
- **Polish** (`pl`), **Russian** (`ru`) and **Ukrainian** (`uk`): NVIDIA's
  FastConformer hybrids and Piper's Gosia, Denis and Tetiana, the whole
  library and every skill in each. Names are heard in their cases ("w
  łazience", "в гостиной", "на першому поверсі") and said back declined;
  durations with every form of their unit ("dwie minuty", "пять минут",
  "півгодини"); Easter is the Orthodox one in Russian and Ukrainian. Each
  verb that plays music but also sets things ("włącz", "включи", "увімкни")
  plays no song called "światło" or "таймер". Ukrainian's model can hear
  "ввімкни" (on) as "вимкни" (off): off counts only when it clearly wins,
  so the light stays as it was rather than going the wrong way. On their
  sentences spoken by HA Cloud voices and Piper: Polish 147 of 156,
  Russian 159 of 160, Ukrainian 103 of 126; typed, all.
- French and Dutch timers take quarter hours ("un quart d'heure", "een
  kwartier"), and a French duration after "de" is heard elided ("un minuteur
  d'une heure"). German and Dutch durations take a count and a half
  ("zweieinhalb Minuten", "tweeënhalve minuut", "vierenhalf uur" -- also as
  the model writes it said quickly, "vier half uur").
- **The speech-to-text entity hears the command from where the wake word
  check ended the wake word**, asked or streamed: on 205 test calls, a
  transcript from there began with a likely first word on 141, from where the
  command was cut for recognition on 103 -- that keeps the end of "Nabu"
  more often ("Taboo set kitchen brightness..."). A session's Recognition
  step says where it was from.

- **Each session step says how long it took**: verification ("accepted
  “okay nabu” in 59 ms"), the second opinion, the speaker, placing the
  command, recognition with TDT's or the STT entity's part ("no command, in
  897 ms (STT 806 ms)"), the action, templates, the agent, and speaking
  (Piper's synthesis; a TTS entity's start and length). A transcriber asked
  twice no longer leaves its first time out.

- **Speech-to-text can always run** (Settings, and a satellite's Input:
  Runs, "Always, as you speak"): the entity hears every command while it is
  said, streamed from just after the wake word, so every session has its
  transcript and a needed one is ready as soon as the entity answers.
  Matching is unchanged.
- **Skills have settings**, on their pages. Unit conversions: its units
  (change, take out or add one, each with its size, plural and other names),
  US or imperial volumes (the UK pint and gallon, metric cup and spoons;
  "an imperial pint" or "a US cup" is that one either way), and their
  precision. Spelling: words
  spelled your way ("colour", a name, "my last name"). Dates: its holidays
  (change, take out or add one: "December 25", "fourth Thursday of
  November", "2 days before Easter", "June 20 2027"). Clock arithmetic: a
  24-hour clock. Math: its precision. Stored as what differs from the
  built-in (`settings.yaml` `skill_settings`).
- **More holidays**: Easter, Good Friday, Thanksgiving, Mother's and
  Father's Day, Memorial Day, Labor Day, Martin Luther King Jr. Day and
  Presidents' Day, and "what day of the week is Christmas".
- **Big numbers are said in full**: "how many centimeters in a mile" is
  160,934, not 1.609e+05. Spelling says a space, hyphen or apostrophe ("O,
  apostrophe, C, L, O, C, K"), and "how is X" is spelling only with
  "spelled".

- **The audio a speech-to-text entity hears** (Settings, and a satellite's
  Input): AGC, as before, or the echo-cancelled AEC capture -- as captured,
  or boosted to a normal level. The AEC stretch is cut to match the AGC
  command, 56.5 ms earlier, from its own pre-roll.
- **A timeout says why**, in the session list and its Timed out step: the
  satellite's audio stopped arriving, the wake word was still being checked,
  no speech was heard, or speech never ended in enough silence -- with how
  much audio, speech and silence there was.
- **Music commands follow the music.** After "play jazz on the Mark 2" or
  "move the music to the kitchen", "turn it down", "pause", "next" and
  "what's playing" act there while nothing plays in the satellite's area --
  they turned down the satellite's own, idle player before -- and say where
  ("Volume at 40% on Mark II Sendspin.").
- **End of speech** on the Settings page: Silero VAD (as before) or
  microVAD, the thresholds speech starts at and falls back to silence below
  (0.5 and 0.35: between them nothing changes), the speech that starts a
  command (0.2 s), the silence that ends it (the `silence_seconds` option),
  the speech that starts that silence over (0.1 s: shorter sounds only pause
  it) and the longest command (15 s). A session's End of speech step says
  which detector ended it. Below 0.5 now counts as silence only under 0.35,
  and a sound under 0.1 s no longer restarts the silence.
- **GLiNER's encoder is 8-bit** (a 4-bit embedding; 78 MB, was 60). With
  4-bit weights it clipped what it heard: "for the shopping list, add apples
  please" added *add apples*. It now finds what full precision does, at about
  a third of a second on a Pi 5 (a quarter before). Re-export with
  `scripts/export_gliner.py`.
- **Sessions** are easier to read. The list shows what was heard, big, then
  what it ran and said. A session's page starts the same way, then lists its
  **steps**, each with an icon, its time since the session started, a line
  on how it went and whether it succeeded; open one for its details. Audio is
  folded away until opened, and "Who spoke" is in the Speaker step.
  Rejection reasons no longer end "(--full-search shows the closest
  placement)".
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
  A `groups` key is refused, saying so: take it out of `commands.yaml`, and
  the command is the Home's.
- A skill turned off for one satellite (before) no longer takes it away: a
  satellite either takes all the Home's or none.
- **Sessions** show the probability each wake word came in with -- the
  satellite model's window average that met the cutoff, its highest single
  inference, and the cutoff -- in the list and on a session's page. Needs
  new voice_labs firmware (`CALL_START` carries them).
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
  it doesn't answer a wake word then (new voice_labs firmware: `MONITOR`,
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
- The Voice Labs integration for Home Assistant (HACS): each satellite gets a
  device with its own area and a `voice_timer_list` entity for its timers.
- A command's response can differ by how it went: `done`, `failed` (Home
  Assistant refused), a timer's `no_timers`, `not_found`, `ambiguous`,
  `already` or `missing_duration`, or an `outcome` a script returns. A plain
  response is only for `done`; the `voice_labs_command` event carries the
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
  (without PyTorch, about a quarter of a second on a Pi 5). Both only run the commands
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
