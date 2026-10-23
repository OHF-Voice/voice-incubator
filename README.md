# voice_call (app)

Experimental voice app for the ESPHome `voice_call` component, packaged as a
Home Assistant app with a web UI (ingress). It connects straight to the device
over TCP (there is no Home Assistant integration for the satellites) and runs
one call at a time:

0. **wake word model push** — on connect the app lowers each `.tflite` in
   `models/wake` and sends it to the device, which has none compiled in
   (below);
1. **detection, on the satellite** — micro_wake_word runs on the ESP32 itself
   (as mixednet-executor, below), fires, and opens a call, replaying 1.5 s of
   pre-roll audio that ends at the detection. This is the only stage that runs
   on the device, and it is deliberately the generous one;
2. **verification, on the host** — the pre-roll is re-decoded with
   [wyoming-wake-kaldi](https://github.com/OHF-Voice/wyoming-wake-kaldi). A
   rejection ends the call, unless the second opinion overturns it (below). An
   acceptance also yields the **split point**: where the wake phrase ends and
   the command begins;
3. **endpointing** — a voice activity detector (`pysilero-vad`, or
   `pymicro-vad` set on the Settings page) watches the command audio for
   trailing silence, with hysteresis on its probabilities and on time.
   Silero hears the wake word first, so a command said straight after it
   isn't heard cold (it scored such commands ~0.4 against ~0.8, and they
   waited for a second try), and its first 0.2 s after the split count for
   nothing (what the wake word left in it). It hears the command raised by up
   to 16 times when the wake word was quiet (its loudest 32 ms brought to
   -30 dBFS: the call's `endpoint` step keeps it as `vad_gain`).
   Speech-to-text hears the audio as it came;
4. **recognition** — the app's own commands (made in its web UI, kept in
   `commands.yaml`) are recognized with [adapt-ctc](adapt_ctc/README.md) on
   Parakeet's CTC output, after the wake phrase as that same output places it
   (below). A command that matches nothing is answered with what it looks like
   it was trying to do: "…set the brightness, but I didn't hear the
   percentage";
5. **action** — the command's script, scene or action runs in Home Assistant
   through `execute_script`, with the slots, the satellite and the speaker as
   variables;
6. **speech** — its response template for how it went (`done`, `failed`, a
   timer operation's or a script's own outcome), rendered by Home Assistant,
   is spoken with Piper (`en_GB-jenny_dioco`); without one, the built-in
   sentence -- when done, at the command's level of verbosity (below). A
   command with nothing to say plays `sounds/acknowledge.mp3`.

Commands, names, satellites and debugging are described for users in
[DOCS.md](DOCS.md).

## Run

```bash
.venv/bin/python -m voice_call --save-dir /tmp/calls    # web UI on http://127.0.0.1:8099/
```

Training wake words in the app needs `requirements-training.txt` too, and
microWakeWord's training (`pip install --no-deps -e ~/opt/micro-wake-word`,
its working tree), in the same Python -- 3.13 at the latest: TensorFlow 2.21
has no wheels for 3.14 -- or in another named by `--wake-trainer-python`.

Commands, satellites, settings and saved calls live in `var/` (`--data-dir`;
`/data` as an app). The web UI listens on localhost unless `--http-host` says
otherwise: it can run actions in Home Assistant with an admin token. As an app
(`SUPERVISOR_TOKEN` set) it talks to Home Assistant through the Supervisor,
answers only the ingress proxy, and reads `/data/options.json`.

For work on the UI without a satellite or a real Home Assistant,
`scripts/dev_ui.sh` runs the app against `tests/fake_hass.py`;
`scripts/dev_ui.sh --replay calls/call-15788.wav ... --` adds a fake satellite
(`tests/fake_satellite.py`) that places those recorded calls in a loop.

With no `--host` it browses `_voice-call._tcp` and serves **every** satellite it
finds, connecting as they appear and dropping them as they go. `--host` pins it
to one device instead, for a different subnet or to test one satellite while
others are on the network.

One set of models serves all of them. That is the point: the models are the
expensive part (~450 MB resident), so a second satellite costs a socket and a
buffer rather than another copy. Recognition is serialized by a shared lock --
two satellites waking at once is rare, and the second waits 0.5-1.5 s on a Pi
rather than racing a half-swapped grammar.

Saved audio is split per device, keyed on the name and the MAC tail from the
device's `HELLO`:

```
/tmp/calls/voice-call-vpe-090087/call-23003.wav
/tmp/calls/voice-call-vpe-09aabb/call-23007.wav
```

Several VPEs flashed from one YAML all report the same name (the config sets
`name_add_mac_suffix: false`), so the MAC is what actually separates them. It is
also what sessions are keyed on, taken from the mDNS TXT record so identity is
known before connecting.

The device accepts **one client at a time** and refuses the rest rather than
replacing them, which stops two apps from kicking each other off in a reconnect
loop. If a connection keeps dropping within 30 s, the app backs off
exponentially and says so -- that is usually a second app holding the device,
not the network.

Useful flags: `--host`, `--hass-url`, `--hass-token-file`, `--data-dir`,
`--http-host`, `--http-port`, `--silence`, `--max-command`, `--wake-model`,
`--wake-cutoff`, `--no-tdt`, `--embedding-model`, `--debug`, and for speaker
identification `--speaker-threshold`, `--speaker-channel`,
`--no-speaker-learn`, `--enroll-commands`, `--no-speaker-id`.

## Commands

The app no longer reads Home Assistant's sentence triggers. Its commands are
its own, in `commands.yaml`, which the web UI writes round-trip (comments and
order survive) and which may be edited by hand: a change on disk is picked up
within two seconds. A save from the UI is validated first -- by the command
schema (`voice_call/commands.py`) and by adapt-ctc's own config checks -- so a
broken command is refused rather than saved, and a file broken by hand keeps
the previous commands in use and says why in the UI.

A command is one adapt-ctc intent (examples, keywords, names, a number, value
lists, free text) plus where it is available and what it does. All of them
compile into one graph: rebuilding after an edit takes 50-150 ms, plus the
first embedding of any new example or description when EmbeddingGemma is
loaded. Scope is applied after recognition, best match first, so a command
unavailable on this satellite is passed over (and named, if nothing else
matched) rather than changing what the others compete with.

Home Assistant's registries are watched only to offer suggestions -- names,
aliases, areas -- and to know each satellite's floor. Nothing is taken from
them into the vocabulary unless someone adds it.

**Combinations, not optional slots.** What a command hears -- names, a
number, value lists, free text -- is never optional. `combinations` lists the
sets of slots that may be said together, each required whole
(`[[], [area], [floor]]`: nothing named, an area, or a floor; without it, all
of them together). Each combination compiles to an adapt-ctc intent of its
own, `lights_off`, `lights_off:area`, `lights_off:floor`
(`commands.command_of` maps them back), and only the first carries the
examples and the description, so the embedding ranks commands, not
combinations. With optional slots, a fallback that picked `lights_off` for
"kill the lights in the office" ran it with the office left out -- in the
satellite's own area; now nothing runs unless what was heard is exactly one
combination. The library went from 56 intents to 92 (lights,
covers, brightness and the thermostat by area or floor; timers by name,
free-text name or length).

**A device and a room.** The library's device, cover, lock, brightness and
thermostat commands have an `[entity, area]` combination, as Home
Assistant's intents have `name_area`. Names may repeat across devices, so
the engine (`Engine.fit_devices`) takes every device with the name heard and
keeps those in the room said, by Home Assistant's registry (an entity's
area, else its device's; `Engine.entity_area`). None there refuses the
command with a hint ("but there's no Fan in the Kitchen", or "I don't know
which room Kettle is in"), and nothing else is tried. Without a room, a name
several devices share is the one in the satellite's area. The room is not
part of the target: `target` holds only the devices when there are any.

**How much a command says** when it is done (`verbosity`,
`commands.VERBOSITY`): `beep`, `brief` (a fixed sentence, so a TTS cache keeps
it), `specific` (what was heard) or `verbose` (what it did, in full). The done
response may be written for each level (`response: {done: {brief: …,
specific: …}}`); a level without one says the closest written, less before
more (`commands.closest_text`), then the built-in sentences for that level
(`timers.Plan.levels`, `music.control_said`/`found_said`). Without a
`verbosity`, music controls beep and the rest say their response, as they
did before levels -- an action with none still beeps. Other outcomes always
say theirs; a question (only a response, a timer's or music's status) never
beeps.

A level is a property of the command because the same number means
different things for different commands: a beep confirms a light you can see
come on, not a timer you can't. What a global setting would get wrong is
"can you see it": `actions.place` works out where the command acted -- the
areas, floors and devices it names (a device's area from the registry) or
else the satellite's area -- and a beep is said only when that is the
satellite's own area. Elsewhere it says `beep_elsewhere` (settings.yaml,
"Responses" on the Settings page; `specific` unless set: "Turned on the
lights in the Office" says where, which "Turned on the lights" can't).
Home Assistant's default does the same with the brief sentence. Templates see
`place` too (`said`: "in the Kitchen", "upstairs"; `here`; `areas`), and a
rendered response is tidied of the gap an empty variable leaves. A copy of the
command on a satellite can say more or less there.

**The Home's and a satellite's own.** A command is the Home's, or one
satellite's (`satellite: <key>` in `commands.yaml`); a satellite takes the
Home's unless `home: false` in `satellites.yaml` (`Satellite.available`).
Skills the same way: the Home's (`settings.yaml` `skills`, each on unless
false) and a satellite's own (`satellites.yaml` `skills: {id: true}`). There
are no command groups or per-area overrides any more (either key is refused,
saying what to do instead). A command that should do something else on one satellite is a
copy of it there (Duplicate, on its page), and on that satellite its own goes
first. The copy says the same example sentences, which adapt-ctc gives to one
intent only: `commands.shared_examples` leaves each sentence to the first
command that has it and records the later ones as its twins
(`Compiled.twins`), and `Engine._take` runs a twin where the first is not
available, or where the twin is the satellite's own. Identical keywords both
match at the same cost, and `Engine._take` takes the satellite's own first;
so do meaning and the model step among a command's versions and copies
(`Satellite.first`).

## Layout

| path | |
|---|---|
| `voice_call/protocol.py` | the device wire format |
| `voice_call/mixednet.py` | lowers a wake word `.tflite` into the device's blob |
| `voice_call/_mixednet_compiler.py` | vendored from mixednet-executor; edit upstream |
| `voice_call/wake.py` | Kaldi wake verification and split point |
| `voice_call/wake_words.py` | the wake words a satellite can listen for: built in and verified, or custom; their cutoffs and placement phrases |
| `voice_call/wake_training.py` | training a new wake word from a typed phrase: the kit, the job, the saved model; `wake_positives.py` has Piper say it (rooms, noise, the frontend's features), with speakers' recordings of it, `wake_trainer.py` trains, temperature-scales and exports it (TensorFlow, microWakeWord) |
| `voice_call/wake_recordings.py` | a speaker saying a wake phrase at a satellite: takes cut by Silero, kept when they sound like them, stored with their voice |
| `voice_call/endpoint.py` | endpointing: Silero VAD or microVAD, with hysteresis |
| `adapt_ctc/` | vendored from adapt-ctc (commit 07ef328) plus value lists, bounded free text, examples first, descriptions and structured rejections; its own README and tests (`tests/adapt_ctc_tests/`) |
| `voice_call/engine.py` | where the command starts, recognition, where a command is available, free text from TDT, hints |
| `voice_call/commands.py` | the command schema: combinations, presets, where it is, what happens |
| `voice_call/store.py` | `commands.yaml`, `satellites.yaml`, `settings.yaml` |
| `voice_call/actions.py` | variables, `execute_script`, intents (on what was heard, as Assist would: exposed devices only), the response, the `voice_call_command` event |
| `voice_call/calls.py` | per-call traces, saved audio, debug sessions |
| `voice_call/web.py`, `voice_call/web/` | the web UI and its API |
| `voice_call/languages/` | language packs, one per language (`en/`, `de/`, `nl/`, `fr/`, `es/`, `it/`, `pl/`, `ru/`, `uk/`): speech models, the matcher's base config, skills; `speech.py`, every sentence the app makes itself, and `reading.py`, what it reads past the matcher (an alarm's time, music cues, a place after free text) -- English's names are what every language's has; and `library/`, the example commands offered in the UI, most in versions (`variants`: here, device, place) |
| `voice_call/web/icons.js` | the Material Design Icons the UI shows (Apache-2.0), from `scripts/make_icons.py` |
| `voice_call/hass.py` | Home Assistant over one websocket: registries, scripts, templates, events |
| `voice_call/tts.py` | Piper, and the acknowledgement fallback |
| `voice_call/server.py` | the call state machine |
| `voice_call/speaker.py` | speaker ID, profiles, enrollment; `python -m voice_call.speaker` |
| `voice_call/integration.py` | the Voice Call integration from the app's side: set-up, satellites, following timers |
| `voice_call/alarms.py` | alarms and timed reminders on the "Voice Call" Local Calendar in Home Assistant: made if missing, followed, rung on the satellite that set them (in a language with a `calendar.py`, English for now) |
| `voice_call/music.py` | music commands: which Music Assistant player, reading "play …", searching, controls |
| `voice_call/voices.py` | Home Assistant STT and TTS entities in place of TDT and Piper |
| `voice_call/fallback.py` | when nothing matched: the closest command by meaning, then GLiNER (`voice_call/gliner.py`) -- each only for the commands chosen for it |
| `voice_call/skills/` | skills: questions answered without an LLM, one module each (spelling, dates, clock, units, arithmetic, live) -- their settings and what they compute -- on or off for the home or a satellite; how each is asked and answered is the language's (`voice_call/languages/<code>/skills/`, with `numbers` and `live`) |
| `voice_call/agent.py` | what nothing here understood: a Home Assistant conversation agent, a conversation kept per satellite |
| `scripts/bench_language.py` | a language pack's library on its `bench.yaml` sentences: typed, or spoken by its Piper voice and Home Assistant TTS voices |
| `scripts/bench_questions.py` | the smart-home quick-questions benchmark's skill answers through the app, spoken or as text |
| `scripts/bench_fallback.py`, `tests/fallback_utterances.yaml` | the fallback chain on sentences the keywords miss, as text (any machine) or spoken |
| `voice_call/timers.py` | timer commands: which timer is meant, the service call, what to say |
| `custom_components/voice_call/` | the Home Assistant integration (HACS): satellite devices, `voice_timer_list` (timer_list, renamed), `voice_call.timer_*` triggers |
| `hass_tests/` | the integration's tests, run with a Home Assistant core checkout's fixtures (see its conftest) |
| `models/wake/stop.tflite` | microWakeWord's "stop", pushed to a satellite while a timer rings |
| `tests/fake_hass.py`, `tests/fake_satellite.py` | stand-ins for Home Assistant and a device, for `tests/test_app.py` and UI work |
| `config.yaml`, `Dockerfile`, `rootfs/`, `script/stage_build` | the Home Assistant app; `stage_build` copies the models and wyoming-wake-kaldi into `local/` for the image |
| `wake.yaml` | wake phrases, confusables, decision thresholds |
| `scripts/check_mixednet.py` | proves the pushed blob decodes like a built-in model |
| `scripts/quantize_tdt.py` | dynamic int8 for either Parakeet encoder |
| `voice_call/wake_ratio.py` | the second opinion on a rejected wake word |
| `scripts/align_sessions.py` | where the wake word is in every prompt of a session |
| `scripts/false_accepts.py` | how often the chain fires on audio with no wake word in it |
| `scripts/synth_wake_words.py` | wake words and near misses spoken by every Piper English voice, in rooms and noise |
| `scripts/bench_wake_words.py` | a wake word through both stages on labeled clips, at several cutoffs; sweeps thresholds afterwards |
| `scripts/check_wake_words.py` | whether custom wake words lower for the device, match TFLite and hear their own phrase |
| `scripts/make_wake_training_kit.py` | the kit wake words are trained with: 5,000 random spectrograms of each negative set |
| `tests/alignments/` | wake word spans and word boundaries, one JSONL per session |
| `tests/corpus_corrections.csv` | what was actually said, where it differs from the prompt list |
| `corpus/positives/` | 32 VPE captures of in-vocabulary commands, for threshold work |
| `tests/commands.txt` | the spoken test corpus, one utterance per line |
| `tests/corpus_expected.yaml` | the command and slots each prompt must come back as, or that it must be refused |
| `tests/corpus_expected.tsv` | the sentence each prompt was meant to be, for the take audits (`scripts/audit_*.py`) |
| `scripts/bench_corpus.py` | runs the whole chain over a mic_tap corpus |
| `scripts/report_corpus.py` | per channel, per prompt, per failure |
| `models/` | everything needed to run offline (~770 MB) |
| `models/speaker/` | `redimnet_b2_vox2.onnx` from `OpenVoiceOS/redimnet-b2-vox2-onnx` |
| `models/parakeet-tdt-v3-int8/` | optional: the multilingual TDT model, `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` from the sherpa-onnx releases |
| `var/speakers.json` | speaker profiles and LED colors |

## Multi-microphone corpus

`tests/commands.txt` was recorded through
[mic_tap](https://github.com/synesthesiam/mic_tap), which captures one spoken
prompt on every microphone in the room at once. Because the words, the speaker
and the moment are identical across the files of one prompt, the only variable
left is the capture path -- which makes the corpus a direct comparison of
devices and channels rather than of recordings.

`scripts/bench_corpus.py` runs each file through what the satellite and the app
actually do: the micro_wake_word frontend, mixednet-executor and
`determine_detected`; a 1.5 s pre-roll into Kaldi wake verification for the
verdict and the split point (with the tail retry and the second opinion); then
the app's engine, from the call decoded half a second before the wake word to
the command recognized after the wake phrase. The commands are a
`commands.yaml` or an adapt-ctc config (`--commands`; by default adapt-ctc's
`home-extras.yaml`), and `tests/corpus_expected.yaml` says which command and
slots each prompt must come back as, or that it must be refused. An expected
command the commands lack is an error before any audio is read, because a
bench run on a mismatched pair measures the files. Rows carry adapt-ctc's
verdicts (correct, missed, wrong intent, wrong slots, false accept), the CTC
cut beside Kaldi's, and both transcripts.

```bash
scripts/bench_corpus.py ~/opt/mic_tap/sessions/mike-merged --workers 8 \
    --wake-binary /path/to/run_okay_nabu
scripts/report_corpus.py tests/bench_corpus.jsonl
```

The wake binary is mixednet-executor's own host harness, built from the same
lowering the device runs:

```bash
cd ~/opt/mixednet-executor
python3 compile_mixednet.py ~/…/models/wake/okay_nabu.tflite \
    -o model_okay_nabu.cpp -n OKAY_NABU_MODEL
g++ -O2 -std=gnu++17 -I. -DMODEL_SYMBOL=OKAY_NABU_MODEL -o run_okay_nabu \
    mixednet.cpp model_okay_nabu.cpp host_main.cpp
```

**Start the detector warm.** `MIN_SLICES_BEFORE_DETECTION` makes a freshly
constructed detector ignore its first 100 slices -- 3 s -- and on a device that
elapsed hours before anyone spoke. Constructing one per clip instead suppresses
every wake word uttered in the first three seconds, which here was about half of
them. The bench starts `ignore` at zero; the cool-off still applies after a
detection.

**Level-match before comparing microphones.** Captures in one room span 70 dB
RMS, from an XMOS AGC tap at -21 dBFS to the AEC tap on the same device at -76.
Compared as captured, the ranking is mostly a ranking of gain staging. Every
file is therefore run twice: as captured, and with a single per-channel scalar
to a common -26 dBFS -- a fixed input gain, not per-utterance AGC. The VPE's AEC
tap goes from 31% wake detections to 74% under that scalar alone.

**Gate on verification when ranking.** The report's `chain` column scores
recognition on its own; `app` scores what the app would do, where a call the
wake verifier rejects is hung up before the command is ever decoded. One capture
here detects the wake word more often than any other and is last by `app`,
because the verifier refuses 87% of those detections.

## Two stages, and which one to make generous

Detection runs on the satellite and verification runs here, and the split is
the design rather than an accident of where the code fits. The device's model
is 46 kB and has to decide in a few hundred microseconds; the verifier has a
full acoustic model, a lexicon and as long as a call can afford. So **stage one
should be generous and stage two strict**, and tuning stage one for precision
duplicates stage two's job while costing recall that stage two can never get
back.

Measured on 1.49 hours per channel of speech and music with no wake word in it:

| channel | cutoff | stage 1 fires/h | reaches the command/h |
|---|---|---|---|
| every VPE tap | 0.85 → 0.20 | 0.00 | 0.00 |
| `mark2-sj201.ch1` | 0.50 | 1.34 | **0.00** |
| `mark2-sj201.ch1` | 0.30 | 2.01 | **0.00** |
| `mark2-sj201.ch1` | 0.20 | 5.37 | **0.00** |
| `pc-brio.mic` | 0.20 | 1.34 | **0.00** |

The second stage caught every one, including with the second opinion enabled.
That is the design working: a first stage that fires five times an hour on
television costs nothing as long as the second stage is doing its job, and it
buys back a great deal of recall.

### The probability cutoff

The stock 0.85 assumes a voice at the device. Across a room it costs most of
the recall, and the false accepts it buys are ones the verifier would have
refused anyway:

| cutoff | mike | jenn | natalie | **oliver** |
|---|---|---|---|---|
| 0.50 | 83.0% | 88.4% | 51.8% | **27.6%** |
| 0.40 | 85.9% | — | 55.9% | 31.9% |
| 0.30 | — | — | 60.3% | 36.6% |
| 0.20 | — | — | 66.7% | **42.8%** |
| 0.15 | — | — | 69.8% | 47.4% |

(commands reaching recognition, VPE AGC channels, both verifier fixes in)

**0.2 to 0.3 is the useful range**, and the lower end is worth it for the
speakers the detector struggles with: it is most of a doubling for a child, and
verification holds at 90-93% all the way down, so the extra detections are real
wake words rather than marginal noise. Below 0.15 the stage-1 rate on a loud
microphone climbs faster than the recall does.

## When the prompt list is wrong

A prompt list says what the speaker was *asked* to say. On a corpus recorded
from a script it is not what they said: a listener went through four sessions
and corrected **57 of 816** prompts, with 5 more unusable because the recorded
window was wrong. For one child it was 40 of 204 — one prompt in five.

That is not a detail, because the error is silent in both directions. Three
corrections flip between two *trained* sentences — `turn on the lights` where
`turn off the lights` was said, 14 percent where 40 was — so a correct
recognition was being scored as a failure. The other 54 put the recording
outside the grammar, and a constrained decoder always returns a trained
sentence, so a forced match was being scored as a success.

`tests/corpus_corrections.csv` is `session,index,verdict,corrected`, and
`bench_corpus.py --corrections` applies it: `fix` re-labels the prompt and
re-derives whether it is still in vocabulary, `segment` drops it (a
mis-segmented window has nothing in it to score), `ok` changes nothing.

| speaker | end-to-end, as labelled | corrected |
|---|---|---|
| jenn | 71.1% | 71.2% |
| mike | 70.5% | 69.6% |
| natalie | 32.5% | **37.2%** |
| oliver | 31.7% | **43.2%** |

The children gain most, because the pipeline was being blamed for them saying
something else. What it costs is the out-of-vocabulary numbers: 647 of those
recordings now get a forced match. 83% of them land on a sentence meaning the
same thing — `set the kitchen brightness` for `set kitchen brightness` — which
is arguably right. The other 17% change the meaning, and 17 of those are
`turn down the volume` answered with `turn the volume up`.

**Collect this while recording, not afterwards.** The `session` task in
[intent-examples](https://github.com/synesthesiam/intent-examples) produces
exactly this file, and the next session gets it for free.

### Where the wake word is

`scripts/align_sessions.py` writes a span per prompt into `tests/alignments/`,
with per-word boundaries:

```json
{"prompt": 1, "wake": {"start": 29759, "end": 37440},
 "words": [{"word": "okay", "start": 29759, "end": 35040},
           {"word": "nabu", "start": 35040, "end": 37440}],
 "lr": 0.5672, "accepted": false, "rejected_by": "confusable"}
```

It slides the Kaldi verifier across the recording and keeps the position it
scores best, which is **not** the on-device detector — localising with the
model you are trying to fix only finds the utterances it already handles. On a
child's session that is the difference between 33% and 100%: all 204 of his
prompts have a span.

1,019 spans across five sessions, every one inside its recording; 829 also pass
the verifier's thresholds. The span is what lets a clip be cut or a review
screen open in the right place — an earlier pass over the same audio computed
these alignments and kept only summary statistics, which is why it had to be
done twice.

## Wake word models

No model is compiled into the firmware. The device runs
[mixednet-executor](https://github.com/synesthesiam/mixednet-executor) -- the
microWakeWord streaming graph without a TFLite interpreter -- and the app pushes
it a model over the same socket right after `HELLO`. Changing the wake word is
a pick in the UI (see "Wake words" below), not a reflash.

The device cannot read a `.tflite`: lowering the graph means walking flatbuffers
and proving every convolution emits exactly one frame, which is the work
mixednet-executor exists to do ahead of time. So the app runs that lowering and
sends the result -- a flat op table plus weights, with pointers written as
offsets into a trailing data section:

```
MNB1 | header | buffers[] | ops[] | shifts[] | sigmoid LUT | weights/bias/mult/shift
```

The ops are mixednet-executor's: ring push, dense, depthwise, copy, and max
pool -- the last for a word trained here, whose last layer is max-pooled
(below). Firmware whose executor has it says `max_pool` in `HELLO`; older
firmware refuses such a model, so the app doesn't send it, and the Wake words
page says the satellite needs newer firmware.

The device copies the small tables out and points the op weights straight into
the received buffer, so the ~45 kB that matter are never duplicated. Detection
settings travel with it as JSON, read from the model's microWakeWord manifest:

| field | |
|---|---|
| `probability_cutoff` | per-slice threshold, 0.0--1.0; per wake word (below); `--wake-cutoff` overrides the verified ones' defaults, the Wake words page any |
| `sliding_window_size` | slices averaged before a detection counts |
| `feature_step_size` | must match the device's frontend, or the model is refused |
| `kind` | `vad` makes the model a gate on the others rather than a wake word |

| model | blob | on-device |
|---|---|---|
| `okay_nabu` | 46,880 B | 27 ops, 24 buffers, 4,745 B arena, 36,480 MAC/invoke |
| `vad` | 18,456 B | 12 ops, 12 buffers, 1,673 B arena, 13,760 MAC/invoke |

Both load in about 30 ms. `scripts/check_mixednet.py` builds the upstream
harness and the device's loader and diffs them frame by frame; both models are
bit-identical over 1,099 inference steps, including 3,000 frames of adversarial
random features.

The stock cutoff of 0.85 assumes a voice at the device. Across a room from a
loudspeaker this bench sees peaks around 0.5--0.9 depending on volume, which is
what `--wake-cutoff 0.5` is for.

## Wake words

Each satellite listens for one wake word: its own (Wake words page,
`satellites.yaml`: `wake_words: [id]`), else the home's preferred one
(`settings.yaml`), else `--wake-model` (default `okay_nabu`). Only the first
of a list is used (`MAX_WAKE_WORDS`), and the API refuses two: the device has
room for more, but the UI offers one for now. The app pushes the wake word's
model on `HELLO`, clearing whatever the device held first, and again when the
pick or its cutoff changes -- never during a call, since clearing stops the
detector's microphone. The firmware has six model slots and says so in
`HELLO` (`model_slots`): "stop" while a timer rings, and up to five wake
words. Older firmware has four, so three. No VAD gate is pushed (below).

The device reports the model's id as the wake word, which decides which
verifier, second opinion and placement phrases apply (`voice_call/wake_words.py`).

| | built in, verified | custom |
|---|---|---|
| where | `models/wake`, entry in `wake.yaml` | `wake_words/` (as an app: `/config/wake_words`), or uploaded in the UI |
| first stage | low cutoff, per word | the manifest's cutoff |
| Kaldi verifier | yes, with confusables | none, unless given confusables on the Wake words page |
| second opinion | yes, per-word threshold | with a verifier, at the default threshold |
| where the command starts | wake phrase found in the CTC evidence | the same, from the manifest's `wake_word` text |

Each verified wake word in use costs its Kaldi graphs and two resident
decoder processes, about 200 MB, so they start when a satellite first listens
for the word, not at startup. Graphs are built in `<data>/wake-train`.

### Tuning on the Wake words page

`wake_words.yaml` in the data directory holds what the page changed, per
wake word, only where it differs from the default:

```yaml
okay_nabu:
  cutoff: 0.3                 # the detector's, on the device
  confusables: [...]          # replaces wake.yaml's list
  pronunciations: {glorbo: ɡ l ˈɔ ɹ b oʊ}   # over wake.yaml's
  thresholds: {theta_competitor: 0.15}      # over wake.yaml's
  second_opinion: 1.1         # wake_ratio threshold
hey_tater:                    # custom: verified once it has confusables
  confusables: [hey potato]
```

`WakeVerifier.build` merges it over `wake.yaml` and loads the result with
wyoming-wake-kaldi's own validation, so a word with no pronunciation refuses
the save and changes nothing. A custom word gets an entry under its id alone
(its name could clash with a built-in one's), its manifest's phrase, and the
first Kaldi model. On a save, `WakeVerifier.apply` rebuilds the graphs and
restarts the decoders of each running word whose entry changed -- about 2 s
on a desktop -- while the old ones answer, then stops them; the catalog is
rescanned (verified, cutoffs), the second opinions get their thresholds, and
satellites whose cutoff changed get their models again. A cutoff set here
wins over `--wake-cutoff`, and a second opinion over `--wake-ratio`. A custom
word keeps its manifest's cutoff when it gets a verifier: lowering it is the
page's to do.

Words the verifier's dictionary lacks need a pronunciation in its phones (IPA,
the stress mark on the vowel). The page offers espeak-ng's, from Piper's
bundled copy, mapped onto those phones (`voice_call/pronounce.py`): on 393
common words from the dictionary it gives the dictionary's own pronunciation
76% of the time, and the same phones with other stress 3% (the rest differ
mostly in unstressed vowels). It is only offered: what is saved is what the
page shows, so nothing is guessed at run time.

### "Hey Jarvis" and "Hey Mycroft"

The models are microWakeWord's own (v2, as ESPHome ships them). Their
`wake.yaml` entries were built the way "okay nabu"'s was: neighbours of the
last word as confusables, then measured and pruned.

- **"Hey Mycroft"** has real recordings: the Mycroft community data
  (`/media/cache/12tb/en-us/hey-mycroft`), clips Precise fired on and people
  labeled -- 66k wake words and 40k false wakes, 1,500 of each sampled. These
  were very likely in the detector's training data, so its numbers on them are
  flattering; the verifier's are not.
- **"Hey Jarvis"** has none, so 1,000 wake words and 1,200 near misses (60
  phrases) were synthesized with every Piper English voice and speaker (598),
  at varied speed, half in a simulated room and most under noise
  (`scripts/synth_wake_words.py`), plus 500 held out (20 phrases, half of
  them never used for tuning).

Everything goes through the chain as the app runs it, at several cutoffs:
detector, 1.5 s window, Kaldi with the tail retry, second opinion
(`scripts/bench_wake_words.py`, which keeps every score so the thresholds can
be swept afterwards).

"Hey Mycroft" first lost 42% of real wake words to the verifier, nearly all
on confusables: "micro", "my crop", "my cough", "mike", "macro" and "my craft"
were each the closest rival of three or more times as many wake words as false
wakes. Dropping those twelve and adding the two ways "mycroft" is mostly said
("my-craft", and "my-croft" with the vowel of "cot") as pronunciations, then
loosening its prefix test and confusable margin (`theta_prefix` 1.2,
`theta_competitor` 0.15) and its second opinion (1.2):

| hey_mycroft, cutoff 0.7 | wake words | false wakes |
|---|---|---|
| detector | 99.5% | 14.9% |
| first confusable list | 58.9% | 2.1% |
| pruned, pronunciations | 85.7% | 2.9% |
| + thresholds | **88.7%** | **3.0%** |
| synthetic: 500 wake words / 1,000 near misses | 92.0% | 44.0% |

The synthetic near misses say the pruning has a price: "hey microphone", "hey
my cross", "hey minecraft", "hey micro" get through nearly every time the
detector fires on them (and did at the stricter settings too: 34% at the
defaults with no second opinion). The recorded false wakes -- what actually
sets the detector off in a home -- are what the list was tuned on.

"Hey Jarvis" kept 97% of the synthetic wake words it detected from the start.
Its leaks were phrases not on the list -- "janice", "jealous", "drivers",
"darkness", "jockey", "jar", "jarred", "guys" -- which were added, and its
second opinion is tighter (1.0: the same recall, fewer near misses through):

| hey_jarvis, cutoff 0.5 | wake words | near misses (tuning) | near misses (held out) |
|---|---|---|---|
| detector | 91.0% | 37.9% | 64.8% |
| verified | **88.3%** | **6.5%** | **10.1%** |

Phrases that contain "jarvis" ("hi jarvis", "okay jarvis", "a jarvis") are
accepted, and are not counted as near misses. The ones that still get through
most are "hey chavez" (14 of 18 detected), "hey jervis", and the made-up
"hey charvis".

**Cutoffs.** The detector's own false firings on 12 hours of speech and music
at the Voice PE (`scripts/false_accepts.py --wake-word ...`), per hour:

| cutoff | 0.2 | 0.35 | 0.5 | 0.7 | 0.95 |
|---|---|---|---|---|---|
| okay_nabu | 0.25 | | | | |
| hey_jarvis | 2.6 | 1.5 | **0.8** | 0.2 | 0 |
| hey_mycroft | 16.7 | 9.4 | 5.1 | **2.7** | 0.9 |

The verifier accepted none of them at any cutoff, but each firing lights the
ring and ducks the music until it is refused, so the cutoffs are 0.2, 0.5 and
0.7. "Hey Mycroft" is the trigger-happy one: at 0.7 it still finds 95% of the
synthetic wake words against 90% at 0.9.

### Custom wake words

A custom wake word is a microWakeWord manifest and model, as ESPHome and the
[Tater Wake Words](https://github.com/TaterTotterson/Tater-Wake-Words) catalog
ship them (Tater's own `.json` and its `.esphome.json` twin both work; the
openWakeWord files beside them are ignored). Its id is the model's file name.
It cannot shadow a built-in word, "stop" or the VAD, and of two with the same
id in a nested directory the first is used (with a warning). A cutoff of 1.0
is run as 0.99: the device fires when the window's average is *above* the
cutoff, so 1.0 would never fire.

`scripts/check_wake_words.py` ran every model in the Tater catalog -- 851
models, 794 distinct phrases, versions 1 to 7 -- through three checks:

| check | result |
|---|---|
| lowered for the device by the app | 851 of 851 |
| upstream executor (what the firmware compiles) against TFLite's reference kernels, on speech | 848 identical; `yuki` off by 1, `igad` by 11 and `okay_hal` by 31 at some step (the executor rounds as esp-nn does; all three still detect) |
| its own phrase, 6 synthetic clips at the manifest's cutoff | 775 hear all 6, 68 hear 5, 7 hear 4; V6 `hey_athena` heard none at its manifest's 1.0, all 6 at 0.99 |

The V7 models are three times the size of the older ones (94,772 B against
33,876 B). A Voice PE took two of them, a V1 model and "okay nabu" at once.
The manifests' cutoffs run from 0.02 to 1.0; 415 are 0.97.

### Training a wake word

On the Wake words page, a typed phrase becomes a custom word
(`voice_call/wake_training.py`): Piper's `en_US-libritts_r-medium` (2,306
speakers) says it 2,000 ways, each in a room with noise behind it, joined by
the recordings of whichever speakers are ticked (Speakers page, above), and a
model learns it against the kit's negatives (`scripts/make_wake_training_kit.py`:
5,000 random spectrograms of each set). A word trained here can be trained
again, with more recordings say: the new model replaces it when done, its
tuned cutoff goes (its verifier stays), and satellites listening for it get it.

The recipe is micro-wake-word's `notebooks/POOLING_AND_PERSONALIZATION.md`:
the base model's 4-block MixedNet with its last layer's 17 positions
max-pooled in fours (the head reads 4 x 64 features, not 17 x 64 tied to
positions; 25,217 parameters), trained from scratch for 8,000 steps at a
constant 5e-4, the head divided by 3 before conversion. There it beat the
Flatten model at 8,000 steps (45.1% against 38.6% at 1 false wake an hour)
and its 100,000. It replaces a warm start from the 54-word base model, which
on a word that base never saw cost 21 points at 8,000 steps.

What a speaker's own recordings do, on okay_nabu's real far-field utterances
(office-multicap: 3,264, Mike and three others at four VPE positions) at the
cutoff where 156 h of household audio gives one false wake an hour, by the
firmware's rule (n=3 unless noted):

| model | Mike (held out) | the other three |
|---|---|---|
| shipped okay_nabu | 93.6% | 67.4% |
| trained here, warm start (before) | 5.2% | 2.6% |
| trained here, pooled | 12.6% (5-25) | 2.2% (0-5) |
| ... and 10 of Mike's recordings | 48.9% (14-72) | 5.8% (2-13) |
| ... and 25 | 77.5% (56-91) | 6.2% (3-10) |

The recordings were his from one VPE position (the others' utterances span
four), cut at the wake phrase. A word trained here is far below the shipped
model across the room: Piper's clips are not far-field speech. Recorded rooms
and background in place of the kit's synthetic ones (270 MIT impulse
responses, 1,370 AudioSet and FMA clips, as micro-wake-word's runs had) did no
better -- 8.7% for Mike without his recordings, 62.3% with ten, 2.4% for the
others -- and neither did prepare_finetune's weights (negatives x20 in the
loss, positives sampled 2 to their 30).

Recordings buy the speaker who made them and almost nobody else, as in
micro-wake-word's own measurements (62% -> 90% -> 96% for Mike with 0, 10 and
25). Whether ten from each of four people do as much for each of them as ten
of one person's do for that person is being measured there.

A model trained here needs firmware whose executor runs max pooling (Wake
word models, above).

### No VAD gate

microWakeWord's VAD model used to gate the wake words (a detection needed the
VAD's own window over its cutoff too). The app no longer pushes it. In the TV
study (mic_tap `okay-nabu-tv`: 684 runs over both VPEs, every channel and
gain, and the XVF3800, with TV and music) the gate dropped ~2% of the wake
words heard -- 21,167 against 21,664 at 0.85, 31,079 against 31,683 at 0.56
-- and stopped no false accepts, there being none either way. On DiPCo
(microWakeWord's `dinner_party_eval`, 91 h of dinner parties) by the
firmware's own rule (`scripts/ambient_false_accepts.py`):

| okay_nabu cutoff | gated | ungated |
|---|---|---|
| 0.3 | 0.68/h | 0.70/h |
| 0.4 | 0.32/h | 0.32/h |
| 0.5 | 0.14/h | 0.14/h |
| 0.69 | 0.04/h | 0.04/h |
| 0.85 | 0 | 0 |

A custom word (okay_beefcake, trained in the app) was the same gated or not:
0.20/h at 0.3, 0.05/h at 0.4, none from 0.5. Where people talk the gate is
open (81% of DiPCo), and that is where false accepts come from; the host
verifies every wake word in any case. The fallback detector ran ungated
already. Its slot goes to wake words.

## Two captures per call

The XMOS exposes exactly two taps into its pipeline, so every call is recorded
twice: `call-*.wav` from the AGC channel and `call-aec-*.wav` from the AEC
channel. The command is still split out of AGC; the speaker is identified from
AEC.

| channel | stage | used for |
|---|---|---|
| 0 | AGC | streamed to the app, command split, `call-*.wav` |
| 1 | AEC | wake word detection (gain 4), speaker ID and `call-aec-*.wav` (gain 16) |

Channel 1 carries two microphone sources because the two jobs want different
levels. Leave `aec_microphone` out of the config and only the AGC capture is
kept (speaker ID then falls back to AGC).

The AEC recording used to run at gain 1, and that wasted it: the tap sits ~45 dB
below full scale, so its top 16 bits held about 7 bits of speech. Gain is applied
to a 25-bit copy *before* the 16-bit truncation, so raising it restores real
resolution rather than amplifying quantization noise. Simulated on recorded VPE
AEC audio (undo the export gain, apply the firmware gain, keep the top 16 bits):

| AEC gain | speech peak | speaker EER (pc-vpe / capture2) |
|---|---|---|
| 32-bit reference | -9 / -11 dBFS | 0.83% / 0.49% |
| 1 | -50 / -55 dBFS | 2.40% / 3.75% |
| 4 | -38 / -43 dBFS | 0.86% / 0.65% |
| **16** | -26 / -31 dBFS | **0.81% / 0.45%** |

16 matches a 32-bit capture and leaves ~25 dB of headroom, so there is no need
to change the protocol to 32-bit samples.

**The captures are offset by a constant 56.5 ms**, AEC leading, measured at
+904 samples in every pairing with a correlation of 0.77-0.90. That is the
pipeline delay through the stages between the two taps; shift before comparing.

Levels differ by roughly 45 dB -- AGC peaked near 31000 where AEC peaked near
640 on the same utterance -- so AGC is close to clipping on a loud talker while
AEC has headroom to spare.

Wake detection moved to AEC because it is simply better there, on the same
20-utterance batch played at the same level:

| wake channel | gain | detections |
|---|---|---|
| AEC | 4 | **18/20** |
| NS (stock) | 4 | 14/20 |
| AGC | 1 | 11/20 (quieter playback) |

Measure this with the talker at a realistic level. An earlier pass had AEC at
1/4 and NS far ahead, which was an artifact of playing test audio normalized to
a peak of 1226 out of 32767: everything was marginal and the ordering was
noise. `scripts/make_cloud.py --gain 20` renders at a usable level.

### learn-tv firmware

`voice-call-vpe-learn-tv.yaml` (beside `voice-call-vpe.yaml`, in the ESPHome
checkout's `my_components_voice_call`) adds learn-tv
(`mic_tap/okay-nabu-tv/LEARN_TV_IMPLEMENTATION.md`): a two-mic null on
whatever has played for the last ~20 s, a TV. Its "learn-tv" switch in Home
Assistant (kept across reboots) goes back and forth between that and the stock
XMOS taps, to compare. In learn-tv mode both XMOS channels carry a mic after
AEC (the aec-both-mics XMOS firmware, 1.3.101), so there is no AGC tap; the
switch rewrites the XMOS stages, and the `learn_tv` microphone routes either:

| channel | used for | learn-tv | stock |
|---|---|---|---|
| 0 | wake words | mic 0 with the null, gain 4 | NS, gain 4 |
| 1 | the call (`call-*.wav`) | mic 0 with the null, gain 16 | AGC, gain 1 |
| 2 | the second capture (`call-aec-*.wav`) | mic 0, gain 16 | NS, gain 16 |
| 3 | a fallback wake detector (no VAD gate) | mic 0, gain 4 | off |

The captures are no longer 56.5 ms apart in either mode (learn_tv delays its
channels alike; NS and AGC leave the XMOS together): HELLO says
`aec_lead_samples: 0`, and the app lines them up with that (the call's
`call_start` step keeps it as `aec_lead`). A wake word only the fallback
detector hears -- the talker stands where the null points -- is held 300 ms for
the null's own detection, then starts a call with the captures swapped, so the
app hears mic 0: CALL_START says `detector: "fallback"` and `swapped: true` (a
session shows "mic 0 only"). The null learns nothing during a call, and keeps
what it learned.

The null takes ~10 dB more off a talker it doesn't point at, and Silero VAD,
on speech at -54 dBFS, missed the start of commands: 30% of a command's 32 ms
chunks over 0.5 on a recorded call, against 73% four times louder. The
capture stays as it is (speech-to-text hears it too); the endpointer raises
only Silero's copy, by how quiet the wake word was (endpointing, above). Mic
0's capture keeps the device's own speech after AEC, about 10 dB over the
talker: the stock pipeline's NS and AGC hide that residue, learn-tv has
neither.

Two sensors show where the null points: "Null direction", in degrees from the
mic axis (0 where a sound reaches mic 0 first, 90 broadside; a two-mic null
can't tell a direction from its mirror about that axis), and "Null coherence",
how steady it is (below ~10% it points nowhere). Both are unknown in stock
mode and for the first 2 s.

Unmeasured yet on a device: recognition on the learn-tv capture (no AGC or NS),
the cost of a second detector next to learn-tv itself, and the gain over the
stock path in a real room.

## Speaker identification

Every command is attributed to an enrolled household member, or to `unknown`,
and the satellite's LED ring takes that person's color while the command is
processed. [ReDimNet-b2](models/speaker/) (22 MB, raw waveform in, mel frontend
inside the graph) embeds the AEC capture from 1.2 s before the end of the wake
word to the end of the command -- "okay nabu" plus the command -- and the
closest profile wins if its cosine clears `--speaker-threshold` (0.45).

It runs beside recognition, on its own lock and two threads, and sends the
event the moment it knows, so the color is up while the command is still being
decoded: ~0.1 s on a Pi 5, ~0.2-0.35 s on a Pi 4.

| event | data |
|---|---|
| `processing` | `speaker` (name or `unknown`), `score`, and the color as `r`, `g`, `b` |

Benchmarked on recorded VPE audio of four speakers (whosaidit): ~1% EER on AGC,
~0.5% on AEC; at 0.45, about 98% of household commands are accepted while 0.5%
of an un-enrolled speaker's in the same room get through.

**Enrolling.** On the Speakers page: pick a name (new, or someone to add
to) and a satellite, and read the passage it shows aloud in a normal voice,
walking slowly around the room -- close to the satellite, across the room,
facing away now and then -- for about a minute (45 s of speech; 10 s at
least). Meanwhile the satellite streams what speakers are identified on (the
second capture, `AUDIO_MONITOR_AEC`, while monitored: firmware that can says
`monitor_aec` in HELLO) and its ring blinks red. Enrolling again on another
satellite, or in another room, adds to the same voice. The passage is the
app's own: commands as they will be said to a satellite, then plain sentences.
Walking matters more than the words: a far-field speaker embedding learns the
room and the distance as much as the voice, and identification happens across
the room. A kept session's page (Sessions) can also add its voice to a
speaker, and so can recordings on the command line (below). The Speakers page
renames, recolors and forgets them. Colors come from a fixed list of hues that
stay distinct on the ring, skipping red (error) and the default cyan (nobody
recognized).

**Their wake words.** Also on the Speakers page, an enrolled speaker says a
wake phrase at a satellite, pausing between, ten times or more
(`voice_call/wake_recordings.py`). The satellite streams as for enrolling,
and while monitored a wake word starts no command. Silero cuts the stream into
utterances; one with 0.4-2.5 s of speech is a take, kept when it scores 0.35
or more against the speaker's voice and no other voice scores higher (Piper's
speakers score about 0.65 against their own, 0.25 against another's) -- a
stricter bar than "someone else talking" below, since a take refused only
costs another. Whether the satellite's own model of the phrase heard each take
(its `WAKE_REPORT`s) is noted: how well the word does for them before
training. Takes are WAVs in `wake_recordings/` beside `speakers.json`, listed
in the profile under `wake` by the word's id (60 at most per word), so they
follow a rename and go with a delete; the page plays and deletes them one by
one. Training a wake word (below) uses the recordings of whoever is ticked.

**Someone else talking.** Once a command is a second long, its speaker is
identified from it and the wake word, and from then on its last second is
checked against their profile every 0.2 s, when it is mostly speech. Two
checks in a row under 0.20 mean someone else is talking: their speech is
silence for the command, which ends as soon as the speaker has been quiet
for the end-of-speech silence since their last word -- most often there and
then -- and what is recognized stops 0.3 s after it (speech-to-text streamed
live has heard the rest). Their last word is where the scores fell halfway
from the speaker's to the other voice's, half a window back, or a pause near
it. The session's End of speech step shows it. Nobody identified: nothing is
checked, as before. It is on by default: "End when someone else talks" on the
Settings page (`end_of_speech: {end_on_other_voice: false}` turns it off),
which says so, with a link to the Speakers page, while no voice is enrolled.

Windows, not stretches between pauses, because people start talking with no
pause at all: three recorded sessions where someone else did so, or after a
0.16 s pause, were cut 1.3 s and about 3.4 and 4.4 s into them by checking
only the stretches after a pause, where windows would have cut them about
0.4-1.1 s in. Simulated with
Piper's voices (six enrolled speakers, each against a voice like most
people's and the closest of 60, after no pause, 0.1 s or 0.3 s), a typical
voice was cut 0.9-1.1 s in every time, rather than after it stopped, with the
speaker's last word placed within 0.26 s early to 0.2 s late (10th to 90th
percentile; the speaker's level is the command's first checks); the closest voice a quarter
of the time (the call otherwise ends as before); and neither the command nor
the speaker's own going on was cut. Higher thresholds, or one check instead
of two, caught more close voices but cut the speaker's own going on: their
short stretches score as low as 0.2 against their own profile with music
playing.

**Learning.** A command that clears the threshold by 0.05, beats the
runner-up by 0.1 and has 2 s of speech is folded into that person's profile,
which then follows slow changes in voice and room. In benchmarking, a profile
recorded five hours earlier went from 81-92% accepted to 94-99% this way.
`--no-speaker-learn` keeps profiles fixed.

Profiles live in `var/speakers.json`, shared by all satellites (enrolling on
one VPE and identifying on another cost almost nothing in benchmarking):

```bash
.venv/bin/python -m voice_call.speaker list
.venv/bin/python -m voice_call.speaker enroll Mike /tmp/calls/*/call-aec-*.wav
.venv/bin/python -m voice_call.speaker color Mike violet     # or "#rrggbb", auto
.venv/bin/python -m voice_call.speaker rename Jenifer Jennifer
.venv/bin/python -m voice_call.speaker forget Mike
```

Profiles remember which capture they were built from; identifying from the
other one scores lower, and the app warns at startup. It also warns if the AEC
capture is too quiet to use -- firmware still streaming it at gain 1.

## Recognition (adapt-ctc)

Commands are recognized by adapt-ctc ([adapt_ctc/README.md](adapt_ctc/README.md)
has how, and how well on the office-multicap corpus), with six additions made
for this app:

- **Where the command starts.** The call is decoded from half a second before
  the wake word (as Kaldi placed it) and the wake phrase is found again in the
  CTC output; the command is the frames after it. Kaldi's own end lands a
  median 0.3-0.4 s late and eats the first word of the command: on the
  office-multicap corpus, cutting there was 76.2% correct against 89.6%. A call
  trace shows both: on `calls/call-15788.wav` the CTC places the end of the
  wake phrase at 1.16 s and Kaldi at 1.44 s. Kaldi still decides whether it was
  the wake word, and its end still starts endpointing.
- **Examples first.** A command's example sentences are searched for in the
  CTC evidence on every call, not only when no keyword intent matched, and one
  said word for word wins over keyword matches (`settings.examples_first`).
  The sentences are a graph of their own (about 16 ms for 51 sentences on
  x86). The TDT transcript, word for word, is still the fallback when nothing
  matched. A sentence found in the evidence counts only as all that was said
  (`settings.examples_whole`: no heard word left unexplained but the wake
  word's tail): the example "pause" (cost 8.0 with "the timer" unexplained,
  under the limit of 10) took "pause the timer" from the timer's keywords
  (0.03), and "cancel" "cancel the timer". On 250 recorded calls (`calls/`,
  `var/calls/`), none decided differently; 11 had matched an example.
- **Templates.** An example may use a small part of hassil's syntax --
  `(a|b)`, `[a]`, nested, inside words: `(turn|switch) on the light[s]`
  (`adapt_ctc/template.py`). A template stands for every sentence it can be,
  each searched for in the CTC evidence and looked up in the transcript like
  one written out; for meaning, the few that say every alternative once
  (`covering`: "turn on the lights", "switch on light") stand for all, since
  the rest are near copies that each cost an embedding (not their centroid:
  that sits between distinct phrasings and is close to neither). Slots and
  rules are refused. A copy whose templates stand for the same sentences is
  a twin (`shared_examples`); one sharing only some is an error naming both.
  At most 512 sentences a template: the example search costs about 0.06 ms a
  sentence on x86 (20 ms at 300, 64 at 1,100; the library's 72 take about
  10), after two changes -- one sentence per intent in the examples' graph
  is the cheapest in one pass, not the general parse that pairs every two
  spans (`one_phrase`), and what each leftover word was heard as is worked
  out once per call, not once per intent -- which took 1,100 sentences from
  156 ms (p90 784) to 64 (p90 112) and changed no decision on the 250
  recorded calls.
- **Value lists** (`lists:`) report which alternative of a group was heard, as
  a value: `{color: red}` for "crimson".
- **Bounded free text.** A wildcard may need both edges (`bounded`: after
  "add", before "to"), or run from a keyword to the end (`to_end`), and may be
  `required`; fillers may then sit inside it ("a bag of rice") but not at its
  edges. Its text is re-read from the TDT transcript over the same frames,
  which spells better than the CTC head.
- **The transcript too.** When nothing matches in the audio, the same
  keyword and name matcher runs on the TDT or STT transcript, as typed text
  is matched by a satellite's Test it (`Engine._match_transcript`: evidence made from
  its words, `Vocabulary.text_log_probs`), before skills and the fallback.
  A strong speech model may hear plainly what the CTC evidence only half
  did: "Unlock front door." from Home Assistant Cloud, where the CTC head
  heard "unlock" at no cost but "front door" for a total of 10.88, past the
  rejection threshold of 10. Keywords and names only: the transcript was
  already looked up among the example sentences whole, and on clean text a
  short example with words around it is no example said ("grab bananas
  *next* time you shop" ran next_track until example search was left out of
  this pass; `recognize_log_probs(examples=False)`). `matched_by:
  transcript`; the call trace keeps what the pass found (`transcript_pass`).
- **Rooms must fit** (`settings.places_must_fit`, on). An area or floor name
  heard that a match leaves unexplained rejects an intent that takes no name
  of that type -- keyword matches and example sentences alike. Before, a
  match only paid for one unused recognized word, and an example sentence
  for a few: "play music in the office" was the example "play music",
  resuming the music here; "pause the music upstairs" paused it here. Free
  text that runs to the end may stop before a place the sentence ends on
  ("in", "on", "at", "to", optionally "the", or "upstairs" / "downstairs"
  alone) when the intent takes that place -- "play jazz in the kitchen" is
  jazz, in the kitchen -- and swallowing it counts as leaving it out for an
  intent that doesn't. A device name works the same way, after "in", "on",
  "at" or "to" only, and only as the end of free text: "play jazz on the
  Mark 2" is jazz on the Mark 2 for an intent that takes that device
  (`_name_allowed`: its domains and ids), and no match for one whose text
  would swallow it -- a duplicate of `play_music` that takes a media player
  wins over the plain one. A device left unexplained elsewhere is not
  checked: commands with a fixed target are said with their device's name.
  The text re-read from a transcript without word times (an STT entity)
  drops the clause too (`without_place`, comparing spoken forms: "Mark 2" is
  the name "Mark two"). A room inside the text is the text: "play the kitchen
  sink by radiohead". The fallback holds its picks to the same rule
  (`Tools.places_left`: area and floor names and aliases in the transcript,
  outside free text and the names of the devices it took). A hint says "but I
  can't pick a room for it". On the office-multicap corpus (2,030
  recordings, `examples/home-extras.yaml`) not one recording changed: 1796
  correct either way, so a misheard word taken for a room rejected nothing.

When nothing matched, the closest command is named. From the keyword evidence
first: a command whose keywords, names and numbers cover at least half the
words that are not fillers, with what it lacked (`IntentResult.missing`) --
or one heard whole that lacked only a room, floor or device it can take,
however much of the sentence was its free text ("play jazz in the office":
"…but I can't pick a room for it"). Or
else by meaning, with EmbeddingGemma 2 against the commands' descriptions and
examples, when the closest is at least 0.6 similar and 0.02 ahead of the next
command or everyday request. The embedding never accepts a command
(`settings.embedding_accept: false`), and is loaded only when present
(`--embedding-model`).

Synthesized commands (Piper, `en_GB-jenny_dioco`) against the library, on x86:

| said | answered |
|---|---|
| "set the brightness" | It looks like you're trying to set the brightness of the lights, but I didn't hear the percentage. |
| "turn on the garage lights" (no such area) | ... turn on the lights, but I don't know "garage". |
| "add to the shopping list" | ... add something to the shopping list, but I didn't hear the item. |
| "what is the time" | ... tell the time, but I didn't understand all of it. (by meaning: 0.92 against 0.85) |
| "set a timer for five minutes" (no timer command) | Sorry, I didn't understand that. |
| "blah blah blah" | Sorry, I didn't understand that. |

Recognizing a command, acoustic pass included, took 40-210 ms there (4
threads). The TDT pass is paid only for free text and for calls that matched
nothing.

## Timers and the Voice Call integration

Timers live in Home Assistant, on a `voice_timer_list` entity per satellite,
provided by the Voice Call integration (`custom_components/voice_call`). That
entity platform is the `timer_list` integration from the
`claude/timer-list-entity-local-d25403` branch -- not yet merged -- with its
domain renamed so the two cannot clash, and with `InMemoryTimerListEntity`
restored from before `b205e2d11ea`. When `timer_list` is merged, the timer
lists should move to it and this copy go.

**Devices.** The integration was meant to put the timer list on the
satellite's ESPHome device, so one area served both. Home Assistant can no
longer do that: since the child-device work (`ad96b06be6a`, August 2026) a
device belongs to one config entry, a lookup by MAC only finds the registering
entry's devices, and a child device's parent must share its config entry. So
each satellite is a device of its own; on creation it takes the ESPHome
device's area, and keeps its own from then on. The app registers satellites
(`voice_call/satellites/sync`) as they connect, and reads each one's area from
its device.

**Matching.** Timer commands carry an operation (`timer: cancel`); which timer
is decided by the app against that satellite's running timers
(`voice_timer_list/item/list`), from `timer_name` (a running timer's name, from
the `running_timers` value list the engine rebuilds when the names change),
free text matched loosely, the length it was set for, or being the only one.
Every plan has an outcome (`done`, `no_timers`, `not_found`, `ambiguous`,
`already`, `missing_duration`); a command's `response` may give a template for
each, and the rest say `timers.plan`'s own sentence.

**Ringing.** The app follows each timer list (`voice_timer_list/item/subscribe`).
When one finishes it pushes microWakeWord's `stop` model to the satellite (the
device holds up to four; `scripts/check_mixednet.py` shows it decodes
identically to the upstream harness over 1,162 steps), says which timer it was,
and plays the stock VPE's `sounds/timer_finished.flac` (CC BY 4.0, see
`sounds/LICENSE.md`) with half a second between repeats until a call starts --
the "stop" model firing, or the wake word -- the center button is pressed (the
firmware sends a `stop_timer` device event, and quiets the ring and any
announcement itself), or five minutes pass. The firmware
plays audio outside a call, and clears its playback buffer when a call starts.
Outside a call the app streams at most `PLAY_LEAD` (0.3 s) ahead of the
device: the device's buffer only pushes back once full, and with Sendspin's
512 KB TCP window seconds of alarm could otherwise queue in front of the
app's hang-up, so "stop" would start a call nothing answers until it timed
out. Each playback also waits for its own `PLAYBACK_DRAINED` (a stale one from
the last response used to let the alarm repeats through back to back).
Afterwards the models are cleared and the wake word pushed again. The
`timer_ringing` and `timer_stopped` events reach the device's YAML, which turns
music down and flashes the LED ring while one rings.

**The LED ring.** Between calls the ring shows the time left on the satellite's
active timer that finishes soonest, as the stock firmware does: a share of the
ring, fading out clockwise. The app sends a `timer_progress` event (`shown`,
`remaining`, `total`, `paused`) when that changes -- a timer set, paused,
lengthened, cancelled or finished, and on connecting -- and the device counts
down by itself in between (`timers.progress`).

## Home Assistant STT and TTS

`voice_call/voices.py`. Each satellite listens and speaks with the built-in
models (TDT, Piper) or with Home Assistant's entities: `stt`, `tts`,
`tts_voice` in `settings.yaml` for every satellite, the same keys in
`satellites.yaml` per satellite (`builtin` for the app's own model, no key for
"as every satellite"), plus `speak_on`, a media player for its responses.

**STT replaces TDT, not the matcher.** The CTC matcher always decides. The
engine asks a transcriber only for text: examples not found in the CTC
evidence (`match_examples`, which takes a per-call transcriber now), free text
(`_reread_text`), hints. `HassTranscriber` is that transcriber for an STT
entity: called from the engine's thread, it posts the command -- from
Kaldi's split (`stt_audio_for`, below) -- to `/api/stt/<entity>` on the event loop (there is no websocket STT outside a
pipeline) once, and only when asked -- keyword commands never wait for it --
and falls back to TDT on failure. STT text has no word times, so free text is
found by the wildcard's own keywords in the transcript (`engine.free_text`:
after an `after` keyword to the end or to a `before` keyword, or as many
words before a `before` keyword as the CTC head heard there). The language is
`stt_language` (`de` matches the entity's first `de-*`), else the entity's
best match for English (`stt/engine/list`); TTS has `tts_language` the same
way, and a satellite may say `auto` to override the home's language with the
automatic one.

**Always run** (`stt_always`, in `settings.yaml` or per satellite, with an
STT entity): `StreamingTranscriber` posts to the same `/api/stt/<entity>` as
a chunked upload, which Home Assistant hands the entity as it arrives
(`request.content`). `VoiceCallApp._start_stt` opens it once Kaldi has
verified the wake word, from Kaldi's split (where endpointing starts) --
the AGC capture, or the AEC one lined up with it as `stt_audio_for` does,
boosted by the gain that brings the wake word in it to -3 dBFS (the
command's own peak isn't known yet). `_feed_stt` sends what arrives (both
captures' messages), and `_respond` sends the rest and ends the upload. The
engine asks it as it would `HassTranscriber`, and waits only for the
entity's answer; if the stream failed it asks again in one request, then
falls back to TDT. When it isn't asked, `_keep_streamed` puts the answer in
the session's recognition step when it comes (`CallLog.update` saves a kept
one again). A call that ends without a command (the device, a dropped
connection, a timeout) cancels the upload.

An STT entity hears the call from Kaldi's split -- streamed or asked in one
request (`stt_audio_for`) -- not from the engine's cut, which is only known
once the whole call is (`place_command` on the start of a call, even with a
second of audio past the split, matched the full call's cut on 89 of 205 test
calls). TDT still reads the command from the cut: its word times are placed
in the command's CTC evidence. On the 205 recordings in `calls/`, Kaldi's split was later than the
cut by more than 0.3 s on 62 -- mostly the end of "Nabu", which the CTC
placement leaves in the command. TDT from Kaldi's split began with a likely
first word ("turn", "set", "what") on 141 of them, from the cut on 103 ("I
would turn on...", "Taboo set..."); starting 0.15 s or 0.3 s before the split
did worse (132, 74). Kaldi's split clips the first word now and then ("that
kitchen brightness" for "set kitchen brightness").

**TTS on the satellite.** `tts.speak` on the satellite's ESPHome media player
(found by its MAC in the device registry): the firmware's `speaker_source`
has an announcement pipeline with an `audio_http` source on a third mixer
input, so Home Assistant sends the URL and the device fetches it (converted
by Home Assistant to what the device accepts). Home Assistant's state for
that player cannot say when the announcement ends -- with Sendspin music on
the media pipeline it is "playing" throughout -- so the device says so
itself: a new device-to-app message, `DEVICE_EVENT` (0x09, `{"type":
"announcing" | "announced"}`), sent by a `voice_call.send_event` action from
the media player's `on_announcement` and `on_state`. The app keeps the call
open until `announced`, as it does until `PLAYBACK_DRAINED` for Piper, so the
ring, the LED and hang-up keep their timing. A new call stops an
announcement, and music is turned down while one plays.

**TTS elsewhere.** With `speak_on`, `tts.speak` targets that player and the
call ends at once. The acknowledgement sound (a command with nothing to say)
is still streamed to the satellite.

**Listening live.** A satellite page's "Listen" opens a WebSocket,
`/api/satellites/{key}/live`: binary messages are the microphone (16 kHz
16-bit mono, batched as they pile up), JSON says `start`, `call` (a call
starts or ends) and `end`. While anyone listens the app sends the device
`MONITOR` (0x14, `{"active": true}`; `false` when the last one leaves), and
the firmware streams its microphone between calls as `AUDIO_MONITOR` (0x0A)
-- a call streams its own audio, which listeners get without its pre-roll,
already heard. The firmware says it can in HELLO (`"monitor": true`), stops
on a disconnect, and fires its own `monitoring` event on every change, which
the VPE config turns into a red blink on the ring: what the ring shows comes
from the device's state, not from what the app says. While monitored, the
firmware also sends `WAKE_REPORT` (0x0B) about ten times a second: each
model's highest probability since the last, of an inference and of its
sliding window's mean (what meets the cutoff), and the wake words detected
-- which it does not answer: someone listening is not talking to it. The
browser plays the audio through Web Audio (a fifth of a second behind, for
the network's jitter) and draws the level, the last six seconds, and a strip
per model with its cutoff and its detections. A speaker being enrolled from a
satellite (the Speakers page) keeps it monitored too, and firmware that says
`"monitor_aec": true` in HELLO also streams the second capture meanwhile, as
`AUDIO_MONITOR_AEC` (0x0C): what speakers are identified on.

A call's `CALL_START` says what its wake word came in with: `probability`,
the sliding window's mean that met the cutoff, `max_probability`, the
highest single inference in that window, and `cutoff`, all 0-255 as in
`WAKE_REPORT` (firmware from before leaves them out). The app keeps them
0-1 in the session's `call_start` stage, which the Sessions list and a
session's page show.

The LED ring's speaker colors are gamma-corrected by ESPHome (2.8), which
turns orange and pink red, and red means someone is listening: the palette
(`speaker.PALETTE`) is written for how it looks on the ring, and a speaker
with a color that showed red gets a new one when the profiles load.

### The multilingual TDT model

`settings.yaml` `tdt_model: multilingual` replaces the English TDT head
(parakeet-tdt_ctc-110m's) with NVIDIA's parakeet-tdt-0.6b-v3, as sherpa-onnx
releases it (`sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8`, copied as is into
`models/parakeet-tdt-v3-int8`; `adapt_ctc/tdt.py` takes its `*.int8.onnx`
names). It is loaded in place of the other while the app runs
(`Engine.use_tdt`, under the recognition lock), and the recognizer is rebuilt
around it. It finds the language itself, so it writes German example
sentences, free text and hints in German -- but the CTC matcher is still the
English model, so commands in another language are matched by their example
sentences, word for word against its transcript, not by keywords.

Transcribing (best of three, after a warm-up), 2 threads:

| | resident | 3.0 s command | 3.8 s German | 6.8 s French |
|---|---|---|---|---|
| English, this machine | 319 MB | 41 ms | 53 ms (nonsense) | 90 ms (nonsense) |
| multilingual, this machine | 1,191 MB | 188 ms | 229 ms | 408 ms |
| English, Pi 5 | 290 MB | 127 ms | 162 ms (nonsense) | 310 ms (nonsense) |
| multilingual, Pi 5 | 867 MB | 382 ms | 468 ms | 849 ms |

(4 threads on the Pi 5: 354 ms for the command.) It is only asked when text is
needed -- never for a keyword command -- so the cost lands on examples, free
text and misses.

## Music (Music Assistant and Sendspin)

The firmware (`voice-call-vpe.yaml`) runs a Sendspin client, wired as in the
stock VPE firmware: the Sendspin media source plays through a `speaker_source`
media pipeline into the mixer's second input. Music Assistant finds it on its
own (`_sendspin._tcp`, port 8928), connects, and makes it a player; Home
Assistant's Music Assistant integration then gives that player an entity. No
player has to be added by hand -- those are Music Assistant's wrappers of Home
Assistant's own media players.

**The satellite's player.** The Sendspin client identifies itself by MAC
(`D8:3B:DA:A3:D5:F4`), but Music Assistant's player id is its own
(`up54cb3d39`; an older player was `up` + its MAC), and Home Assistant's
device for it has no MAC and no area, so nothing in Home Assistant's
registries ties it to the satellite. Music Assistant's own player data does:
the Voice Call integration reads the players from the Music Assistant
integration's client (`entry.runtime_data.mass.players`), finds the one whose
device has the satellite's MAC -- or wraps the Sendspin protocol player that
has it -- and returns its entity with the satellite
(`custom_components/voice_call/music.py`). The app re-registers satellites
when Home Assistant's registries change, so a player added later is found.
Without the integration the app guesses: `up` + the MAC, else the only Music
Assistant player whose device is ESPHome's model named like the satellite
(several satellites flashed from one YAML share that name, so then none). The
satellite's page can always pick one.

**Commands.** A `music:` operation is carried out by the app (like `timer:`):
the player is one the command names, else the satellite's own, else -- to
pause, skip, change the volume or ask what's playing -- whichever of Music
Assistant's players plays in the satellite's area (Home Assistant's own media
intents choose the same way); "resume" picks what voice paused there.
Moving the music (`music: move`, `music.choose_move`) goes from what plays
there -- else the one player playing anywhere, so "move the music to the
kitchen" works from the kitchen too -- to the first player named that isn't
the source (one not playing first), with `music_assistant.transfer_queue`
(`source_player`, `auto_play`): the queue goes along and the source stops.
Everything goes through Home Assistant: player state by template,
`media_player.*` for controls, and for "play …" `music_assistant.search` on
every reading of what was heard ("so what by miles davis" is a title, and a
title by an artist; "the album …", "my … playlist" pick a type), the result
closest to what was heard (`difflib`, a small type prior), then
`music_assistant.play_media` with its URI, so the response names what was
found and a miss is `not_found` rather than a random track. When
`music_assistant.search` fails -- Home Assistant 2026.11 rejects its own
response when Music Assistant leaves an album's `favorite` empty
(`music_assistant/schemas.py`: `probatio.Optional(ATTR_FAVORITE): bool`) --
the app searches through the player (`media_player.search_media`, "Artist -
Name" titles, the type from the URI) from then on. Controls say
nothing when done (the acknowledgement sound). Pause and next act on the
player's queue: a grouped player pauses its whole group.

**On the device.** Music is never paused for voice: the mixer's media input
drops 20 dB at `on_call_start` and while a timer rings (the `timer_ringing`
event), and comes back over 1 s when neither holds. The center button pauses
the Sendspin group when it plays and resumes it on the next press, through the
Sendspin controller role (`media_player: platform: sendspin`), without Home
Assistant.

## When nothing matches: meaning, then GLiNER

`voice_call/fallback.py`. After the CTC matcher (keywords and examples), in
order, each only when the one before found nothing -- and each only for the
commands chosen for it (`fallback: [meaning, llm]` on the command; ticked on
its page, or many at once on the Settings page). None are chosen to begin
with, the library's included: a command is run this way only when someone
decided it may be.

1. **Meaning.** EmbeddingGemma (already loaded for hints) names the closest
   command; it is *picked* only when it is chosen for meaning, the similarity
   is at least 0.80, and at least 0.05 ahead of the runner-up (another
   command, chosen or not, or an everyday request that is not one -- never
   another combination of the same command: only a command's first
   combination carries its description and examples). A command that hears
   nothing runs; for one that hears something, GLiNER hears what was said in
   it (only that command's entity types, no classification), and that picks
   the combination --
   nothing heard is the empty one, when it has it. Without the model, meaning
   runs only commands that hear nothing. `settings.yaml` `fallback_meaning`,
   on by default.
2. **The model step** (`fallback_model`; the commands chosen for `llm`, its
   id from when it was an LLM): GLiNER2.5 small (below). What it picks is
   checked like a match: the command must be chosen and available on the
   satellite, names map back to ids, list values to values, and a name,
   value, number, duration or free text the sentence does not say is
   dropped. A required one that is missing or wrong refuses it, and the miss
   is hinted from the command picked. `fallback_llm`, on by default (it loads
   nothing until a command is chosen for it); `fallback_top_k`.
3. **The hint**, as before.

**GLiNER** (`voice_call/gliner.py`): GLiNER2.5 small (fastino, 74M
parameters, Apache-2.0), an encoder that in one pass classifies a sentence
among labels and finds spans of named types in it. The labels are the
commands' descriptions plus "something else: a question, small talk or
thanks"; the types are their parameters (a room, a device, music, something
to buy, a duration, a percentage, a value from a list, ...). One label per
command, whatever its combinations: what it hears in the sentence picks the
combination ("lights out in the bedroom": turn off the lights, with an
area). It picks among every command, those that hear nothing too ("have you
got the time"). It is given the 8 (`fallback_top_k`) closest in meaning to
the sentence, *chosen or not*: alone, a command is taken for nearly
anything ("never mind" played music at 0.87), and the others are what it
tells the chosen ones from. Its pick runs only when it is chosen, at least
`fallback_threshold` sure (0.3), ahead of "something else", and what it heard
is exactly one of the command's combinations (a room and a floor for a
command that takes either is refused). Hearing nothing in it ("lights out"),
there is nothing to check the pick by, so it runs only when meaning puts the
same command first -- ahead of the everyday requests that are no command,
too: with every library command chosen, "thank you" was "say good night",
"what's the capital of France" the date and "what's twelve times eight"
turned on repeat, each with an everyday request far closer in meaning
(0.96, 0.97, 0.87) than the command (0.76, 0.60, 0.67). The same words found
as two types ("living room" as a room and as a floor) count once, as the
surer type the command can take. There is no prompt prefix, so how many are
chosen costs nothing.

**Versions.** The library's commands come in versions (on this satellite, a
device by name, an area or floor by name), each a command of its own, and
described alike. To meaning and the model step, commands described alike
are one (`fallback.label_of`): meaning's runner-up is the closest command
described otherwise (`closest_by_meaning(group=...)`), GLiNER gets one label
for them, and what the model hears picks the version as it picks a
combination -- of those it fits, the one that takes the most ("play jazz on
the Mark 2" is the device's, not this satellite's with the device left
out). As separate commands, "kill the lights" was 0.88 from Turn off the
lights in this satellite's area and refused as "too close" to the same
command for an area or floor, and GLiNER chose among identical labels.

It runs without PyTorch: onnxruntime, numpy and the tokenizers library
(about 50 ms a sentence on a desktop, 0.27-0.36 s on a Raspberry Pi 5 with 4
threads; 440 MB at peak with it loaded, of which ~110 MB is the tokenizer).
The networks are exported once by `scripts/export_gliner.py` (it needs
torch and the gliner2 package, in a venv of its own) into
`models/gliner2.5-small-v1-onnx` (78 MB), which `script/stage_build` copies
into the image: the encoder with 8-bit weights and a 4-bit embedding
(MatMulNBits), and the boundary head split in two around the candidate
spans, which are chosen in numpy (it branches on sizes and needs a stable
sort, which ONNX lacks). The
rest of what the gliner2 package does -- the prompt, word splitting,
decoding -- is ported. Against the package, on 131 of the benchmark's inputs:
identical tokens, positions and entities, probabilities within 3e-6.

**The encoder's precision.** With 4-bit weights throughout (60 MB) the
small model clipped spans: "for the shopping list, add apples please" added
*add apples*, and so did 1 of 5 rephrasings; full precision (285 MB) and
the base model found *apples* in all six. The 4-bit layers did it -- a 4-bit
embedding alone changed nothing -- so the layers are 8-bit (onnxruntime
quantizes an embedding to 4 bits only). That behaves like full precision: on
the eight-command benchmark both 4-bit and 8-bit get 17 of 34 right, refuse
30 of 30 and run nothing wrong; 8-bit gains "can you play something by the
beatles" and loses "can i get some light in the living room" (refused, as
full precision does). On a Pi 5 it is 15-25% slower than 4-bit and 20 MB
bigger at peak. int8 with quantized activations breaks DeBERTa-v3 (base: not
a command right; small: *add apples* in 4 of the 6).

`scripts/bench_fallback.py` with `tests/fallback_utterances.yaml`: 64
sentences the library's keywords mostly miss -- music, shopping and to-do
lists, alarms, timers, lights and devices, covers and locks -- plus 10 that are
not commands ("what's the capital of France", "thank you"), in a made-up home.

The fallback as the app runs it (`--mode fallback`: meaning, then the model
step, on the sentence as text), desktop. Eight commands chosen (`--chosen`:
play music, add to and take off the shopping list, add to the to-do list,
set an alarm, start a timer, lights on, lights off); 32 sentences are for one
of them, and the other 32 (for another command, or none) should be refused:

| model step | its commands right | refused when it should be | ran a wrong command | model step, median |
|---|---|---|---|---|
| GLiNER small, 0.3 | 19/32 | 32/32 | 0 | 0.05 s |
| GLiNER small, 0.2 | 20/32 | 32/32 | 1 | 0.05 s |
| GLiNER small, 0.5 | 13/32 | 32/32 | 0 | 0.05 s |

Every library command chosen (56):

| model step | right | non-commands refused | ran a wrong command | model step, median |
|---|---|---|---|---|
| GLiNER small, 0.2 | 67% | 10/10 | 3 | 0.05 s |
| GLiNER small, 0.3 | 66% | 10/10 | 2 | 0.05 s |
| GLiNER small, 0.4 | 63% | 10/10 | 2 | 0.05 s |

Since the library has versions (2026-10-18), the same eight as versions
(play music on this satellite and in an area or floor, the lights on and off
on this satellite and in an area or floor, and the rest): GLiNER at 0.3 gets
16/32, refuses 32/32, runs no wrong command; with every version chosen (109
commands), 56% right, 10/10 non-commands refused, 4 wrong (the two timers
below, "throw on my workout playlist" as more like it, "don't let me forget
to buy eggs" as a reminder). Before versions were one choice to it, 11/32;
GLiNER also picking commands that hear nothing gains "hold the music",
"forget the timer" and "secure the house" and loses "can you play something
by the beatles" (offered more types, it no longer finds the band).

With optional slots instead of combinations, GLiNER at 0.3 got 17/32 and
59%. Meaning now picks the command for slot-taking commands too, and the
model hears the rest: "kill the lights in the office" is the office, "shut
the lights off downstairs" the ground floor -- with optional slots, meaning
ran both in the satellite's own area. GLiNER almost never runs a wrong
command: with eight chosen, what it misses it refuses -- "not sure enough",
or a closer command that is not chosen. With every command chosen, the two
it gets wrong are timers: "give me ten minutes" and "let me know when twenty
minutes are up" ran *how long is left* on a 10 and 20 minute timer. GLiNER
reads any length of time as that label, however start and status are
described; it is a question, so harmless, but wrong. 0.2 runs one more wrong
command, so the default stays at 0.3. What it doesn't do well is infer: "we
need milk", "it's too dark in here".

**German** (`--language de`: the same 64 sentences said in German,
`tests/fallback_utterances.de.yaml`, with the German library). GLiNER2.5
small does not understand German -- its encoder is the English DeBERTa-v3,
which has no "▁Küche" -- so the German pack leaves it out (`gliner=False`):

| 2026-10-20 | English | German | German, commands described in English |
|---|---|---|---|
| meaning alone, every command chosen | 12/64 | 12/64 | |
| meaning and GLiNER, every command chosen | 38/64, 5 wrong | 19/64, 10 wrong | 18/64, 11 wrong |
| meaning and GLiNER, the eight chosen | 17/34, 0 wrong | 5/34, 1 wrong | 1/34, 0 wrong |

What it got right in German shares letters with a description ("Licht",
"Einkaufsliste"); what it got wrong shares a word: "der Kaffee ist alle"
locked *alle* Türen, "wir brauchen keine Äpfel mehr" stopped repeating
(*nicht mehr wiederholen*). Meaning (EmbeddingGemma, multilingual) does as
well in German as in English.

**GLiNER2.5 multi** (`fastino/gliner2.5-multi-v1`, Apache-2.0): the same
architecture on mDeBERTa-v3-base (768 wide, a 250k vocabulary), exported by
`scripts/export_gliner.py` as the small one is (8-bit layers, 4-bit
embedding: 274 MB; against the gliner2 package, classification within 0.04
and the same entities). `--model-dir models/gliner2.5-multi-v1-onnx`:

| 2026-10-20 | every command chosen | the eight chosen |
|---|---|---|
| small, English (the app's) | 38/64, 5 wrong | 17/34, 0 wrong |
| multi, English | 35/64, 10 wrong | 15/34, 1 wrong |
| multi, German | 28/64, 8 wrong | 11/34, 2 wrong |
| multi, German, at 0.25 | 30/64, 9 wrong | 13/34, 2 wrong |
| multi, German, German type labels and readings | 26/64, 10 wrong | 11/34, 3 wrong |

Better in German than the small one (19/64), not as good as the small one in
English, and its wrong commands include opposites: "das ist zu laut" turned
it up, "hier ist es zu dunkel" turned the lights off. Labels and readings in
German (types such as "Uhrzeit: eine Uhrzeit, wie halb acht", German numbers
and durations) don't help. It is 4x slower (0.17 s a sentence on a desktop,
against 0.04 s) and 420 MB bigger at peak (896 MB). German keeps the model
step off.

The model step was a small LLM before GLiNER -- Granite 4.0 350M or Hammer
2.1 0.5B in llama.cpp, the commands as tools -- and they were dropped: with
the same eight chosen they got 21/32 and 19/32 right but refused only 26/32
and 28/32 and ran 7 and 6 wrong commands, at 0.4 s on a desktop and up to
6 s on a Pi 5 with the closest eight. Their code and numbers are in git
history.

## Trying typed text

Test it, on a satellite's page (`/api/try`), skips speech recognition: the typed text is the
transcript, and the CTC matcher reads evidence made from it
(`Engine.recognize_text`, adapt-ctc `Vocabulary.text_log_probs`). Its spoken
words (`adapt_ctc.text.spoken_words`: "10" is "ten", "&" is "and") are
spelled in the model's own SentencePiece pieces (the fewest that spell each
word), each piece certain for two frames, with blanks between pieces and
words and around them. Keywords, names and numbers then match as if the
sentence had been heard perfectly, through the same code as audio; the text
itself stands in for the TDT head (`TypedText`), so free text ("Kind of Blue
by Miles Davis", as typed), skills and questions read it as written. Example
sentences count only typed whole (`Recognizer.example_first`, then
`recognize_log_probs(examples=False)`): example search in the evidence
allows words around an example, for audio, and on clean text took "grab
bananas *next* time you shop" for "next" -- the same rule as the transcript
pass. All 58 example sentences in the library match their own command this
way. A word no pieces spell (outside the model's
alphabet) is left out, and listed in the trace.

With `speak` (the "Say it first" box), the old way: Piper says it, and it is
recognized from the audio like a call -- to test speech recognition too.

## Skills and questions

Ported from llama.cpp's smart-home-qa-bench (`router.py`, and its model
results). When no command is meant -- after the CTC matcher, and around the
command fallback -- a question still gets an answer:

1. **Skills** (`voice_call/skills/`, one module each, Questions on the Home
   page): spelling, dates, clock arithmetic, unit conversions, arithmetic --
   grammars that must parse the whole question *and* compute an answer, or
   let it fall through. Before the command fallback: they are instant and
   never wrong when they answer. Spelling first ("how do you spell weather"
   is not about the weather); the others only for a question that is not
   about now or here (`skills/live.py` `is_live`: weather, news, scores,
   prices, opening hours, "tonight", "last night"). Each answers at three
   levels (`common.Reply`: brief "96.", specific "It's 96.", verbose
   "Twelve times eight is 96." -- the question said back where the skill
   can't say it with the answer); `settings.yaml` `skill_responses` picks
   one and may give a template per level, which the server renders through
   Home Assistant (`VoiceCallApp._skill_says`, the built-in sentence when it
   can't). The "can't look it up" skill, which answered questions about now
   or here, is gone: those go to the agent, or the Unknown command.
2. **A Home Assistant conversation agent** (`voice_call/agent.py`;
   `settings.yaml` `agent`, none unless chosen): everything else -- not only
   questions -- through `conversation/process`, with the satellite's
   `device_id` (Home Assistant 2026.10 and later; sent without it to an older
   one) and its `conversation_id`, kept for `agent_keep_minutes` (5, the
   Voice PE's `conversation_timeout`) after the agent's last answer on that
   satellite. An answer that is Home
   Assistant's `no_intent_match` counts as none. A dry run never asks it: an
   agent can act. This replaced a small question LLM (Granite 4.0 350M or
   Gemma 3 1B in llama.cpp, for what read as a question); the benchmark
   numbers below are from then.

Each gets the question normalized first (`skills/common.py` `normalize`):
what a typed question, or a speech-to-text that formats what it hears (Home
Assistant's), writes -- "What’s", "50°F", "−5", "12 × 8", "½ cup", "1½
quarts", "5²", "10 m²", "√144", "$80", "100km" -- in the words the grammars
read. A currency is live only when there are two ("how many euros is a
dollar"); "15% of $80" is 12 dollars, and "3.5 kg in pounds" is a weight.

They read both transcripts, TDT's then CTC's where it differs: on a spoken
Try of "What is 50°F in Celsius?" (Piper reads it "fifty degrees F") TDT
heard "fifty degrees FN Celsius" and CTC "F in Celsius". Piper's audio differs from
run to run, so the same question can be heard differently each time. The
grammars take what the models make of short words: "F and C", "CNF" and "CN
F" for "C in F", "one a half", "quartz", and spoken decimals ("ninety eight
point six").

Every skill is on unless turned off, for the whole home (`settings.yaml`
`skills: {spelling: false}`) or one satellite (`satellites.yaml`, the same;
its own setting wins). A skill is a `Skill` (`skills/common.py`: id, name,
description, examples, icon, `answer(text, settings) -> Reply | None`,
`settings`) in a module named after its id, listed in `SKILLS`
(`skills/__init__.py`) in the order they are asked.

Its settings are a `common.Settings` subclass in its module: `load` reads
them as stored, `to_json` gives them to its page in full, and `store` turns
what the page sends back into what is stored -- only what differs from the
built-in, so built-in units and holidays left alone follow updates.
`settings.yaml` `skill_settings`, by skill:

```yaml
skill_settings:
  units:
    volumes: imperial      # the built-in volumes' sizes (units.VOLUMES; us)
    units:                 # by name: a unit, or false for a built-in one taken out
      cup: {measures: volume, size: 240}
      nautical mile: {measures: length, size: 1852, also: [nmi]}
      Rankine: {measures: temperature, freezes: 491.67, boils: 671.67}
      stone: false
    digits: 3              # significant digits (4)
  spelling:
    words: {color: colour, my last name: Hansen}
  dates:
    holidays:              # by name: when, {when, also}, or false
      Thanksgiving: second Monday of October
      My birthday: {when: June 3, also: [my bday]}
      Juneteenth: false
  clock: {hours: 24}
  arithmetic: {digits: 6}
```

A unit's size is in its measure's base unit (`units.MEASURES`: meters,
square meters, milliliters, grams, seconds, bytes); a temperature scale is
two points instead, where water freezes and boils. `volumes` sets the
built-in volumes' sizes, and what a unit is compared with to store it:
imperial is the UK fluid ounce, pint, quart and gallon, with the metric cup
and spoons. A volume said with its measures ("an imperial pint", "a US
cup", `units.QUALIFIED`) is theirs whichever are set. A holiday's `when` is read
by `dates.when`: a date, the nth or last weekday of a month, Easter or days
or weeks before or after one of those, or a date with its year (once).
Settings that can't be read -- edited by hand -- are logged and the built-in
ones used (`skills.configure`); the skill's page says why. The engine gets
them as `Engine.skill_settings` (`VoiceCallApp.configure_skills`, at start
and when a page saves).

From the benchmark (Q8_0, the "plain" prompt the app uses), on the 148
knowledge questions its router hands to a model: Granite 4.0 350M 128 right,
~0.2 s per answer on a Ryzen 9 5950X; Gemma 3 1B 137, ~0.6 s. On a Pi 5 here
(4 threads, 40 questions) Granite answered in 0.45 s median, 0.9 s p90.

The port was checked against the original: it routes all 241 benchmark
questions and all 180 held-out ones exactly as it did, and every exact answer
is right (65 and 55). It was written against typed questions, so spoken ones
needed more: "zero", years in pairs ("twenty twenty four"), clock times in
words ("ninety minutes after two pm"), "how should you spell", and a
transcript's question marks in the middle of a sentence ("the average of
four? eight and twelve").

Spoken (`scripts/bench_questions.py`: Piper says each question, then the
whole recognition with the library's commands, meaning on, Granite):

| | benchmark (241) | held out (180) |
|---|---|---|
| exact answers | 57, all right | 43, all right (one, "how many days until Christmas", has no answer key: it depends on today) |
| "can't look that up" | 25, all live | 31, all live |
| live questions answered otherwise | 3 (the weather command) | 9 (5 the weather command) |
| question model | 155 (0.29 s median) | 92 (0.29 s median) |
| taken as a command | 3 | 8 |
| nothing (mis-transcribed) | 1 | 6 |

The question model's answers are not graded here; its accuracy is the
benchmark's (above). The live patterns were widened after the first held-out
run missed "who's the prime minister" (the model said Boris Johnson),
"how many euros is a dollar", "what time does Costco close", "did Manchester
United win" and "who won the Super Bowl last year", so the held-out live
numbers are no longer held out. Questions that ran a command:

- **The weather command** for "will it rain this afternoon", "do I need an
  umbrella": fair.
- **`play_music`** for "who played Jack in Titanic", "how many players are on a
  soccer team", "what movies are playing near me": the keyword "play" is
  heard inside "played", "players" and "playing". The library's `play_music`
  now excludes those words.
- **`what_time`** for "what time does Costco close": it now excludes "does",
  "close", "open", "after" and the like, so that one is live and "what time
  is 45 minutes after 3 pm" is answered.
- **The `script` example "good night"** for "how do you say good night in
  Italian": an example said word for word always wins.

The exact questions that were not answered exactly when spoken were
mis-transcribed ("how many ounces are in a pound" heard as "how many ants are
in a pand", "leap year" as "leap here"); they went to the model, which got
some of them anyway.

Since: an ounce beside a volume is a fluid ounce ("how many ounces are in a
cup" is 8; it was not answered); and with normalization, both transcripts and
the above, spoken again: benchmark 57 exact (one wrong: "definitely" heard
as "Defimaty", and spelled), 26 live; held out 47 exact (46 graded, all
right), 33 live.

## A second opinion on a rejected wake word

The Kaldi verifier makes three separate decisions, and they do not fail
together. Measured on the Voice PE taps (AGC and AEC on four devices, as
captured and level-matched, detector cutoff 0.2, adaptive tail), its
thresholds refuse most of a child's real wake words:

| | verified by Kaldi |
|---|---|
| mike (adult male) | 80.4% |
| mike, an earlier session | 61.6% |
| jenn (adult female) | 78.4% |
| oliver (boy) | 71.1% |
| **natalie (girl)** | **32.1%** |

Two of its tests — `prefix` and `last` — ask *is this the wake word or just
speech*. The third, `confusable`, asks *is this "nabu" or "now"*, and that one
needs a pronunciation lexicon.

The command model is already loaded and is good at the first question and bad
at the second: asked whether a true wake word is "okay nabu" or one of eight
rivals, it prefers **"okay now"** on 83-92% of them for three of four speakers.
It has no pronunciation for `nabu` and an overwhelming prior for a common word,
which is exactly what the lexicon entry in `wake.yaml` exists to supply.

So the second opinion is an accept path for `prefix` and `last` rejections
**only** (`voice_call/wake_ratio.py`). A `confusable` rejection stands: that is
Kaldi being right about the thing it is for.

That does not keep confusables away from it. Kaldi applies `prefix` and `last`
first, so a confusable that fails them is never tested as one: of 557 TTS
"okay now" clips the detector fired on, 183 were rejected on `last` or
`prefix`. The score itself has to refuse "okay now".

**The score.** The pre-roll goes through the command model once, and the
log-probabilities are explained two ways: as "okay nabu" with blank before and
after it, and as a free loop over the words spelled by the first 255 pieces of
the vocabulary — the commonest fragments, since SentencePiece numbers its
pieces in merge order. The score is the per-frame log-probability the first
gives up against the second, both measured from the all-blank path. Low means
"okay nabu" explains the window nearly as well as ordinary speech does. The
graph is adapt-ctc's, built from the model's vocabulary and searched with its
Viterbi; nothing else is loaded.

Three choices carry the separation, and all three were measured rather than
assumed: rejected wake words recovered (all five sessions, 3,923 of them) at a
fixed rate of false accepts on each kind of negative, and how many "okay now"
clips pass at the speech/music threshold:

| "okay nabu" spelled | filler | 0.5% speech/music | 0.5% commands | 5% TTS confusables | "okay now" passed |
|---|---|---|---|---|---|
| speech-to-phrase ratio (before) | 63-piece loop | 87.5% | 81.7% | 22.1% | 4.4% |
| any case, breaks anywhere | greedy path | 56.4% | 78.0% | 56.3% | 0.5% |
| any case, breaks anywhere | 255-piece words | 74.1% | 87.3% | 51.7% | 0.5% |
| any case, whole words | 255-piece words | 85.5% | 93.3% | 51.1% | 2.7% |
| lowercase, breaks anywhere | 255-piece words | 86.4% | 97.4% | 68.0% | 1.6% |
| lowercase, whole words | 63-piece words | 90.2% | 97.1% | 52.5% | 4.4% |
| **lowercase, whole words** | **255-piece words** | **91.4%** | **98.5%** | **66.2%** | **3.3%** |
| lowercase, whole words | 1023-piece words | 88.6% | 97.7% | 62.0% | 1.6% |
| lowercase, whole words, + "okay now" | 255-piece words | 92.9% | 99.0% | 1.7% | 97.8% |

- **Lowercase pieces, whole words.** With capitalised pieces allowed, "okay"
  can be the single piece `▁Okay`, which the model writes for every "okay …"
  alike. Spelled in lowercase pieces, each word starting a word and not
  breaking inside it, recovery goes up at each of these rates. Why it
  helps this much is not established; it is what the speech-to-phrase grammar
  happened to do, and the table is the evidence.
- **A filler that cannot spell the wake word as cheaply as the greedy path.**
  Against the greedy path, a window the model reads as "Okay now" — as it reads
  many real wake words — is a poor fit for "okay nabu" however clearly it was
  said. The words of the first 255 pieces stand in for speech without that
  advantage; 63 and 1023 both separate less well.
- **"okay now" is not a spelling.** Adding it recovers the most wake words and
  passes 98% of the "okay now" clips Kaldi rejected on `last`.

Mechanically it is the speech-to-phrase ratio's shape — grammar against free
loop, per frame — with the free loop widened and both sides on adapt-ctc's
lattices, so the numbers are on a similar scale but the thresholds are not
interchangeable.

**Calibration.** `--wake-ratio` sets the threshold (the Wake words page, per
wake word, wins), and a negative value disables it. Measured on the rejections it exists to overturn — every wake word
Kaldi refused on `last` or `prefix`, 3,923 across five sessions — and on 1.5 s
windows that should stay refused: 14,288 of the speech-and-music recording,
12,446 of the command right after each wake word (those Kaldi rejected on
`last`/`prefix`), and 1,603 TTS confusables the detector fired on and Kaldi
rejected on `last`/`prefix`, 183 of them "okay now". The earlier session of
mike was not used to choose the score.

| threshold | jenn | mike | natalie | oliver | mike, earlier | speech/music | commands | TTS confusables | TTS "okay now" |
|---|---|---|---|---|---|---|---|---|---|
| 1.10 | 68.7% | 78.8% | 86.1% | 86.0% | 72.8% | 0.06% | 0.16% | 8.4% | 1.1% |
| 1.20 | 81.3% | 89.2% | 90.7% | 92.9% | 83.3% | 0.17% | 0.19% | 14.8% | 3.3% |
| 1.25 | 85.6% | 92.9% | 92.1% | 94.6% | 87.5% | 0.35% | 0.21% | 20.1% | 3.3% |
| **1.30** | **89.8%** | **94.6%** | **93.5%** | **97.1%** | **90.3%** | **0.69%** | **0.23%** | **27.5%** | **4.4%** |
| 1.35 | 93.8% | 96.1% | 94.8% | 98.9% | 92.9% | 1.21% | 0.27% | 34.7% | 5.5% |
| 1.40 | 95.8% | 96.5% | 95.9% | 99.1% | 94.9% | 2.06% | 0.29% | 43.4% | 8.2% |
| 1.50 | 99.6% | 99.0% | 97.6% | 99.4% | 97.8% | 4.88% | 0.45% | 56.5% | 13.7% |
| speech-to-phrase, 1.20 | 82.0% | 92.5% | 94.2% | 96.6% | 87.5% | 0.83% | 1.20% | 54.0% | 4.9% |

The default of 1.3 takes fewer false accepts than the speech-to-phrase ratio
did on every kind of negative, and recovers as many wake words or more for
every speaker but Natalie (93.5% of her rejections against 94.2%). 1.35 recovers
more than the old ratio for everybody, at 1.21% of speech/music windows. The
quiet-room recording (9,216 windows) is at 0.00% up to 1.35.

At the same false-accept rate, on identical windows:

| negatives | false accepts | speech-to-phrase | this |
|---|---|---|---|
| speech/music | 0.5% | 86.4% | 91.4% |
| speech/music | 1% | 91.1% | 93.8% |
| commands | 0.5% | 81.7% | 98.5% |
| commands | 1% | 89.5% | 99.1% |
| TTS confusables | 1% | 10.0% | 40.5% |
| TTS confusables | 5% | 21.0% | 66.2% |

Against the command said right after the wake word, the gap between the 90th
percentile of wake words (every detection) and the 10th of commands is 1.6 to
3 times as wide:

| speaker | wake p90 | command p10 | separation | speech-to-phrase |
|---|---|---|---|---|
| jenn | 1.290 | 2.147 | +0.857 | +0.395 |
| mike | 1.117 | 2.220 | +1.103 | +0.417 |
| mike, earlier | 1.213 | 2.204 | +0.991 | +0.324 |
| natalie | 1.126 | 1.961 | +0.835 | +0.526 |
| oliver | 1.134 | 2.053 | +0.919 | +0.367 |

What reaches the command, of every detection:

| | Kaldi | + speech-to-phrase ratio | + this ratio |
|---|---|---|---|
| jenn | 78.4% | 89.7% | **90.8%** |
| mike | 80.4% | 94.6% | **94.9%** |
| mike, earlier | 61.6% | 91.5% | **92.4%** |
| natalie | 32.1% | **91.6%** | 91.1% |
| oliver | 71.1% | 93.4% | **93.5%** |

The spread across speakers goes from 48 points to 4.

The on-device detector fired 22 times at cutoff 0.2 across every channel of the
speech-and-music and quiet-room recordings (88 channel-hours). Kaldi refused all
22, 21 of them on `last` or `prefix`, and neither ratio overturned any.

**What still gets through** is the TTS confusables closest to the wake word.
Of clips the detector fired on and Kaldi rejected on `last`/`prefix`, it
overturns 112 of 244 English "okay maybe" (the old ratio 201), 98 of 195
"naboo naboo" (135) and 69 of 104 German-accented "okay natalie" (103). These
are clean synthetic voices at the microphone, not room captures, but they are
the class to watch: none of it is the `confusable` test's to catch, because
Kaldi never gets that far.

It costs one forward pass over the 1.5 s pre-roll on the model the commands
already use: 15 ms on 4 threads on x86 (32 ms on one), of which the graph
search is 0.1 ms. To reproduce every table here:

```bash
scripts/calibrate_wake_ratio.py --wake-binary ~/opt/mixednet-executor/run_okay_nabu \
    --out /tmp/wake_ratio.jsonl --workers 8      # about 26 minutes
scripts/calibrate_wake_ratio.py --report /tmp/wake_ratio.jsonl
```

### When the wake word runs off the end of the pre-roll

The pre-roll ends **at** the detection, so a detector that fires before the
speaker has finished leaves the last phoneme outside the window the verifier
sees. The aligner then has nowhere to put the end of the phrase but the edge,
and `theta_last` is applied to a truncated word.

How often that happens is a property of the speaker, not of the setting. At
cutoff 0.2 the split lands on the pre-roll edge for 68.8% of one adult's
detections and 8.2% of another speaker's.

Adding a fixed tail of audio from after the detection fixes it for the first
and costs the others, because for them it only adds command audio the verifier
has to explain:

| | +0 ms | +100 | +200 | +300 | +500 | pinned at +0 |
|---|---|---|---|---|---|---|
| jenn | 86.5% | 90.9% | 93.6% | **94.2%** | 94.9% | **68.8%** |
| mike | 94.6% | 93.2% | 93.9% | 93.2% | 92.0% | 26.1% |
| natalie | 90.3% | 91.4% | 91.2% | 91.2% | 91.2% | 8.2% |
| oliver | 92.9% | 92.6% | 89.4% | 90.1% | 87.3% | 13.1% |

But the verifier says which case a call is: that is what the split landing on
the edge means. So the window is extended and re-decoded **only then**
(`--wake-tail`, default 0.3 s, 0 to disable), and only those calls pay for it:

| | +0 ms | +300 always | adaptive | retried |
|---|---|---|---|---|
| jenn | 86.5% | **94.2%** | 90.3% | 68.8% |
| mike | 94.6% | 93.0% | **94.4%** | 26.1% |
| natalie | 90.3% | 91.2% | **90.3%** | 8.2% |
| oliver | 92.9% | 90.1% | **93.6%** | 13.1% |

Always-on gives the truncated speaker twice as much and takes 1.6 and 2.8
points from two others. Retrying on a pinned split gives her +3.8 with nobody
worse — and after it the pinned rate falls from 68.8% to 0.3%, which is the
whole mechanism.

It costs latency, not accuracy: a second Kaldi decode, plus the wait for audio
the device has not sent yet, on the calls that trigger it. The retry is skipped
rather than blocking if that audio never arrives.

The split is measured from the start of whatever window was decoded, so the
retry moves it; `call.split_bytes` carries the offset.

### What is left

Nearly all of it is `confusable`, which is the one class deliberately left
alone — for Jenn, `okay now`. That is a real precision/recall trade rather than
a bug. The lever would be a per-confusable margin (a higher bar for `now` than
for `naruto`), which is a change in wyoming-wake-kaldi: `theta_competitor` is
global today.

## Thresholds

Both gates are deliberately strict and both are calibrated for a **real voice
at the device**:

- `wake.yaml` `theta_competitor: 0.10` rejects near-homophones ("okay natalie")
- adapt-ctc's `span_gate` (2.5 nats per token) and `max_cost` (10), tuned on
  Voice PE recordings and the office-multicap corpus; override them under
  `settings:` in `commands.yaml`. (`--max-score`, speech-to-phrase's per-token
  gate, is gone with it.)

Playing a recording through loudspeakers across a room degrades the audio
enough that both gates reject it even when the text decodes correctly. That is
the gates working, not a bug; raise them only for bench testing.

Being calibrated for one kind of voice is also how they came to refuse most of
a child's real wake words, which is what the second opinion above is for. Do
not raise `theta_last` to fix that: it is the test doing its job on audio the
thresholds were never fitted to, and loosening it loosens it for everyone.

### Pruning a confusable

A confusable earns its place only if the **satellite** fires on it. The
detector is the first filter, so an entry it already blocks can do nothing but
cost recall. Fed straight to micro_wake_word with no room in between and the
level swept:

| phrase | peak | |
|---|---|---|
| okay nabu | 0.95 | the control |
| **okay now** | **0.59** | clears the 0.50 cutoff — keep it |
| okay natalie | 0.30 | |
| okay naruto | 0.15 | |
| **okay no** | **0.08** | below the 0.13 the detector reaches on television |
| turn on the lights | 0.01 | |

`okay no` was removed on that basis. It was the only rival beating "okay nabu"
for the adult woman in the corpus — all 17 of her confusable rejections on one
VPE channel were this one entry — and dropping it is worth 8 points of
verification to her and nothing to anybody else.

`okay now` is the opposite case and stays. It is the one rival that genuinely
reaches the verifier, and it is also what the recognizer mistakes the wake word
for 83-92% of the time, which is why the second opinion is not allowed to
overturn a `confusable` rejection.

Test an entry before removing it. Two minutes with a TTS voice and the detector
settles it, and the recordings in `false positives/` ("okay natalie", "okay
naruto", "okay neighbor") are real false wakes that show the family is not
hypothetical.

## Memory

Almost all of it is one file. Measured per startup stage on the Pi:

| stage | resident |
|---|---|
| imports + Home Assistant + wake model lowering | 78 MB |
| Kaldi wake verifier | +2 MB (`decoderd` is a separate process) |
| **speech-to-phrase (Parakeet CTC)** | **+862 MB** |
| hassil, FST grammars, Piper | +1 MB (the 129 MB of grammars are mapped, not resident) |

(Measured with speech-to-phrase. adapt-ctc loads the same int8 CTC model once,
and the grammars are gone; EmbeddingGemma 2, when loaded for hints, is
another model on top. These have not been re-measured on the Pi.)

`--no-tdt` only drops the TDT transducer, so it barely moves the total.
The encoder that always loads is the one to shrink:

```bash
SRC=models/speech-to-phrase/stt_en_parakeet_tdt_ctc_110m
.venv/bin/python scripts/quantize_tdt.py --src $SRC/model.onnx --dst $SRC-int8/model.onnx
cp $SRC/tokens.txt $SRC/MODEL_CARD.md $SRC-int8/
```

The app prefers `-int8` when it exists. On the Pi that is 1029 -> 519 MB peak
and 890-1357 ms -> 569-1212 ms per recognition, with all nine saved calls
choosing the same sentence and slots.

Dynamic int8 picks different kernels per architecture, so verify on the machine
you will run on: the same models on x86 agreed on 11 of 12, degrading
`play a moon shaped pool by radiohead` to `...by radiohe`, which the Pi did not
reproduce. (`scripts/compare_recognizers.py` did exactly this check; adapt-ctc's
`python -m adapt_ctc.score --model DIR` does it now.)

## Recording more captures

The captures in `corpus/positives/` came from Home Assistant Cloud (Azure) TTS
played at the device. Piper cannot be used for this: none of six voices reached
micro_wake_word's cutoff, at any level. Azure woke the device on 29 of 44.

Two things matter. Render levels must match a capture known to work -- TTS
comes out near full scale and the clipping alone stops detection, so
`make_cloud.py` normalizes to the reference peak of 1226/32767. And the wake
verifier has to be relaxed during collection (`theta_*` in `wake.yaml`), since
it rejects most TTS wake words while the device itself fires; the command audio
is unaffected by those thresholds. Put them back afterwards.

A rejected wake word now saves its audio as `rejected-{call,command}-*.wav`.
Those command clips are truncated -- the call hangs up while the speaker is
still talking -- so they are for diagnosing rejections, not for use as
positives.

## Running on a Pi 4 (aarch64)

Three dependencies are native and must match the architecture; everything else
(the ONNX models, Piper, the Python code) copies straight across.

| piece | |
|---|---|
| Kaldi + OpenFST binaries | not portable. wyoming-wake-kaldi ships prebuilt `local/tools` per arch; point `models/wake-kaldi-tools` at the aarch64 copy |
| `verify_decoderd` | build with `decoderd/build.sh <tools_dir>`, or symlink an existing aarch64 build |
| adapt-ctc `_viterbi` | a C extension (limited API), built in place with `python setup.py build_ext --inplace`; without it a numpy fallback gives the same scores several times slower |

(speech-to-phrase's `_fst`, an OpenFST extension that needed `libfst-dev` 1.8,
is only needed for the benchmarks of the old chain now.) Then:

```bash
rsync -a --exclude .venv --exclude var --exclude 'models/wake-kaldi-tools' \
      --exclude 'models/wake-kaldi-decoderd' voice_call/ pi:~/voice_call/
ssh pi 'cd ~/voice_call && python3 -m venv .venv &&
        .venv/bin/python -m pip install -r requirements.txt &&
        .venv/bin/python -m pip install -e _src/wyoming-wake-kaldi &&
        .venv/bin/python setup.py build_ext --inplace'
```

## Benchmark

```bash
.venv/bin/python -m scripts.benchmark --runs 5
```

Times each stage against a fixed WAV. `--window` sets how much pre-roll goes to
the verifier (default 1.5 s, matching the device); wake-verify cost is linear in
that, so it is the main latency knob.
