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
3. **endpointing** — `pysilero-vad` watches the command audio for trailing
   silence;
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
"Responses" on the Commands page; `specific` unless set: "Turned on the
lights in the Office" says where, which "Turned on the lights" can't).
Home Assistant's default does the same with the brief sentence. Templates see
`place` too (`said`: "in the Kitchen", "upstairs"; `here`; `areas`), and a
rendered response is tidied of the gap an empty variable leaves. Overrides can
set a level per area or satellite.

## Layout

| path | |
|---|---|
| `voice_call/protocol.py` | the device wire format |
| `voice_call/mixednet.py` | lowers a wake word `.tflite` into the device's blob |
| `voice_call/_mixednet_compiler.py` | vendored from mixednet-executor; edit upstream |
| `voice_call/wake.py` | Kaldi wake verification and split point |
| `voice_call/wake_words.py` | the wake words a satellite can listen for: built in and verified, or custom; their cutoffs and placement phrases |
| `voice_call/endpoint.py` | silero VAD endpointing |
| `adapt_ctc/` | vendored from adapt-ctc (commit 07ef328) plus value lists, bounded free text, examples first, descriptions and structured rejections; its own README and tests (`tests/adapt_ctc_tests/`) |
| `voice_call/engine.py` | where the command starts, recognition, scope, free text from TDT, hints |
| `voice_call/commands.py` | the command schema: scope, overrides, presets, what happens |
| `voice_call/store.py` | `commands.yaml`, `satellites.yaml`, `settings.yaml` |
| `voice_call/actions.py` | variables, `execute_script`, the response, the `voice_call_command` event |
| `voice_call/calls.py` | per-call traces, saved audio, debug sessions |
| `voice_call/web.py`, `voice_call/web/` | the web UI and its API |
| `voice_call/library/` | example commands offered in the UI |
| `voice_call/web/icons.js` | the Material Design Icons the UI shows (Apache-2.0), from `scripts/make_icons.py` |
| `voice_call/hass.py` | Home Assistant over one websocket: registries, scripts, templates, events |
| `voice_call/tts.py` | Piper, and the acknowledgement fallback |
| `voice_call/server.py` | the call state machine |
| `voice_call/speaker.py` | speaker ID, profiles, enrollment; `python -m voice_call.speaker` |
| `voice_call/integration.py` | the Voice Call integration from the app's side: set-up, satellites, following timers |
| `voice_call/music.py` | music commands: which Music Assistant player, reading "play …", searching, controls |
| `voice_call/voices.py` | Home Assistant STT and TTS entities in place of TDT and Piper |
| `voice_call/fallback.py` | when nothing matched: the closest command by meaning, then a small LLM (Granite 4.0 350M or Hammer 2.1 0.5B) with the commands as tools -- each only for the commands chosen for it |
| `voice_call/skills/` | skills: questions answered without an LLM, one module each (spelling, dates, clock, units, arithmetic, live), on or off for the home or a satellite |
| `voice_call/questions.py` | questions no command or skill took: a small LLM |
| `scripts/bench_questions.py` | the smart-home quick-questions benchmark through the app, spoken or as text |
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
| `scripts/fetch_music_library.py` | enumerate Music Assistant artists/albums for a closed-vocabulary recognizer |
| `voice_call/wake_ratio.py` | the second opinion on a rejected wake word |
| `scripts/align_sessions.py` | where the wake word is in every prompt of a session |
| `scripts/false_accepts.py` | how often the chain fires on audio with no wake word in it |
| `scripts/synth_wake_words.py` | wake words and near misses spoken by every Piper English voice, in rooms and noise |
| `scripts/bench_wake_words.py` | a wake word through both stages on labeled clips, at several cutoffs; sweeps thresholds afterwards |
| `scripts/check_wake_words.py` | whether custom wake words lower for the device, match TFLite and hear their own phrase |
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

(Before the commands were the app's own, this bench ran speech-to-phrase on the
sentence triggers instead; the `tests/bench_*.jsonl` results and
`tests/bench_corpus_report.txt` are from that chain.)

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

The device copies the small tables out and points the op weights straight into
the received buffer, so the ~45 kB that matter are never duplicated. Detection
settings travel with it as JSON, read from the model's microWakeWord manifest:

| field | |
|---|---|
| `probability_cutoff` | per-slice threshold, 0.0--1.0; per wake word (below); `--wake-cutoff` overrides the verified ones |
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

Each satellite listens for one or more wake words: its own pick (Satellites
page, `satellites.yaml`: `wake_words: [...]`), else every satellite's
(`settings.yaml`), else `--wake-model` (default `okay_nabu`). The app pushes
the VAD gate and the picked models on `HELLO`, clearing whatever the device
held first, and again when a pick changes -- never during a call, since
clearing stops the detector's microphone. The firmware has six model slots and
says so in `HELLO` (`model_slots`): the VAD gate, "stop" while a timer rings,
and up to four wake words. Older firmware has four, so two.

The device reports the model's id as the wake word, which decides which
verifier, second opinion and placement phrases apply (`voice_call/wake_words.py`).

| | built in, verified | custom |
|---|---|---|
| where | `models/wake`, entry in `wake.yaml` | `wake_words/` (as an app: `/config/wake_words`), or uploaded in the UI |
| first stage | low cutoff, per word | the manifest's cutoff |
| Kaldi verifier | yes, with confusables | no: the detection stands |
| second opinion | yes, per-word threshold | no |
| where the command starts | wake phrase found in the CTC evidence | the same, from the manifest's `wake_word` text |

Each verified wake word in use costs its Kaldi graphs and two resident
decoder processes, about 200 MB, so they start when a satellite first listens
for the word, not at startup.

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
| `enrolling` | `speaker`, `needed`, color -- after "learn a new voice for ..." |
| `speaker_enrolled` | `speaker`, color -- once enough of their voice is in |

Benchmarked on recorded VPE audio of four speakers (whosaidit): ~1% EER on AGC,
~0.5% on AEC; at 0.45, about 98% of household commands are accepted while 0.5%
of an un-enrolled speaker's in the same room get through.

**Enrolling.** Say *"okay nabu, learn a new voice for Natalie"*. The app handles
that itself (it never reaches Home Assistant), replies, and learns Natalie's
voice from the next five commands on that satellite -- which run normally.
Meanwhile the ring shows the color she is getting. Commands that do not sound
like the rest (someone else stepping in) are left out, as is anyone already
enrolled and recognized with confidence, so it waits for five consistent ones;
after ten minutes an unfinished enrollment lapses. Saying it for an existing
name re-enrolls them and keeps their color. Colors come from a fixed list of
hues that stay distinct on the ring, skipping red (error) and the default cyan
(nobody recognized).

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
has how, and how well on the office-multicap corpus), with four additions made
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
  matched.
- **Value lists** (`lists:`) report which alternative of a group was heard, as
  a value: `{color: red}` for "crimson".
- **Bounded free text.** A wildcard may need both edges (`bounded`: after
  "add", before "to"), or run from a keyword to the end (`to_end`), and may be
  `required`; fillers may then sit inside it ("a bag of rice") but not at its
  edges. Its text is re-read from the TDT transcript over the same frames,
  which spells better than the CTC head.

When nothing matched, the closest command is named. From the keyword evidence
first: a command whose keywords, names and numbers cover at least half the
words that are not fillers, with what it lacked (`IntentResult.missing`). Or
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
entity: called from the engine's thread, it posts the command audio to
`/api/stt/<entity>` on the event loop (there is no websocket STT outside a
pipeline) once, and only when asked -- keyword commands never wait for it --
and falls back to TDT on failure. STT text has no word times, so free text is
found by the wildcard's own keywords in the transcript (`engine.free_text`:
after an `after` keyword to the end or to a `before` keyword, or as many
words before a `before` keyword as the CTC head heard there). The language is
`stt_language` (`de` matches the entity's first `de-*`), else the entity's
best match for English (`stt/engine/list`); TTS has `tts_language` the same
way, and a satellite may say `auto` to override the home's language with the
automatic one.

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
Satellites page can always pick one.

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

## When nothing matches: meaning, then GLiNER or an LLM

`voice_call/fallback.py`. After the CTC matcher (keywords and examples), in
order, each only when the one before found nothing -- and each only for the
commands chosen for it (`fallback: [meaning, llm]` on the command; ticked on
its page, or many at once on the Commands page). None are chosen to begin
with, the library's included: a command is run this way only when someone
decided it may be.

1. **Meaning.** EmbeddingGemma (already loaded for hints) names the closest
   command; it is *picked* only when it is chosen for meaning, the similarity
   is at least 0.80, and at least 0.05 ahead of the runner-up (another
   command, chosen or not, or an everyday request that is not one -- never
   another combination of the same command: only a command's first
   combination carries its description and examples). A command that hears
   nothing runs; for one that hears something, the model hears what was said
   in it (GLiNER: only that command's entity types, no classification; an
   LLM: a call with that one tool), and that picks the combination --
   nothing heard is the empty one, when it has it. Without the model, meaning
   runs only commands that hear nothing. `settings.yaml` `fallback_meaning`,
   on by default.
2. **The model step** (`fallback_model`; the commands chosen for `llm`):
   GLiNER2.5 small by default (below), or a small LLM -- Granite 4.0 350M
   (Apache-2.0, already there as the question model) or Hammer 2.1 0.5B (Qwen
   2.5 0.5B tuned for function calling, CC-BY-NC-4.0), Q8_0 in llama.cpp. What
   it picks is checked like a match: the command must be chosen and available
   on the satellite, names map back to ids, list values to values, and a
   name, value, number, duration or free text the sentence does not say is
   dropped (a small LLM fills in arguments nobody gave: "kill the lights"
   came back with a bedroom and a floor, "start the coffee maker" with a
   5-minute timer). A required one that is missing or wrong refuses it, and
   the miss is hinted from the command picked. `fallback_llm`, on by default
   (it loads nothing until a command is chosen for it); `fallback_top_k`.
3. **The hint**, as before.

**GLiNER** (`voice_call/gliner.py`): GLiNER2.5 small (fastino, 74M
parameters, Apache-2.0), an encoder that in one pass classifies a sentence
among labels and finds spans of named types in it. The labels are the
commands' descriptions plus "something else: a question, small talk or
thanks"; the types are their parameters (a room, a device, music, something
to buy, a duration, a percentage, a value from a list, ...). One label per
command, whatever its combinations: what it hears in the sentence picks the
combination ("lights out in the bedroom": turn off the lights, with an
area). It picks only among commands that hear something -- meaning takes
the rest. It is given the 8 (`fallback_top_k`) of those closest in meaning
to the sentence, *chosen or not*: alone, a command is taken for nearly
anything ("never mind" played music at 0.87), and the others are what it
tells the chosen ones from. Its pick runs only when it is chosen, at least
`fallback_threshold` sure (0.3), ahead of "something else", and what it heard
is exactly one of the command's combinations (a room and a floor for a
command that takes either is refused). Hearing nothing in it ("lights out"),
there is nothing to check the pick by, so it runs only when meaning puts the
same command first. The same words found as two types ("living room" as a
room and as a floor) count once, as the surer type the command can take.
There is no prompt prefix, so how many are chosen costs nothing.

It runs without PyTorch: onnxruntime, numpy and the tokenizers library
(about 50 ms a sentence on a desktop, 0.22-0.28 s on a Raspberry Pi 5 with 4
threads; 424 MB at peak with it loaded, of which ~110 MB is the tokenizer).
The networks are exported once by `scripts/export_gliner.py` (it needs
torch and the gliner2 package, in a venv of its own) into
`models/gliner2.5-small-v1-onnx` (70 MB), which `script/stage_build` copies
into the image: the encoder with 4-bit weights (MatMulNBits; int8 with
quantized activations breaks DeBERTa-v3 -- not a command right), and the
boundary head split in two around the candidate spans, which are chosen in
numpy (it branches on sizes and needs a stable sort, which ONNX lacks). The
rest of what the gliner2 package does -- the prompt, word splitting,
decoding -- is ported. Against the package, on 131 of the benchmark's inputs:
identical tokens, positions and entities, probabilities within 3e-6.

**An LLM instead.** Its tools are the commands' descriptions plus how they
are said (their examples, else the first keyword of each required group).
The music commands' descriptions were sharpened for it ("play an artist, album, song, playlist or
genre", "play more music like what's playing"): Hammer could not tell
"play music" from "play music like this".

**A way out, for Granite.** Granite calls *some* tool for almost any
sentence: "what's the capital of France" ran `what_time`, "never mind" went
on the shopping list. It is given one more tool, `not_a_command` ("a question
about the world, small talk, thanks, or something none of the other tools
does"), and asked to call that instead of nothing; that halves the wrong
commands it runs. Hammer, trained to answer an empty list, does slightly worse
with it, so it is not given one.

**Close in meaning, too.** What the LLM picks must also be close in meaning
to the sentence (EmbeddingGemma, as for hints): 0.65, or 0.70 when the call
takes nothing from the sentence -- there is nothing else to check it by.
"What's the capital of France" played as music came in at 0.48, "thank you"
as the time at 0.65; every right pick on the benchmark was at 0.678 or more.
Applied to the runs below it would have refused about half of Granite's wrong
commands (15 to 7 with 8 chosen; 11 to 7 and 16 to 10 with every library
command) and a third of Hammer's (9 to 7; 15 to 10 and 5 to 5), and none of
the right ones -- tuned on these same 64 sentences, so the margin is thin. A
pick refused this way gives no hint ("it looks like you're trying to..."),
and the call's summary says what was picked and how close it was.

The similarity is to the command's description and examples, so a thin
description costs: "I want to hear something corporate" is 0.61 from a
`play_music` described as "play music", refused, and 0.67 from the library's
"play an artist, album, song, playlist or genre"; "never mind" is 0.61 and
0.60. (Granite's query there was "corporate": the sentence, all lowercase,
reads as "something that is corporate". Given "Something Corporate" it takes
the band.)

**Every chosen command, or the closest few.** With no more chosen than
`fallback_top_k` (8), the LLM gets them all, in a fixed order, so the
system-and-tools prefix is evaluated once and reused by every request (it
gets every chosen command whatever the satellite; availability is checked
after). With more, only the closest 8 by meaning (`Recognizer.rank_by_meaning`)
that the satellite may run, evaluated each time.

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
| Granite 4.0 350M | 21/32 | 26/32 | 7 | 0.42 s |
| Hammer 2.1 0.5B | 19/32 | 28/32 | 6 | 0.38 s |

Every library command chosen (56):

| model step | right | non-commands refused | ran a wrong command | model step, median |
|---|---|---|---|---|
| GLiNER small, 0.2 | 67% | 10/10 | 3 | 0.05 s |
| GLiNER small, 0.3 | 66% | 10/10 | 2 | 0.05 s |
| GLiNER small, 0.4 | 63% | 10/10 | 2 | 0.05 s |
| Granite, closest 8 | 63% | 10/10 | 9 | 1.51 s |

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
command, so the default stays at 0.3. Neither LLM tells commands apart as
well. What none does well is infer: "we need milk", "it's too dark in
here".

The LLM alone, on the sentence as text (`--mode llm`: no meaning step, and
no similarity check on its pick), Raspberry Pi 5, 4 threads. The same eight
commands chosen:

| | its commands right | refused when it should be | ran a wrong command | per sentence | once |
|---|---|---|---|---|---|
| Granite 4.0 350M | 18/32 | 18/32 | 15 | 1.0 s (p90 1.1) | 7 s prefix |
| Hammer 2.1 0.5B | 16/32 | 26/32 | 9 | 0.35 s (p90 1.4) | 7 s prefix |

Every library command chosen (56):

| | commands right | non-commands refused | ran a wrong command | per sentence | once |
|---|---|---|---|---|---|
| Granite, every command | 41% | 7/10 | 11 | 2.1 s (p90 2.8) | 70 s prefix |
| Granite, closest 8 | 52% | 4/10 | 16 | 6.3 s (p90 8.7) | |
| Hammer, every command | 43% | 9/10 | 15 | 1.7 s (p90 2.0) | 45 s prefix |
| Hammer, closest 8 | 59% | 10/10 | 5 | 4.5 s (p90 6.2) | |

Neither is ahead everywhere. Granite gets about as many right; Hammer is
the more careful with a few commands chosen (9 wrong runs against 15) and
with the closest 8 (5 against 16, every non-command refused), Granite with
every library command (11 against 15). Without the way out Granite ran a
wrong command for 29 of the 64 sentences (8 chosen). Choose for the LLM the
commands it would do little harm to run by mistake. Earlier, on a
desktop (Ryzen 9 5950X), Hammer alone: 43% with every command (0.57 s, 10 s
prefix) and 61% with the closest 8 (1.15 s); every non-command refused.

The whole chain, as a call runs it (`--mode chain`: Piper speaks the
sentence, then CTC, meaning, Hammer), desktop, every command chosen:

| chain | commands right | decided by CTC (right) | by meaning | by Hammer (right) | median |
|---|---|---|---|---|---|
| CTC alone | 31% | 18 (17) | | | 0.09 s |
| + meaning | 35% | 17 (15) | 4 (4) | | 0.11 s |
| + Hammer, closest 8 | 61% | 18 (17) | | 21 (16) | 1.12 s |
| + meaning + Hammer, closest 8 | **67%** | 18 (17) | 4 (4) | 21 (15) | 1.10 s |
| + Hammer, every command | 56% | 18 (17) | | 27 (13) | 0.42 s |

Every non-command was refused by every chain. Meaning never ran a wrong
command. What neither LLM does well is infer: "we need milk", "we're out of
coffee", "it's too dark in here" mostly come back as no call, or the wrong
one.

**llama.cpp and pysilero-vad** each ship a `libggml` under the same soname,
and the first one loaded serves both; only llama.cpp's has everything both
need, so `voice_call/__init__.py` imports `llama_cpp` first.

## Trying typed text

The Try page (`/api/try`) skips speech recognition: the typed text is the
transcript, and the CTC matcher reads evidence made from it
(`Engine.recognize_text`, adapt-ctc `Vocabulary.text_log_probs`). Its spoken
words (`adapt_ctc.text.spoken_words`: "10" is "ten", "&" is "and") are
spelled in the model's own SentencePiece pieces (the fewest that spell each
word), each piece certain for two frames, with blanks between pieces and
words and around them. Keywords, names, numbers and example sentences then
match as if the sentence had been heard perfectly, through the same code as
audio; the text itself stands in for the TDT head (`TypedText`), so example
lookups, free text ("Kind of Blue by Miles Davis", as typed), skills and
questions read it as written. All 58 example sentences in the library match
their own command this way. A word no pieces spell (outside the model's
alphabet) is left out, and listed in the trace.

With `speak` (the "Say it first" box), the old way: Piper says it, and it is
recognized from the audio like a call -- to test speech recognition too.

## Skills and questions

Ported from llama.cpp's smart-home-qa-bench (`router.py`, and its model
results). When no command is meant -- after the CTC matcher, and around the
command fallback -- a question still gets an answer:

1. **Skills** (`voice_call/skills/`, one module each, the Skills tab): spelling,
   dates, clock arithmetic, unit conversions, arithmetic -- grammars that
   must parse the whole question *and* compute an answer, or let it fall
   through. Before the command fallback: they are instant and never wrong
   when they answer. Spelling first ("how do you spell weather" is not about
   the weather); the others only for a question that is not about now or
   here.
2. **The live skill**: anything about now or here (weather, news, scores,
   prices, opening hours, "tonight", "last night") gets "Sorry, I can't look
   that up." After the command fallback, so "do I need an umbrella" can
   still be the weather command.
3. **The question model**: the rest, only when it reads as a question (a
   question word first, or a question mark) -- "thank you" and "I'm cold"
   are not asked, nor is a live question, with the live skill off or on --
   goes to a small LLM, answered in one short sentence. `questions_llm`, on
   by default; `questions_model`: Granite 4.0 350M (Apache-2.0, 370 MB,
   downloaded on first start) or Gemma 3 1B (for a faster machine). The same
   llama.cpp as the command fallback.

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
description, examples, icon, `answer(text) -> sentence | None`) in a module
named after its id, listed in `SKILLS` (`skills/__init__.py`) in the order
they are asked.

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

## History: the sentence-trigger chain

Until the commands became the app's own, a command was decoded by
speech-to-phrase against a grammar built from Home Assistant's `conversation`
sentence triggers, sent to `conversation.process`, and answered with the
automation's `set_conversation_response`. The sections from here through
"Anchored matching", and "Closed-vocabulary backends" and "Rejection
confidence" below, record what was measured on that chain. Its code
(`voice_call/recognize.py`, `wildcards.py`, `fst_hybrid.py`, `tdt.py`) and the
scripts that measured it are in git history before the commands moved in.

## Wildcard sentence triggers

A trigger like `play {song} in the {room}` cannot go in the speech-to-phrase
grammar: Home Assistant treats **every** list reference in a conversation
trigger as a wildcard ([`default_agent.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/conversation/default_agent.py) — "Assume slot list references
are wildcards"), so the slot matches arbitrary speech and cannot be enumerated.

Triggers are split on whether they contain `{...}`:

| | matched with | |
|---|---|---|
| plain | speech-to-phrase grammar | constrained, scored, rejects anything else |
| wildcard | hassil, against the Parakeet transcript | same matcher Home Assistant uses |

The grammar is tried first. On rejection the unconstrained transcript goes to
hassil; a match means the transcript is sent to `conversation.process`, which
re-matches it and fills the slots when the trigger fires. No match ends the
call. Configurations with only wildcard triggers work — the grammar is simply
disabled and every command goes down the transcript path.

## What was actually said

The grammar decode can only ever return one of the trained sentences, so a
rejected command tells you nothing about what the person wanted. On rejection
the app also runs a plain greedy CTC decode of the same audio -- Parakeet with
no grammar at all -- and reports it as `Recognition.greedy_text` and in the
`not_recognized` event:

```
spoken  : 'play some jazz in the kitchen'
  grammar : 'turn on the lights'  score=22.80  accepted=False
  parakeet: 'Play some jazz in the kitchen.'
```

That distinguishes "out of vocabulary" from "misheard", gives you the text to
paste into a new sentence trigger, and is what wildcard triggers match against.
It is free: `speech_to_phrase.Result.greedy_text` comes from the same forward
pass as the grammar decode. Disable with `greedy_on_reject=False`.

## Second pass (Parakeet TDT)

speech-to-phrase ships only the **CTC head** of parakeet-tdt_ctc-110m. Decoded
greedily at 80 ms frames it mixes subword paths -- the model hedges between
`'P'`+`'l'`+`'ay'` and `'play'`, and greedy walks across both, yielding
`Pplayay`, `arrtist`, `cororporate`. That text is what wildcard triggers match
against, so the artifacts cost real matches.

When the grammar decode fails, the audio is re-transcribed with the **TDT
transducer head** of the same model (`models/parakeet-tdt`, sherpa-onnx). That
is the decoder NVIDIA's published accuracy refers to. Measured on 8 synthesized
commands, wildcard matches went **3/8 (CTC greedy) -> 6/8 (TDT)**.

It runs only on the failure path, and **sequentially**, not beside the CTC
pass. On a 4-core Pi 4 one ONNX pass already saturates the cores:

| tdt threads | tdt alone | sequential | concurrent |
|---|---|---|---|
| 1 | 711 ms | 1087 ms | 908 ms |
| 2 | **448 ms** | **883 ms** | 934 ms |
| 4 | 528 ms | 939 ms | 1219 ms |

The best configuration is 2 threads, run sequentially. Speculating in parallel
never wins, and would also add ~550 ms to every *successful* call.

The encoder is quantized to int8 (`scripts/quantize_tdt.py`, dynamic, MatMul
only). That is a win on every axis, so `models/parakeet-tdt-int8` is preferred
automatically when present:

| | fp32 | int8 |
|---|---|---|
| encoder on disk | 456 MB | **172 MB** |
| full stack RSS on the Pi | 1440 MB | **1000 MB** |
| decode, Pi 4, 1.65 s audio | 433 ms | **316 ms** |
| transcripts | — | 7/8 identical |

With ~1650 MB available on the Pi, that leaves real headroom. `--no-second-pass`
still disables it.

## Wildcard sentences: grammar selection

Matching a transcript cannot tell `play the artist X` from `play the album X`.
The two differ by one word, that word is exactly the one a decoder gets wrong,
and it selects the action. So the sentence is chosen by **decoding**, not by
string matching.

Each wildcard trigger is compiled into its own grammar: literals pinned to what
the trigger says, each `{slot}` a free loop over the whole token alphabet. All
of them decode the same (single) acoustic pass, and the cheapest wins:

```
spoken 'play the artist radiohead'   artist 12.70   album 52.96  -> artist
spoken 'play the album radiohead'    artist 52.98   album 10.75  -> album
```

Three details are load-bearing:

* **`beam=10`.** The slot loop makes the search enormous. Exact search costs
  ~9 s per decode; beam 10 gives an identical answer in ~20 ms.
* **Slot penalty (`6.0` per slot-emitted token).** A free loop may pick any
  tokenization, so it fits the audio at least as well as a required literal and
  the most permissive sentence wins by default. Charging per slot token
  restores the preference for sentences whose literals explain the audio.
* **Token-level slot extraction.** Splitting the decoded *text* does not work:
  the grammar pins "play" but the following loop may take its trailing
  subwords, so the text reads "playing". Literals are exact at the token level,
  and a slot is trimmed to the first SentencePiece word boundary.

Optionals and alternatives stay as branches inside one grammar rather than
being expanded into several, and compiled grammars are cached in
`var/grammars/` (`Grammar.save`/`load`), so they are built once per sentence.

| | x86 | Pi 4 |
|---|---|---|
| cold build, 6 sentences | 9.1 s | 77 s |
| warm startup (cached) | 0.3 s | 22.8 s |
| select, all sentences | 4-130 ms | 0.8-1.4 s |
| cache on disk | 135 MB | 129 MB |
| full stack RSS | — | ~1000 MB |

## Anchored matching (fallback)

Transcription errors land on the **fixed** words, not the slots: "play" comes
back as "They", "Lay", "played", "These". hassil is all-or-nothing, so one wrong
function word loses the command *and* its slots. Note "They" scores 0.25
similarity against "play" -- plain fuzzy string matching does not catch this.

So an exact hassil match is tried first; failing that, the transcript is aligned
against each trigger's word pattern with the fixed words anchored to what the
trigger actually says and a budget of one substitution. That is the
matching-layer equivalent of decoding the fixed words under a grammar, costs
**0.15 ms**, and needs no change to the acoustic models. Ties are broken toward
the pattern whose fixed skeleton explains more of the utterance, so
"play music buy Daft Punk" resolves to `play music by {artist}` rather than
letting an album slot swallow the rest.

Measured over 12 synthesized commands:

| | matched |
|---|---|
| CTC greedy + exact hassil | 8/12 |
| TDT int8 + exact hassil | 11/12 |
| TDT int8 + anchored | **12/12** |

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

**Calibration.** `--wake-ratio` sets the threshold, and a negative value
disables it. Measured on the rejections it exists to overturn — every wake word
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

## Closed-vocabulary backends (Kaldi, Coqui)

The original speech-to-phrase decodes against a fixed graph instead of a
general acoustic model, which is far cheaper. Measured on a Pi 4 against 12 real
VPE captures, trained on exactly these sentence triggers:

| | Parakeet CTC int8 | Kaldi `en_US-rhasspy` | Coqui `en_US-coqui` |
|---|---|---|---|
| peak RSS | 515 MB | 175 MB | **101 MB** |
| transcribe | **0.3-1.2 s** | 0.8-1.5 s | 1.2-2.3 s |
| slots correct | 7/12 | 11/12 | **12/12** |
| out-of-vocab rejected | 3/4 | 3/4 | 2/4 |

Those numbers are with wildcards enumerated to a handful of values, which is
what makes them look so good. Two things spoil it.

**Coqui substitutes rather than failing.** Retrained with an artist list that
excludes radiohead, Kaldi refused all six radiohead commands; Coqui turned
`play the artist radiohead` into `play the artist the beatles` and
`play in rainbows by radiohead` into `play everything by miles davis`. It also
read `turn off ecobees` as `turn on the lights`. Do not use it where a wrong
answer is worse than no answer.

**Kaldi does not scale to a real library.** With the full Music Assistant
library from `scripts/fetch_music_library.py`:

| vocabulary | train | per command | correct |
|---|---|---|---|
| 7 values | 4.6 s | 0.8-1.5 s | 11/12 |
| 996 (album artists + albums) | 108 s | 3.3-5.8 s | 11/12 |
| 1631 (all artists + albums) | 222 s | 5.7-8.4 s | 9/12 |

Narrowing the decoder beam does not help -- at `max-active=1000 beam=10` latency
only falls to 4.4 s and accuracy drops, including
`play in rainbows by radiohead` becoming `play hannah montana 3 by bo burnham`.
The time is not where it looks: the HCLG graph is only 1.9 MB and the decoder
starts in 0.43 s. Per utterance at 996 values it is roughly 2.4-3.0 s of
decoding plus a flat **1.5 s composing against the 107 MB `G.fuzzy.fst`**, which
is rebuilt from the whole vocabulary and loaded fresh every time.

So the cheap backends are cheap in memory and expensive in latency, in
proportion to how many wildcard values you give them. Worth revisiting with a
persistent decoder (the wake verifier already runs one) and a bounded fuzzy FST.

## Rejection confidence

Two signals, measured on 32 VPE captures of the two fixed-vocabulary commands
and 13 out-of-vocabulary utterances.

**Fuzzy cost** is what speech-to-phrase already computes: how many words the
language model hallucinated that the deletion-only FST had to strip, rejected
above 2.0.

**Likelihood ratio** is the per-frame acoustic cost of the grammar decode minus
the same for an unconstrained decode, using the wake verifier's background
graph (same acoustic model, so the costs are comparable). Raw acoustic cost
alone is useless here -- in-vocabulary lands at -1.46 to -2.10 and
out-of-vocabulary at -1.24 to -2.30, fully overlapping -- because it measures
fit to the path the grammar forced, not whether a better path existed.

| | fuzzy cost | ratio |
|---|---|---|
| in-vocabulary (32) | 0.00 - 0.80 | -0.096 - +0.179 |
| out-of-vocabulary (13) | 3.20, 5.00, 5.90, rest no path | +0.171 - +0.821 |
| alone | 32/32, 12/13 | 32/32, 12/13 |
| both must accept | **32/32, 13/13** | |

Neither separates on its own and they fail on different utterances, which is
what makes the conjunction worth having. Fuzzy cost scores
`turn on standing light` at 0.00 and accepts it, because the LM snapped
straight onto a valid sentence and left no spurious words to count; the ratio
puts it at 0.242. The ratio's own near-miss is `what's the date today` at
0.171, against a worst in-vocabulary case of 0.179 -- an 0.008 margin, so there
is no threshold that separates on the ratio alone.

The catch is cost: the second decode adds ~1.8 s, because a free loop keeps far
more states alive than a seven-sentence grammar. That is a bad trade for one
semantically-near false accept, so the fuzzy cost alone is what to ship until
the filler loop lives in the same graph as the sentences and the comparison
becomes free.

A note on the corpus: an earlier version of this table, built on three
captures, showed the ratio separating cleanly at every threshold. That was
overfitting to three samples. The overlap only appeared at 32.

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
