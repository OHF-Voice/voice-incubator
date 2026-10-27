# Changelog

## 0.2.0

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
  `voice_call.cancel`).
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
  what is exposed (`exposed`, a new template variable). The Voice Call
  integration (0.2.0) runs them (`voice_call/intent/handle`,
  `voice_call/exposed`); without it, Home Assistant's REST API and exposure
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
  with a time, is an event on a Local Calendar named "Voice Call" that the
  app creates in Home Assistant. It outlives restarts, may be for another day
  or repeat ("every weekday at 6:45"), shows in Home Assistant's calendar, and
  rings the satellite that set it: an alarm until "stop", a reminder said
  once ("remind me at 5 pm to call my mom"). New commands: Cancel an alarm,
  Cancel a reminder (by what it is about, its time or its day), Say the
  alarms, Say the reminders. A reminder without a time still goes on
  the to-do list. Other languages keep alarms as timers.
- **Language packs**: what the app hears and says is one language's pack
  (`voice_call/languages/<code>`): its speech models, Piper voice, the
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
