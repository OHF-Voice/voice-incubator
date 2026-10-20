# adapt-ctc

> Vendored into voice_call from adapt-ctc commit `07ef328`, with additions
> listed under [Additions in voice_call](#additions-in-voice_call). Paths below
> (`examples/`, `tests/`) are `adapt_ctc/examples/` and
> `tests/adapt_ctc_tests/` here.

Adapt-style intent recognition for Home Assistant, scored directly on CTC
acoustics instead of a transcript.

Intents are declared like [Mycroft Adapt](https://github.com/MycroftAI/adapt)
intents. Each has required, optional and
[excluded](https://github.com/MycroftAI/adapt/pull/156) keywords, plus the
names (entity, area, floor, media), number and free text it needs. Every phrase is then searched
for in Parakeet's per-frame CTC probabilities. The result is a set of likely
spans with their times, plus the speech that no span accounts for. Each intent
is matched against those spans, and the output is a ranking of the intents that
match and the reasons the rest were rejected.

```
$ adapt-ctc examples/home.yaml tests/fixtures/audio/vpe_turn_all_the_lights.wav
heard: "turn all the lights."  (1.92s, 24 frames of 80 ms)

MATCHED
 1. TurnOnLights             cost   0.15  (acoustic 0.15, unexplained 0.00)
      require  'turn on'          0.56-0.88s  cost  0.15  (0.08/token)
      filler   'the'              0.96-1.04s  cost  0.00  (0.00/token)
      require  'lights'           1.04-1.36s  cost  0.00  (0.00/token)

REJECTED
   TurnOffLights
      - required [turn off | switch off] not heard; 'turn off' (0.56-0.88s,
        cost 3.96) was outscored by 'turn on' (0.56-0.88s, cost 0.15)
   ...
```

That recording is a Voice PE capture. Its greedy transcript says "turn **all**",
but at that frame `▁on` has probability 0.45 against `▁all` at 0.52. Matching the
transcript would lose the command. Scoring the phrase does not.

## Run

```bash
uv venv && uv pip install -e '.[audio,dev,tdt,embedding]'
hf download onnx-community/embeddinggemma-2-ONNX onnx/model_q4.onnx onnx/model_q4.onnx_data tokenizer.json
.venv/bin/adapt-ctc examples/home.yaml command.wav [more.wav ...] [--json] [--spans]
```

| flag | |
|---|---|
| `--model DIR` | CTC model with `model.onnx` + `tokens.txt`. The default is `$ADAPT_CTC_MODEL`, or else voice_call's int8 Parakeet TDT-CTC 110M |
| `--json` | machine-readable output: spans, unexplained words, reasons, timings |
| `--spans` | also list every place a phrase could have been said, heard or outscored |
| `--tdt-model DIR` | TDT model (`encoder.onnx`, `decoder.onnx`, `joiner.onnx`, `tokens.txt`) for intents with example sentences. The default is `$ADAPT_CTC_TDT_MODEL`, or else voice_call's int8 Parakeet TDT. Loaded only when some intent has `examples` |
| `--embedding-model DIR` | EmbeddingGemma 2 ONNX snapshot (`onnx/model_q4.onnx`, `tokenizer.json`) for examples said in other words. The default is `$ADAPT_CTC_EMBEDDING_MODEL`, or else the Hugging Face cache of onnx-community/embeddinggemma-2-ONNX. Loaded only when some intent has `examples` |
| `--threads N` | ONNX intra-op threads (4) |
| `--full-search` | score every phrase everywhere, so rejection reasons can quote the closest placement of a phrase far outside the gate (`settings.search: full`) |

WAV input of any width and channel count is accepted. Non-16 kHz input needs
the `audio` extra (soxr).

## How it works

1. **One forward pass.** Parakeet's CTC head gives log-probabilities `[T, V]`,
   one frame per 80 ms. A command is 20–45 frames.
2. **Every phrase in one graph.** Keywords, fillers, name aliases and
   spelled-out numbers go into a trie of words. Each word is expanded into a
   lattice of *every* sequence of vocabulary pieces that spells it, in any
   letter case.
   - No SentencePiece model ships with the export, and none is needed.
   - Case matters because Parakeet writes `▁T urn` at the start of a command
     (0.82, against 0.16 for `▁turn`), and it capitalizes names.
   - Names may break into words anywhere and need not start a new word after
     their first one. So *EcoBee*, *Eco-Bee*, *Ecobee*, *eco bee* and
     *e co bee* are all the same name.
3. **Mining.** A single Viterbi pass over the graph scores every phrase at
   every end frame, with a free start.
   - Scores are relative to the greedy path. A span's cost is the
     log-probability (in nats) its best alignment gives up against the model's
     own reading of those frames, so 0 means "this *is* what the model heard".
   - Punctuation pieces count as silence.
   - A placement is **heard** if it costs at most `span_gate` per piece, and
     no phrase covering mostly the same frames beats it by `contest_margin`.
     So "turn off" on "turn on" audio passes the gate, but is outscored.
   - Numbers are read longest-first: "one hundred" is not a number on its own
     inside "one hundred fifty".
   - **Live-state search** (the default). A partial phrase is dropped once its
     cost exceeds `span_gate` × (pieces so far + the most pieces any phrase
     could still need from there). Even perfect remaining pieces couldn't
     bring it under the gate, and anything that would follow from it would be
     dropped too.
     - Every placement the full search finds within the gate comes out
       identical. The tests check this exactly, and on every fixture.
     - What is lost is the "closest" placement of phrases nowhere near the
       audio; rejection reasons say "nothing close" instead.
     - Per frame the search visits only live arcs, the arcs leaving them, and
       phrase starts whose first piece the frame scores well enough.
4. **Matching.** For each intent, a weighted-interval-scheduling search picks
   the non-overlapping spans that explain the audio most cheaply, subject to
   these hard constraints:
   - every required group is heard;
   - each name type is heard as often as declared (or between `min` and
     `count` times), with distinct ids;
   - one number in range if the intent takes one (or none, if it is
     `optional`), none otherwise.
5. **Unexplained speech.** Each word of the greedy transcript that the chosen
   spans don't cover costs something:
   - `unexplained_important_cost` if some other phrase recognized it: a name,
     a number, or another intent's keyword. These are listed as *unused*.
   - `unexplained_other_cost` otherwise.
   A word's cost is spread over its pieces, so explaining "light" of "lights"
   pays for the leftover "s".

An intent's **cost** is its span costs, plus its number prior, plus its
unexplained words. Intents are ranked on that cost, so they are comparable with
each other and with the global `max_cost`. An intent is rejected, with reasons,
in any of these cases:

| rejection | |
|---|---|
| a constraint fails | the reason gives the closest placement of what was missing, or what outscored it |
| an excluded keyword is heard | only where the parse leaves the audio unexplained. A word the parse explains better is not "heard" |
| a name it cannot act on | the word right after a `name_cues` word ("the garage", "the kitchen" for an intent that takes no area) is left unexplained, and nothing recognized it or only as a name. The intent would otherwise act on everything |
| too many unexplained words | more than `max_unexplained_important` unused or `max_unexplained_other` unrecognized |
| cost above `max_cost` | "no intent matched" is when every intent is rejected |

At the same cost, the match that names more comes first: a word heard as both
a keyword and a name ("stell die Heizung auf 21 Grad", a thermostat named
Heizung) is the thing named. Matches within `ambiguity_margin` of the best
are flagged ambiguous.

## Configuration

A config is the intents plus a home's names. The examples split them:

| file | |
|---|---|
| [examples/intents/core.yaml](examples/intents/core.yaml) | 63 intents for the commands most people use |
| [examples/intents/extras.yaml](examples/intents/extras.yaml) | opt-in: two areas, "warmer"/"cooler", compound durations |
| [examples/home.yaml](examples/home.yaml) | a home: names, settings, wake word, `include: [intents/core.yaml]` |
| [examples/home-extras.yaml](examples/home-extras.yaml) | the same home with the extras |

**Intents follow verbs, not Home Assistant's intents.** "turn on", "open" and
"lock" are separate intents (`TurnOnLights`, `Open`, `Lock`), as are "is it
on" and "is it open" (`AreLightsOn`, `IsEntityOpen`), and "volume up" and
"volume up by"
(`VolumeUp`, `VolumeUpBy`). Each competes for the audio on its own verb, and
acts only on the kinds of device that verb fits. So "lock the TV" is rejected
rather than turned on, and the direction or state is the intent rather than a
slot. Mapping these to Home Assistant's intents is the caller's job.

**Intents also follow what is acted on**, so that more has to be heard. "turn
on" is `TurnOnEntity` (a named entity is required), `TurnOnLights` and
`TurnOnFans` ("lights"/"fans" required; the area is the one named or the
satellite's), or `TurnItOn`. Likewise `Dim`/`DimEntity`, `VolumeUp` (needs
"volume", "music" or "sound") against `Louder`, and the state questions:
`IsEntityOn` (a named entity), `AreLightsOn`/`AreFansOn` (the word required),
`AreCoversOpen` ("doors", "windows", "blinds", "shades") and `AreDoorsLocked`.
A verb followed by mishearings ("turn on the o", "turn around out") then
matches nothing. Before the split, the oven prompts were three of the false
accepts.

**Follow-ups** have their own intents, for the caller to resolve in context:
`TurnItOn`/`TurnItOff` ("turn (it|them) [back] (on|off)") and
`OpenIt`/`CloseIt` ("(open|close|shut) (it|them)").

**Statements are not commands.** `Open`, `Close`, `Lock` and `Unlock` exclude
"is" and "are", so "the front door is locked" is rejected rather than locking
it ("lock" fits inside "locked"). The questions need "the" between the verb
and the state, so "the lights are on in the kitchen" isn't one either.

**Core first.** Core takes one area, one unit of time ("ten minutes", "an hour
and a half") and the common phrasings. Less common commands are opt-in, so
they can't cost the common ones: compound durations, for example, let a
misheard "thirty" become "three seconds" after "one hour".

```yaml
include: [intents/core.yaml]            # merged first; this file's keys go on top
language: en
settings: {span_gate: 2.5, contest_margin: 2.0, max_cost: 10.0, search: live, ...}
filler: [the, a, please, can you]       # explains audio, means nothing
name_cues: [the, my, named, called]     # a name goes after these
numbers:
  detect: {min: 0, max: 200}            # always listened for

names:                                  # gazetteer-matcher's home format
  areas:
    living_room: {name: Living Room, aliases: [Den]}
  entities:                             # ids carry their domain
    climate.ecobee: {name: EcoBee, aliases: [Echo Bee, Thermostat]}
    lock.front_door: {name: Front Door}
  media:                                # a media library, for Play
    artist.radiohead: Radiohead

intents:
  Lock:
    require: [[lock]]                   # each inner list: one group, any alternative
    names:
      entity: {count: 1, domains: [lock]}   # only lock.* entities
  TurnOnAreas:
    require: [[turn on, turn ... on]]
    optional: [[light, lights], [and]]
    exclude: [off]
    names: {area: 2}                    # exactly two distinct areas
  Mute:
    require: [[mute]]
    names:
      entity: {count: 1, optional: true, domains: [media_player]}   # "mute", "mute the TV"
  StartTimer:
    require: [[timer], [second, seconds, minute, minutes, hour, hours]]
    optional: [[named, called]]
    wildcard: {slot: timer, before: [timer], after: [named, called]}   # {timer: "pizza"}
    number: {min: 0.5, max: 100, step: 0.5, units: [second, minute, hour]}   # {minutes: 10}
  TimerStatus:
    require: [[how long], [left]]
    number: {min: 1, max: 100, units: [minute], optional: true}  # "...my timer", "...my 5 minute timer"
  SetTemperature:
    require: [[set, temperature, degrees]]
    names:
      entity: {count: 1, optional: true, ids: [climate.ecobee]}    # a subset of the shared list
      # or: {count: 1, items: {id: {name: ..., aliases: [...]}}} to replace it
    number:
      min: 40
      max: 90
      step: 0.5
      prior:                            # first matching rule's cost is added
        - {multiple_of: 1, cost: 0}
        - {cost: 1.0}
```

**Duration units** are named in English (`second`, `minute`, `hour`) and
reported by name (`{minutes: 10}`), and said in the config's language: a
German intent's keywords are `sekunde`, `minuten`, `stunden` ...
(`numbers.UNIT_WORDS`). Some numbers are only said with their unit: "a
minute and a half", "half an hour"; "eine Minute", "eineinhalb Stunden",
"eine halbe Stunde", "zweieinhalb Minuten"; "tweeënhalve minuut", "een
kwartier"; "un quart d'heure", "deux minutes et demie"; "dos horas y
media"; "mezz'ora"; "полчаса", "półtorej minuty", "дві з половиною
хвилини". Spanish, French, Italian and Slavic numbers are also heard as
said before a noun: "veintiún grados", "vingt et une minutes", "ventun
minuti", "две минуты", "dwie minuty". Polish, Russian and Ukrainian units
have more forms than two (`UNIT_WORDS`: "minuta", "minut", "minuty",
"minutę").

**Includes.** `include` files are merged in order, then the file's own keys on
top. An intent with the same name replaces the included one. Names and
settings merge per entry, and anything else is replaced.

**Split phrases.** A required phrase written `turn ... on` is its parts in
order with anything between them: "turn the kitchen lights on", "turn it off".
The words between are explained by other spans or paid for as usual. Without
this the matcher has no word order at all.

**Names.**
- Name counts default to 0. A name heard by an intent that takes none of that
  type counts as an unused word against it, and right after a name cue ("the
  kitchen") rejects it.
- `count` is exact unless the entry also has `min` (fewest) or `optional: true`
  (`min: 0`). An intent needs at least one thing that is required: a keyword,
  a name, or a number.
- `domains: [cover, valve]` limits an intent to shared entities whose id is in
  those domains (`cover.curtains`). `ids` picks single entities.
- Name types are `entities`, `areas`, `floors` and `media`; the last is a
  closed media library for `Play`.
- A word left unexplained after a name cue rejects the intent, so the search
  prefers using even a poorly heard name to leaving it: with an optional
  entity, "turn on the EcoB" still turns on the EcoBee.

**Free text.** `wildcard: {slot, before, after, max_words}` takes 1 to
`max_words` (3) words of the transcript as the slot, as heard:
- right before a `before` keyword ("the pizza timer"), when the word before
  the name is a filler, a name cue or a unit ("a twenty minute rice timer").
  That keeps misheard words at the start of a command ("we set a timer") out
  of the name;
- or right after an `after` keyword ("a timer named chicken nuggets").

Fillers and name cues are never part of the name. Each word costs
`wildcard_cost` (0.5): less than an unexplained word, so a name is worth
taking, but more than a keyword, name or number, so "the kitchen timer" is
still the kitchen area. Matching the text to a running timer is the caller's
job, and it has to allow for spelling: Parakeet writes "lasagna" as
"Lasaga".
- `name_cues` (default `[the, my]`) also catch names the config lacks: an
  unrecognized word after a cue rejects every intent that leaves it
  unexplained, so "turn on the garage lights" doesn't turn on every light.
  Set `name_cues: []` to turn this off.
- Digits in names are spelled out ("Zone 2", "2nd Floor"). Possessives also
  match without the apostrophe.
- Homophones are different spellings and need an alias. Parakeet writes Piper's
  "ecobee" as "Echo Bee".
- A word of a name may be heard with an ending: one of the language's
  (`morphology.ENDINGS`: German has e, s, n, en, er, es, em, ns; English
  none) added to it or in place of one it ends with, so "des Wohnzimmers" is
  the Wohnzimmer, "den kleinen Ventilator" the fan named "Kleiner
  Ventilator", and "Küchen-" the Küche. `name_endings` gives a config its
  own. One lattice spells every form of a word, and where the name and its
  longer form fit equally the longer is taken, so the ending isn't left
  unexplained.
- In French and Italian, a name starting with a vowel or an h may be heard
  with an article or preposition joined to it (`morphology.ELISIONS`): the
  model writes "dans l'entrée" and "nell'ingresso" as one word, and that is
  the Entrée, the Ingresso.
- Polish, Russian and Ukrainian decline names by case, with endings as
  German's ("w kuchni", "в гостиной", "на першому поверсі"); Polish and
  Ukrainian also change the consonant before some, which
  `morphology.ALTERNATIONS` give ("w łazience" for the Łazienka, "на
  поверсі" for the поверх).
- A keyword in `compounds` may also end a compound after an area or floor
  name, as one word: German's "Küchenlicht" is the light in the kitchen
  (Küche, its linking "-n" an ending, then "licht"). The model writes the
  compound as one word, so the keyword is also listened for mid-word, and
  counts only where it starts in the word a place name ends in -- no word
  starting between them, at most `COMPOUND_GAP` (8) frames on -- and not
  where it was heard as a word of its own.
- A place the sentence ends on is said with the language's words
  (`matcher.PLACE_WORDS`, `PLACE_ARTICLES`, `BARE_PLACES`): "in the kitchen",
  "upstairs"; "in der Küche", "im Wohnzimmer", "oben". Free text to the end
  stops before it.
- Typed text is spelled in the fewest pieces, the first one starting a word
  -- or, where none starts a word with its first letter, a bare word marker
  and then continuation pieces, as the model writes them (German's "▁" "ö"
  "ff" "ne").

**Numbers.**
- Numbers come from `icu_rbnf`, since the vocabulary has no digits.
- English adds "a hundred", "one hundred and five", "seventy two and a half",
  "point five", "half", "twenty seven hundred", and "minus"/"negative".
- `optional: true` lets the intent match without a number, as well as with one
  in range.
- `detect` is for diagnostics. A number outside an intent's range is reported
  as "heard 150, outside 0-100" instead of "no number".
- `units` (English) hears numbers said only with their unit: "a minute" (1),
  "an hour and a half" (1.5), "half a minute" (0.5). The span counts as the
  unit keyword too, for required groups and `when` conditions. Units must be
  keywords of the intent.
- With `units` (from second, minute and hour) the number is a duration. Each
  number must be followed by its unit, or said with it ("an hour"). The slots
  are `hours`, `minutes` and `seconds` instead of `number`.
- `parts: 3` hears a compound duration, largest unit first: "one hour five
  minutes and thirty seconds". Each part has its own `min`/`max`/`step` check.

**Number priors.**
- `prior` rules take `multiple_of`, `values`, `min`, `max`, and `when` (the
  intent's own keywords).
- Use them to separate confusable numbers by context. For example, timers of
  15 vs 50 *seconds*, but not minutes. In a compound duration each part is
  priced with its own unit, so this applies only to the seconds of "one hour
  and fifteen seconds":

  ```yaml
  - {when: [seconds], values: [5, 10, 15, 20, 30, 45, 60, 90], cost: 0}
  - {when: [seconds], cost: 2.5}
  ```

**Example sentences.** An intent can list `examples` instead of (or as well
as) keywords. These are a fallback, tried only when no keyword intent matched,
in three steps: in the CTC evidence, in a TDT transcript, and by meaning.

```yaml
intents:
  GetWeather:
    examples:
      - What's the weather like tomorrow?
      - Is it going to rain today?
```

- **CTC evidence.** Each sentence is one phrase, searched for in the same
  model output as the keywords, but in a graph of its own. That way sentences
  never outscore keywords, and never count as recognizing a keyword intent's
  leftover words. A sentence's cost is its acoustic cost plus the words it
  leaves unexplained, priced as for intents. The cheapest sentence matches if
  it costs at most `example_max_cost` (10). It must also beat every other
  intent's sentences by `example_margin` (2). The match has spans and a cost
  like a keyword match, and no slots. It takes about 16 ms with 51 sentences.
- **TDT transcript.** Otherwise the command is transcribed by Parakeet's TDT
  transducer head. The CTC export has no transducer, so this is a second
  model, decoded with sherpa-onnx (the `tdt` extra). It costs about 30 ms per
  command on x86.
- The transcript must equal an example after normalizing both: case,
  punctuation, apostrophes and spaces are ignored, and digits are spelled out.
  So "Whats the weather like to morrow." matches the first example above.
  When the keyword pass ignored a lead-in word (the wake word's tail), the
  transcript is also tried without its first word.
- A TDT match has no cost and no slots. Reports show the example it matched
  and `TDT heard: "..."`.
- **Measured** on 200 recordings of 51 different sentences (`audio/mike-basic-*`),
  with every sentence as an example and no keyword intents:
  - 180 were matched in the CTC evidence, 3 more by TDT, and none wrong.
  - With each recording's own sentence left out, nothing matched any of the 200.
  - The best-scoring wrong sentence never cost under 22.
  - TDT alone gets only 111 right: it writes "a membrella" where the CTC head
    has "an umbrella".
  - A sentence one word short of an example ("what's the weather tomorrow"
    against "…like tomorrow") costs 17.85.
- An intent with only examples takes no keywords, names or numbers. One with
  both matches on either. An example may belong to only one intent.
- **Templates.** An example may be a template, a small part of Home
  Assistant's hassil syntax: `(a|b)` is either, `[a]` may be left out, nested
  and inside words: `(turn|switch) on the light[s]` is four sentences
  ([template.py](template.py)). Each is matched as if written out; by
  meaning, the few that say every alternative once stand for them all.
  Slots (`{area}`) and rules (`<name>`) are refused, as is a template of
  more than 512 sentences.
- **By meaning.** Last, both transcripts (TDT and CTC) are embedded with
  EmbeddingGemma 2, using the text model of onnx-community's ONNX export
  (`model_q4`, 175 MB, the `embedding` extra). Each is compared with the
  examples by cosine similarity, so the example "will I need an umbrella
  tomorrow?" is close to the TDT's "Do I need a membrella tomorrow?" (0.95).
  - **The margin decides.** Similarity alone doesn't: every request to a
    voice assistant is 0.75–0.95 similar to every other. The intent with the
    closest example matches only when it beats `embedding_margin` (0.08) over
    both the next intent and a built-in list of about 100 everyday requests
    ([data/distractors.en.txt](src/adapt_ctc/data/distractors.en.txt)).
  - **The built-in requests stand for "none of the examples".** So a config
    with a single example intent still has something to beat, and a misheard
    keyword command ("turn on the garage lights") goes to "turn on the
    lights" instead of the nearest example. Requests at least
    `embedding_distractor_cutoff` (0.85) similar to one of the config's
    examples are dropped, so they don't stand in for that intent.
  - **Cost:** about 2.5 s at startup to embed the examples and requests, and
    about 40 ms per command (two transcripts), only after the other steps
    have failed.
  - **Measured** on the 200 mike-basic recordings, with the 50 sentences
    grouped into 41 intents, each with 2–4 examples in other words than any
    recorded sentence:
    - 91 matched: 8 in the CTC evidence and 83 by meaning. One was wrong:
      "add milk to my shopping list", which both heads heard as "and go to my
      shopping list".
    - With each recording's own intent left out, 1 of 200 matched (the same
      recording).
    - With `home.yaml`'s keyword intents first and a lone `Weather` intent,
      12 of the 20 weather recordings matched and none of the other 180.
    - The margins and cutoff were chosen on these same recordings.
- Without a model (TDT, embedding) or its package there is a warning, and
  that step is skipped.

## Wake word verification

The device's wake word detector also fires on sound-alikes ("okay Natalie").
A `wake` section in the config makes adapt-ctc the second-stage check that
wyoming-wake-kaldi is in voice_call, using the same CTC model as the commands.

- **What is checked:** the pre-roll, i.e. the audio up to the detection.
- **Evidence is summed** (`scoring: sum`, the CTC forward score). Each
  phrase's probability is added up over every alignment, segmentation and
  start frame, relative to the greedy path. The wake word's spellings are
  pooled at each end frame.
- **Accepted unless** the strongest confusable's evidence beats the wake
  word's by more than `-min_margin` nats.
- **Alternatives:** `scoring: best` scores the single best path, as commands
  are scored. `max_cost_per_token` optionally adds a fit requirement.
- **Placing the wake word** (for the split) always uses the best path, since
  a sum has no single alignment.
- **Phrases are spellings.** Parakeet has no word "Nabu", so it writes
  "Naboo", "Nabu" or, about a quarter of the time, "now".
- **Confusables are real words and names**, which Parakeet can write. A
  nonsense near-homophone like "okay napu" is, to Parakeet, just another way
  to spell "Nabu".

Measured on 109 genuine calls and 7 false wakes (one speaker, a Voice PE).
To compare systems whose scores have different units, the last two rows give
the nearest call's distance from the threshold, as a fraction of the gap
between the median genuine and median false score.

| | Kaldi (voice_call) | best path, margin + fit | summed margin |
|---|---|---|---|
| genuine wakes accepted | 109/109\* | 106/109 | 108/109 |
| false wakes rejected | 7/7\* | 7/7 | 7/7 |
| nearest accepted genuine wake | 0.08 | 0.09 | 0.09 |
| nearest false wake | **0.37** | needs the fit rule | 0.06 |
| time per call, x86 | 68 ms | 17 + 0.4 ms | 18 + 6.7 ms (numpy) |

\* By construction: the genuine calls are ones Kaldi accepted, and the false
wakes ones it rejected. Kaldi's genuine-side figure is understated, because
its confusable margin is clipped at 0.

**What summing gained.** It pools evidence the model splits between
spellings. Call 10020, hedging between "Naboo" and "Nabu", went from losing
to "okay boo" by 1.26 nats to winning by 0.52. And one score now decides,
where the best path needed a second (fit) rule to catch a false wake.

**The one rejected genuine wake** (9515) loses to "okay number" by 1.6 nats.

**Not yet tested: "okay now".** It is accepted as a spelling of the wake
word, so a spoken "okay now…" would pass. Record some.

**Keep Kaldi as the verifier.** Its nearest false wake has about six times
the room of the summed margin's, and it made no errors. The two systems also
find different calls hard (rank correlation 0.01). This verifier is an
experiment; its advantages are speed and needing no second model.

**Whole calls.** `adapt-ctc --call` (and `adapt-ctc-score --calls DIR`)
recognize a whole call in two passes:
1. The pre-roll alone, for an early verdict.
2. The call from 0.5 s before the wake phrase. The wake phrase is found again
   there, and the command is recognized from the frames after it.

There is no cut in the audio. The command starts after the latest wake
placement of any spelling that fits within 2 nats of the best, so a "-bu"
heard as "ab" goes with the wake word.

On the 101 recordings above, command results match Kaldi's cuts exactly. The
only differences are the 2 genuine wakes the verifier rejected.

```bash
adapt-ctc-wake examples/home.yaml calls/*.wav --false "false positives"/*.wav
adapt-ctc examples/home.yaml --call call-9181.wav
adapt-ctc-score audio/manifest.yaml --calls ../voice_call/calls
```

## Running on a Raspberry Pi

Copy the code, the model and the recordings across:

```bash
rsync -a --exclude .venv --exclude build --exclude '*.so' ~/opt/adapt-ctc/ pi:adapt-ctc/
rsync -a ~/opt/ohf-apps-experimental/voice_call/models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m-int8/ pi:models/parakeet-ctc-int8/
rsync -a ~/opt/ohf-apps-experimental/voice_call/calls "$HOME/opt/ohf-apps-experimental/voice_call/false positives" pi:adapt-ctc-data/
```

On the Pi, build the native Viterbi and install. The C compiler and headers
are for `_viterbi`; libicu-dev is only needed if pip finds no `icu-rbnf`
wheel for the platform.

```bash
sudo apt install build-essential python3-dev python3-venv libicu-dev
cd ~/adapt-ctc && python3 -m venv .venv && .venv/bin/pip install -e '.[audio,dev]'
export ADAPT_CTC_MODEL=~/models/parakeet-ctc-int8
.venv/bin/python -c "from adapt_ctc import spotter; print('native:', spotter._viterbi is not None)"
```

Measure:

```bash
for t in 2 4; do .venv/bin/adapt-ctc-wake examples/home.yaml ~/adapt-ctc-data/calls/*.wav \
    --false ~/adapt-ctc-data/"false positives"/*.wav --threads $t | tail -4; done
.venv/bin/adapt-ctc-score audio/manifest.yaml --calls ~/adapt-ctc-data/calls --threads 4 | tail -12
.venv/bin/adapt-ctc examples/home.yaml --call ~/adapt-ctc-data/calls/call-9754.wav
```

voice_call's `scripts/benchmark.py --runs 5` times its Kaldi verifier on the
same machine for comparison.

## Scoring recordings

`adapt-ctc-score` runs a set of recordings and reports how each one fared
against what it should do. A manifest says what to expect; see
`src/adapt_ctc/score.py` for the format:

```yaml
config: ../examples/home.yaml
sentences:
  turn on the lights: {expect: TurnOnLights, slots: {}}
  turn the kitchen lights on: {expect: TurnOnLights, slots: {area: [kitchen]}}
  turn up the music: {expect: none}        # must not match
recordings:
  - {path: "close/*/*.wav", tags: [close]}     # sentence = directory name
  - {path: "far/*/*.wav", tags: [far]}
  - {path: "close/*/command-91*.wav", tags: [music]}
```

```bash
.venv/bin/adapt-ctc-score audio/manifest.yaml [--config other.yaml] [--json] [--full-search]
```

The report has four parts:
- Each recording: expected vs got, its cost, the required keywords' cost per
  token, and the transcript.
- Totals by sentence and by tag, split into correct, false accept, missed,
  wrong intent and wrong slots.
- A calibration section: the costs of correct matches against wrong ones, and
  whether any `max_cost` or `span_gate` would separate them on these
  recordings.
- A config check up front: an expected intent the config doesn't define is an
  error, so a typo can't count as misses.

`--cache DIR` keeps each recording's model output, so re-scoring with an edited
config skips the acoustic model (a minute instead of four on 2,030
recordings). `--failures` lists only what went wrong; `--only GLOB` (name or
sentence) with `--explain` prints those recordings' full recognitions.

### The office-multicap corpus

[audio/office-multicap.yaml](audio/office-multicap.yaml) scores voice_call's
multi-microphone corpus: five sessions × 204 prompts of the form "okay nabu,
<command>", each captured on 36 channels at once. The speakers are an adult
man (two sessions, `mike` and `mike-old`), an adult woman (`jenn`) and two
children (`natalie`, `oliver`). A manifest entry
can name such a corpus:

```yaml
recordings:
  - corpus: /media/cache/12tb/en-us/office-multicap/*-merged/prompts
    alignments: ../../ohf-apps-experimental/voice_call/tests/alignments
    corrections: ../../ohf-apps-experimental/voice_call/tests/corpus_corrections.csv
    channels: [pc-vpe.aec, pc-vpe.agc]
    strip: "okay nabu,"
```

- **What was said** is the prompt, or a listener's correction where the speaker
  said something else. A listener corrected 57 of the 816 prompts in four
  sessions (`mike-old` was not reviewed), and 5 prompts with an unusable window
  are dropped.
- **Where the command starts.** By default (`split: wake`) the whole prompt is
  decoded, and the command begins after the latest wake phrase placement, as
  in `--call`. voice_call's Kaldi alignments only bound that search.
  `split: alignment` cuts at Kaldi's wake word end instead. That cut is too
  late to use: on its own channel it lands a median 0.3–0.4 s after Parakeet
  places the end of "Nabu", and other devices run up to 0.2 s later still. So
  it removes the first word of the command ("turn on the TV" is heard as "the
  TV").

Where the command starts, measured with the earlier (Home Assistant-named)
intents on the VPE AGC tap and the Mark II channel the alignments were made on:

| | correct | false accept | missed | wrong intent | wrong slots |
|---|---|---|---|---|---|
| Kaldi cut (`split: alignment`) | 1546 / 2030 (76.2%) | 12 | 355 | 67 | 50 |
| wake placement (`split: wake`) | **1818 / 2030 (89.6%)** | 6 | 127 | 24 | 55 |

With the verb intents, on the Voice PE's two taps (`examples/home-extras.yaml`):

| | correct | false accept | missed | wrong intent | wrong slots |
|---|---|---|---|---|---|
| `pc-vpe.aec` | 905 / 1015 (89.2%) | 3 | 66 | 10 | 31 |
| `pc-vpe.agc` | 891 / 1015 (87.8%) | 2 | 81 | 8 | 33 |
| both | **1796 / 2030 (88.5%)** | 5 | 147 | 18 | 64 |

| speaker | correct |
|---|---|
| jenn | 402 / 408 (98.5%) |
| mike | 373 / 408 (91.4%) |
| mike-old | 337 / 408 (82.6%) |
| natalie (child) | 361 / 400 (90.3%) |
| oliver (child) | 323 / 406 (79.6%) |

On the AGC tap this is 3 recordings short of the earlier intents (891 vs
894), with 2 false accepts against 3. Requiring what is acted on costs where
it isn't heard ("are all the fans off" as "are all the off"). Splitting by verb trades a little: where
the audio says "turn off the ceiling light" or "is the kitchen light off", the
split intents report what was heard rather than what was asked.

The core intents alone (`--config examples/home.yaml --skip-missing`) get
1707 / 1970: the 5 sentences that need extras are left out, and the compound
timers core can't represent count as failures.

Timer names, on 25 synthesized commands × 3 Piper voices with names never
listed anywhere ("the oatmeal timer", "a timer named chicken nuggets"): 69 of
75. Five of the six failures are spellings ("oatmill", "Lasaga", "te"), which
matching against running timers has to allow for.

Follow-ups and state questions, on 23 synthesized commands × 3 voices
including statements that must not match ("the front door is locked", "the
garage door is open"): 66 of 69, no false accepts.

What is left:
- **Mishearings**, mostly: "timer" as "time" (now accepted for timers), "mute"
  as "meet", "unpause" as "everyone pause", "curtains" as "currents",
  "fifteen" as "fifty", "bedroom lamp" as "bedroom".
- **False accepts**:
  - "blah blah" heard as "go back", and "make it cozy" as "warmer". Both are
    at 2.3–2.5 nats per token against a `span_gate` of 2.5.
  - "turn off the lights on the stairs" as the floor "downstairs" (and, in
    `audio/manifest.yaml`, "on stairs" as "upstairs").
  - "set kitchen brightness to 150 percent" heard as "kick your prices to
    other fifty percent".

## Performance

The Viterbi pass is a C extension: limited API, no numpy headers, optional at
install time. A numpy implementation is kept as the fallback and as the parity
reference. Native and numpy give bit-identical scores in both search modes.

Measured on x86 over the 19 fixtures (1.6–3.8 s commands):

| config | graph | build | full search | live search | recognition after the acoustic pass (full → live) |
|---|---|---|---|---|---|
| `examples/home.yaml` as it was then (9 intents, 11 names) | 9.9k arcs | 58 ms | 1.8 ms | 0.9 ms | 2.7 → 1.8 ms |
| 30 areas, 300 entities × 2 aliases, timers to 180 by 0.5, °F/°C by 0.5 | 62.5k arcs | 107 ms | 10.7 ms | 3.0 ms | 12.2 → 4.4 ms |

The same commands' Parakeet forward pass takes about 25 ms here (onnxruntime, 4
threads). Without the C extension, the numpy full search takes 20 ms and 120 ms.

**Why only 2–4× from pruning.** The exact rule has to assume a phrase could
still need its most fragmented spelling, one letter per piece. So long names
and numbers stay live for a few frames after a bad start. That is about 2.8k
of 62.5k arcs per frame on the large home. Tighter rules (dropping states more
than *s* nats over the gate) visit 0.3–1% of arcs, but changed hits on 2–4 of
19 fixtures, so they are not used.

**On a Pi 5** (8 GB, Python 3.13), over 101 Voice PE commands of about 2 s:
the Parakeet pass takes a median 95 ms with 2 threads (113 ms with 4: for
commands this short, more threads cost more than they save), search 2.5 ms
and intent matching 1.2 ms. Memory is about 500 MB. Results are the same as on
x86, though int8 costs differ by up to ~2 nats on single recordings, as ARM
and x86 round the int8 maths differently.

## Tests

```bash
.venv/bin/python -m pytest
```

| test | |
|---|---|
| `test_spotter.py` | graph Viterbi vs a brute-force CTC reference over every accepted piece sequence. Native vs numpy parity in both search modes. The live search keeps everything within the gate exactly. Name word-break variants |
| `test_matcher.py` | scripted log-probs on a toy vocabulary: hedged frames, relative exclusion, aliases, two areas, id subsets, optional names and numbers, domains, free-text wildcards, includes, compound durations, out-of-range numbers, priors on 15/50, unexplained limits, the global threshold, unknown and unusable names, split phrases, numbers with units, config errors |
| `test_score.py` | manifests, globs, mic_tap corpora (prompts, alignments, corrections), and outcome classification |
| `test_end_to_end.py` | the real model on two Voice PE captures and 17 Piper commands (`tests/fixtures/make_fixtures.py`). Live and full search give the same hits and ranking on every one. Skipped if the model is absent |

## Known limits

- **Tuned on little.** `span_gate` and the unexplained costs were tuned on
  Piper audio and one speaker's Voice PE recordings, close and across a room.
  On those (`audio/manifest.yaml`), `examples/home-extras.yaml` gets 100/101
  close and 87/96 far, with 2 false accepts (the stairs, as floors). The far misses are mishearings: "timer"
  as "tower", "den" as "dead", "ecobee" as "these copy". The intents added for
  the office-multicap corpus were written against that corpus, so its scores
  are not held out.
- **No acoustic edit tolerance.** A name only matches spellings of its letters,
  so homophones need aliases.
- **Name cues are positional.** "These" or a name glued to a keyword
  ("deadlights") gets past them -- unless the keyword is in `compounds`.
- **One number per intent** (or one duration of up to three parts), **word order
  only within split phrases, no compound commands.** A compound command is
  rejected for its unused words, not split.

## Additions in voice_call

All are off unless a config uses them; upstream configs behave as before, and
`tests/adapt_ctc_tests/test_voice_call_additions.py` covers them.

| | |
|---|---|
| `description` | an intent's verb phrase ("set a timer"), for saying what a near miss was trying to do |
| `lists` | value lists: a required (or `optional`) group whose alternatives carry values, reported as slots (`{color: red}` for "crimson"). Inline (`values:`) or shared (top-level `lists:`, referred to by name) |
| `wildcard.bounded` | free text right after an `after` keyword *and* right before a `before` keyword ("add <milk> to the list"), fillers allowed between; fillers and name cues may sit inside the text but not at its edges |
| `wildcard.to_end` | free text from an `after` keyword to the last word ("remind me to <take out the trash>") |
| `wildcard.required` | the intent matches only with its free text |
| `settings.examples_first` | search the CTC evidence for example sentences on every command, and let a sentence said word for word win over keyword matches |
| `settings.embedding_accept` | `false`: the embedding never matches an intent; `Recognizer.closest_by_meaning()` names the closest by examples *and* descriptions, for hints |
| `IntentResult.missing`, `heard_groups` | what a rejected intent lacked, as data (`require`, `name`, `number`, `text`, `unexplained`, `unknown_name`, `excluded`, `cost`), and how many required groups were heard |
| `TdtTranscriber.transcribe_words()` | the TDT transcript with word times, to re-read free text over the frames it was found in |
| `name_endings` | endings a word of a name may be heard with, added or in place of its own (`morphology`): the language's by default (German's; English has none) |
| `compounds` | keywords that may end a compound after an area or floor name, written as one word ("Küchenlicht"); each must be some intent's keyword |

