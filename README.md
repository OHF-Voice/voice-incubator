# voice_call (app)

Experimental voice app for the ESPHome `voice_call` component. It connects
straight to the device over TCP (no Home Assistant integration yet) and runs
one call at a time:

0. **wake word model push** — on connect the app lowers each `.tflite` in
   `models/wake` and sends it to the device, which has none compiled in
   (below);
1. the device's wake word model fires and opens a call, replaying 1.5 s of
   pre-roll audio that ends at the detection;
2. **wake verification** — the pre-roll is re-decoded with
   [wyoming-wake-kaldi](https://github.com/OHF-Voice/wyoming-wake-kaldi). A
   rejection ends the call. An acceptance also yields the **split point**:
   where the wake phrase ends and the command begins;
3. **endpointing** — `pysilero-vad` watches the command audio for trailing
   silence;
4. **recognition** — speech-to-phrase decodes the command against a grammar
   built from Home Assistant's `conversation` sentence triggers. Triggers that
   capture free text are handled separately (below). A command that matches
   neither ends the call, carrying the unconstrained Parakeet transcript so you
   can see what was actually said;
5. **action** — the recognized text goes to `conversation.process`, which
   fires the sentence trigger, runs the automation, and returns its
   `set_conversation_response`;
6. **speech** — the response is spoken with Piper (`en_GB-jenny_dioco`). An
   automation that sets no response plays `sounds/acknowledge.mp3` instead.

## Run

```bash
.venv/bin/python -m voice_call --save-dir /tmp/calls
```

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

Useful flags: `--host`, `--hass-url`, `--hass-token-file`, `--silence`,
`--max-command`, `--max-score`, `--wake-model`, `--wake-cutoff`, `--debug`.

## Live re-training

The app holds a websocket to Home Assistant and rebuilds the recognizer's
grammar whenever the sentence triggers change -- no restart. It watches two
events, because one is not enough:

| event | fires on |
|---|---|
| `automation_reloaded` | creating or editing an automation (the config API reloads after every write) |
| `entity_registry_updated` | deleting one -- HA skips the reload and only drops the entity |

Events are debounced for a second, the trigger list is re-read, and the grammar
is rebuilt only if the vocabulary actually changed. Retraining takes a few
milliseconds and holds a lock against decoding, so a call in flight waits
rather than racing a half-built grammar. If a reload leaves no sentence
triggers at all, or re-training fails, the previous grammar is kept so the app
never goes deaf.

## Layout

| path | |
|---|---|
| `voice_call/protocol.py` | the device wire format |
| `voice_call/mixednet.py` | lowers a wake word `.tflite` into the device's blob |
| `voice_call/_mixednet_compiler.py` | vendored from mixednet-executor; edit upstream |
| `voice_call/wake.py` | Kaldi wake verification and split point |
| `voice_call/endpoint.py` | silero VAD endpointing |
| `voice_call/recognize.py` | speech-to-phrase, trained on HA sentences |
| `voice_call/hass.py` | sentence triggers in, conversation response out |
| `voice_call/tts.py` | Piper, and the acknowledgement fallback |
| `voice_call/server.py` | the call state machine |
| `wake.yaml` | wake phrases, confusables, decision thresholds |
| `scripts/check_mixednet.py` | proves the pushed blob decodes like a built-in model |
| `scripts/quantize_tdt.py` | dynamic int8 for either Parakeet encoder |
| `scripts/compare_recognizers.py` | A/B two recognizer model directories over saved calls |
| `scripts/fetch_music_library.py` | enumerate Music Assistant artists/albums for a closed-vocabulary recognizer |
| `corpus/positives/` | 32 VPE captures of in-vocabulary commands, for threshold work |
| `tests/commands.txt` | the spoken test corpus, one utterance per line |
| `tests/corpus_expected.tsv` | what each prompt must come back as, or that it must be refused |
| `scripts/check_corpus.py` | proves the ground truth and the sentence triggers agree |
| `scripts/bench_corpus.py` | runs the whole chain over a mic_tap corpus |
| `scripts/report_corpus.py` | per channel, per prompt, per failure |
| `models/` | everything needed to run offline (~750 MB) |

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
verdict and the split point; speech-to-phrase against the grammar built from
Home Assistant's sentence triggers; and hassil for the triggers that capture
free text. `tests/corpus_expected.tsv` says what each prompt must come back as,
or that it must be refused -- `scripts/check_corpus.py` proves that file and the
triggers agree before any audio is read, because a bench run on a mismatched
pair measures the files.

```bash
scripts/check_corpus.py
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

## Wake word models

No model is compiled into the firmware. The device runs
[mixednet-executor](https://github.com/synesthesiam/mixednet-executor) -- the
microWakeWord streaming graph without a TFLite interpreter -- and the app pushes
it a model over the same socket right after `HELLO`. Changing the wake word is
`--wake-model`, not a reflash.

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
| `probability_cutoff` | per-slice threshold, 0.0--1.0; `--wake-cutoff` overrides |
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

## Two captures per call

The XMOS exposes exactly two taps into its pipeline, so every call is recorded
twice: `call-*.wav` from the AGC channel and `call-aec-*.wav` from the AEC
channel. The command is still split out of AGC; AEC is recorded only.

| channel | stage | used for |
|---|---|---|
| 0 | AGC | streamed to the app, command split, `call-*.wav` |
| 1 | AEC | wake word detection (gain 4) and `call-aec-*.wav` (gain 1) |

Channel 1 carries two microphone sources because the two jobs want different
levels: the wake models want gain 4, which would clip a recording meant for
comparison. Leave `aec_microphone` out of the config and only the AGC capture
is kept.

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

## Thresholds

Both gates are deliberately strict and both are calibrated for a **real voice
at the device**:

- `wake.yaml` `theta_competitor: 0.06` rejects near-homophones ("okay natalie")
- `--max-score` defaults to speech-to-phrase's calibrated 5.0 per-token penalty

Playing a recording through loudspeakers across a room degrades the audio
enough that both gates reject it even when the text decodes correctly. That is
the gates working, not a bug; raise them only for bench testing.

## Memory

Almost all of it is one file. Measured per startup stage on the Pi:

| stage | resident |
|---|---|
| imports + Home Assistant + wake model lowering | 78 MB |
| Kaldi wake verifier | +2 MB (`decoderd` is a separate process) |
| **speech-to-phrase (Parakeet CTC)** | **+862 MB** |
| hassil, FST grammars, Piper | +1 MB (the 129 MB of grammars are mapped, not resident) |

`--no-second-pass` only drops the TDT transducer, so it barely moves the total.
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
reproduce. `scripts/compare_recognizers.py` is for exactly this check.

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
| speech-to-phrase `_fst` | a C++ OpenFST extension with no pure-Python fallback, and not on PyPI, so it is built from source |

`_fst` needs `python3-dev` and `libfst-dev` (OpenFST **1.8** — the copy bundled
with Kaldi's tools is older and does not compile). Then:

```bash
rsync -a --exclude .venv --exclude var --exclude 'models/wake-kaldi-tools' \
      --exclude 'models/wake-kaldi-decoderd' voice_call/ pi:~/voice_call/
ssh pi 'cd ~/voice_call && python3 -m venv .venv &&
        .venv/bin/python -m pip install -r requirements.txt &&
        .venv/bin/python -m pip install -e _src/wyoming-wake-kaldi _src/s2p-lib'
```

## Benchmark

```bash
.venv/bin/python -m scripts.benchmark --runs 5
```

Times each stage against a fixed WAV. `--window` sets how much pre-roll goes to
the verifier (default 1.5 s, matching the device); wake-verify cost is linear in
that, so it is the main latency knob.
