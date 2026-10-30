> Vendored into voice_labs from wyoming-wake-kaldi (Michael Hansen's,
> unpublished) at commit `9fda1ff`, under the Apache License 2.0 (`LICENSE`):
> the package, and the resident decoder's source in `decoderd/`, which the
> image's build compiles against the Kaldi tools it fetches
> (`voice_labs/downloads.py`). Where this says `local/tools` and
> `local/models`, voice_labs has `models/wake-kaldi-tools` and
> `models/wake-kaldi-models`; its configuration is `wake.yaml`. Its tests and
> scripts are not vendored.

# wyoming-wake-kaldi

A [Wyoming](https://github.com/OHF-Voice/wyoming) wake service that **verifies**
a wake word with Kaldi and reports **where the wake phrase ends**.

It is a second-stage check, not a streaming detector. A satellite that has
already fired its on-device detector (microWakeWord, openWakeWord, …) sends the
wake window here; the service answers either

* `detection` — the wake word is confirmed, and `timestamp` is the **split
  point in milliseconds**: where the wake phrase ends and the voice command
  begins in the submitted audio; or
* `not-detected` — the audio is a false positive, and the pipeline should stop.

The two recognizers fail differently, which is what makes the second check
worth running: an on-device detector is a small streaming classifier, while
this service decodes a larger acoustic model against explicit wake-phrase,
background, prefix, and confusable grammars.

The verifier logic is ported from the `voice` project's `alignment.py` /
`verifier.py` and produces bit-identical scores.

## How it works

Four grammar-constrained decodes of one shared acoustic model:

| graph    | grammar                                             | used for |
|----------|-----------------------------------------------------|----------|
| `kw`     | the accepted wake phrases + a costed filler loop     | the verdict's numerator, and the forced alignment that yields the split point |
| `bg`     | a free per-phone filler loop                         | the background hypothesis |
| `prefix` | the accepted phrases **minus** their last word       | isolates the last word's evidence |
| `kwc`    | `kw` plus the configured confusable phrases          | how much the best confusable beats the wake phrase by |

The filler loop absorbs whatever follows the wake phrase (the command), and
filler arcs carry the same insertion cost in every graph, so the command's cost
cancels between them. Costs are Kaldi best-path totals:

```
lr        = (cost_bg     - cost_kw)     / wake_frames
lr_prefix = (cost_bg     - cost_prefix) / prefix_frames
lr_last   = (cost_prefix - cost_kw)     / last_word_frames
margin    = max(0, cost_kw - cost_kwc)  / wake_frames
```

The wake word is accepted iff `lr_prefix >= -theta_prefix` **and**
`lr_last >= -theta_last` **and** `margin <= theta_competitor`.

`lr_prefix` / `lr_last` exist because a one-word confusable ("okay **now**")
dilutes to almost nothing in a whole-phrase likelihood ratio but shows up
undiluted per word. `margin` exists because near-homophones ("okay naruto")
beat per-word thresholds.

The split point is the end time of the last wake word in the `kw` graph's
1-best alignment.

## Install

```bash
pip install -e .
```

The service also needs a `local/` tree, which is **not** tracked in git (~255 MB
of model data and Kaldi binaries):

* `local/models/en_US-rhasspy` — the Kaldi nnet3 acoustic model, its IPA
  lexicon (`lexicon.db`) and its phone inventory.
* `local/tools/{kaldi,openfst}` — the Kaldi and OpenFST binaries, `utils/` and
  `steps/` scripts used to build graphs and decode.

Copy it from a checkout that has one, e.g.:

```bash
rsync -a --exclude aspire_chain_model --exclude tests \
  <voice>/local/tools/kaldi/   local/tools/kaldi/
rsync -a <voice>/local/tools/openfst/ local/tools/openfst/
rsync -a --exclude g2p.fst \
  <voice>/local/models/en_US-rhasspy/ local/models/en_US-rhasspy/
```

The G2P model (`g2p.fst`) and the `phonetisaurus` binary are deliberately
**not** included: the service never guesses a pronunciation.

### Optional: resident decoders

Without them, each verification spawns four Kaldi processes that reload the
acoustic model. Building the daemon keeps the decoders resident:

```bash
./decoderd/build.sh local/tools
```

A missing or broken daemon is not an error — the service logs it and falls
back to subprocess decodes. Build it on anything slower than a desktop; see
Performance below.

## Run

```bash
python -m wyoming_wake_kaldi --uri tcp://0.0.0.0:10400
```

| flag | meaning |
|------|---------|
| `--config PATH` | YAML configuration (default `config.yaml`) |
| `--uri URI` | `tcp://`, `unix://` or `stdio://` (default `tcp://0.0.0.0:10400`) |
| `--decoder-daemon PATH` | `verify_decoderd` location (default `decoderd/verify_decoderd`) |
| `--debug-dir DIR` | write every verified window (wav + json) here, for calibration |
| `--check` | validate the config, build the graphs, and exit |
| `--debug` | log at DEBUG |

Graphs are built on startup and cached under `train_dir`; they rebuild
automatically when the phrases, confusables or pronunciations in the config
change. A first build takes a few seconds on x86 (~24 s on a Pi 5); after that
startup is immediate.

## Performance

Latency is dominated by the **graph search**, not by the neural net. Measured
on a Raspberry Pi 4 with resident decoders, for a 1.68 s window:

| component | cost |
|-----------|------|
| features + acoustic model (per decode) | ~95 ms |
| `kw` search | ~215 ms |
| `prefix` / `kwc` search | ~300 ms each |
| `bg` search (free phone loop — the widest grammar) | ~515 ms |

The four decodes run concurrently on four cores but contend for memory
bandwidth, so the wall clock is roughly 60 % of their sum. Two knobs matter,
and they multiply:

**1. `max_window_seconds` — decode time is roughly linear in window length**
(~390 ms per second of audio on a Pi 4, falling toward ~250 at short windows
as per-decode overhead starts to dominate). Everything past the window is paid
for and thrown away.

The floor is **not** just "long enough for the wake phrase". The window must
also be long enough for the longest *confusable* to finish, because a
confusable that gets cut off can never win the `kwc` decode — the margin
collapses and the false positive is accepted. Truncating the corpus here:

| window | positives | "okay natalie" margins |
|---|---|---|
| 1.4 s | 4/4 | 0.157 · 0.865 · 0.196 |
| 1.3 s | 4/4 | 0.162 · 0.865 · 0.256 |
| 1.2 s | 4/4 | 0.180 · 0.861 · 0.282 |
| 1.1 s | 2/4 | **0.099** · 0.855 · **0.105** — accepted |
| 1.0 s | 2/4 | 0.187 · 0.864 · **0.004** — accepted |

1.2 s is a cliff, not a gradient, and it fails in the dangerous direction.
The default is 1.4 s. Raise it if you add a longer confusable, or if your
on-device detector fires later relative to the wake word.

**2. `decoder.beam` / `decoder.max_active` — these grammars barely need a
beam.** They are tiny and heavily constrained, so Kaldi's default search width
is mostly wasted. Over every sample measured, `beam: 10.0` with
`max_active: 700` produced *identical* costs, alignments, split points and
verdicts to the unnarrowed search — **provided the confusable graph is left
uncapped**, which the service enforces regardless of what you configure:

> `max_active` keeps only the N best-scoring states per frame. In `kw` and
> `prefix` there is essentially one path, and `bg`'s best path through a free
> phone loop is found greedily, so capping changes nothing there. `kwc` is
> different: its whole job is to find a *rival* hypothesis, and that rival is
> by construction scoring worse than the forced wake phrase right up until it
> wins. Capping prunes it before it gets there, the margin collapses toward
> zero, and the false positive is accepted.
>
> This is not hypothetical. With `kwc` capped at 700, three recordings of
> "okay natalie" were accepted as "okay nabu" — the `kwc` decode settled for
> "okay now"/"okay nia" and reported margins of 0.046–0.110, under the 0.13
> threshold. Uncapped, all three find "natalie" and are rejected at
> 0.149–0.863. `beam` does not have this effect: 9, 10 and 12 give identical
> margins.

Together, on a Pi 4:

| | 1.68 s window | 3.0 s window |
|---|---|---|
| beam 12, unlimited `max_active`, no window cap | 1060 ms | 2265 ms |
| **defaults: beam 10, `max_active` 700 (`kwc` uncapped), 1.4 s window cap** | **382 ms** | **382 ms** |

Those are clean close-mic recordings. **Latency is content-dependent**, and
real far-field microphone audio costs noticeably more: the same 1.4 s window
of live-mic capture takes **575–680 ms** on a Pi 4. Ambiguous or noisy audio
keeps a wider active-state frontier alive, and `kwc` — the one graph that is
deliberately uncapped — pays the most for it:

| 1.4 s window | `kw` | `bg` | `prefix` | `kwc` |
|---|---|---|---|---|
| clean recording | 106 ms | 196 ms | 168 ms | 191 ms |
| live far-field mic | ~210 ms | ~240 ms | ~230 ms | **~340–400 ms** |

So budget around **600 ms per verification on a Pi 4** in real use, not 382.

Both are well inside what a second-stage check can absorb, so a Pi 4 is
enough — a Pi 5 is not required. On x86 the same defaults give 38–68 ms.

`beam: 12.0` with `max_active: 2147483647` restores Kaldi's unnarrowed search
for about 1.3x the latency (738 ms for the 1.68 s window on a Pi 4) and
identical verdicts and margins.

### A caveat on the defaults

"identical over every sample measured" is a claim about a small corpus, and it
has already been wrong once: the first version of this narrowing also capped
`max_active` on the confusable graph, which looked free on the samples
available then and turned out to accept "okay natalie" as "okay nabu". The
rule now enforced — never cap `kwc` — is a mechanism, not a curve fit, but the
numbers around it are still measured on a few dozen recordings.

Before relying on them, re-run your own labeled positives and hard negatives at
both settings (`--debug-dir`, or `scripts/mic_test.py --save-dir`) and check
that nothing crosses a threshold. What you are looking for is not just the
verdicts but the *headroom*: a negative that is rejected with a margin of
0.14 against a threshold of 0.13 is one bad recording away from being
accepted.

### Sharing the acoustic pass

The four graphs score the same audio against different grammars, so the nnet3
forward pass is identical for all of them. `decoderd` can therefore hold
several graphs in one process and compute it once. That sounds like it should
be worth the ~95 ms a pass costs, times three — but it is not, because graphs
in separate processes decode *in parallel*, and giving that up costs more than
the duplicated work. Measured on a Pi 4 (4 cores, 1.4 s window, verdicts and
split points identical in every case):

| `decoder.groups` | latency | resident memory |
|---|---|---|
| `[[kw, bg, prefix, kwc]]` — one process | 473 ms | 88 MB |
| `[[kw], [bg], [prefix], [kwc]]` — one each | 382 ms | 350 MB |
| **`[[kw, bg], [prefix, kwc]]` — the default** | **359 ms** | **176 MB** |

Two processes of two graphs halves the neural-net work *and* keeps two-way
parallelism, which on this hardware is nearly free: memory bandwidth, not
cores, is the limit, and four concurrent decodes only reached 1.7x speedup.
Pairing matters a little — `kw+bg | prefix+kwc` and `kw+kwc | bg+prefix` both
land at ~360 ms, `kw+prefix | bg+kwc` at 380 ms, because that last one puts the
two cheapest searches in one process and the two dearest in the other.

On a machine with more cores to spare, one graph per process is faster again
(x86: 41 ms versus 51 ms for the default).

### Threshold placement

`--debug-dir` output also shows whether `theta_competitor` sits in the right
place. On the corpus here the confusable margin separates cleanly, but the
default threshold is not centred in the gap:

| | confusable margin |
|---|---|
| true positives (8) | 0.0001 – 0.0217 |
| "okay natalie" false positives (3) | 0.1493 – 0.8625 |

The originally calibrated `theta_competitor: 0.13` sat 6.0x above the worst
positive but only 1.15x below the worst false positive — nearly all its slack
on the wrong side. The default is now **0.06**, near the middle of the gap: at
the 1.4 s window that leaves 2.7x headroom above the positives and 2.6x below
the false positives. Anywhere from 0.04 to 0.12 separates this corpus. If you
see "okay &lt;name&gt;" false positives, this is the knob.

### If you need more

At the defaults a 1.4 s window of clean audio costs 382 ms on a Pi 4, of which
~185 ms is the acoustic model computed four times over identical audio and
~197 ms is the four searches (which parallelise at only 1.70x on four cores —
this is memory bandwidth bound, not compute bound). What is left:

* **Drop the `bg` graph** — worth about 25 %, at the cost of `lr` and the
  `lr_prefix` gate, since both are differences against the background cost.
  That gate never fired on the corpus here, but it is a real check and this is
  a genuine accuracy trade.
* **A smaller acoustic model** — below ~150 ms you need one, and the
  thresholds would have to be recalibrated from scratch.

Sharing the acoustic pass across the searches is already done, and is the
`decoder.groups` knob below — it turned out to be worth much less than the
duplicated work suggested.

Two things that look promising and are not:

* **Trimming the confusable list.** 66 → 4 saved 9 % (293 → 267 ms) and barely
  shrank the graph (475K → 425K): `kwc`'s cost is the filler loop, which every
  graph shares, not the alternatives. Keep them; they are nearly free.
* **Narrowing the search further.** `max_active` must not touch `kwc` (above),
  and beam 9, 10 and 12 give identical margins.


## Protocol

```
                 client                     service
describe  ------------------------------->
          <-------------------------------  info     (one wake model per configured wake word)

detect(names=["okay_nabu"])  ------------>
audio-start ----------------------------->
audio-chunk ----------------------------->  (16 kHz / 16-bit / mono; other
audio-chunk ----------------------------->   formats are converted)
audio-stop  ----------------------------->
          <-------------------------------  detection(name="okay_nabu", timestamp=960)
                                            ... or not-detected
```

* `timestamp` is milliseconds from the **start of the submitted audio** to the
  end of the wake phrase. Everything after it is the voice command.
* `name` is echoed back exactly as the client asked for it, so an alias stays
  an alias.
* A `detect` with no names verifies against every configured wake word; a name
  that is not configured is ignored (and `not-detected` is returned if that
  leaves nothing to check).
* The window is verified when `audio-stop` arrives, or as soon as
  `max_window_seconds` of audio has been buffered — whichever comes first.
  Audio beyond that cap is never decoded. Send the on-device detector's preroll
  plus a little live audio; the wake phrase must start near the beginning,
  because the `kw` grammar has no pre-phrase filler escape, and it must *end*
  before the cap or it will be cut off and rejected.

## Configuration

`config.yaml` declares which wake words exist. **Only wake words listed there
are recognized**, and only words the model can pronounce may be used: a word is
either in `lexicon.db` or it carries an explicit pronunciation in the config.
An unknown word is a startup error naming the word — nothing is ever guessed.

```yaml
tools_dir: local/tools
train_dir: train
max_window_seconds: 2.0      # also caps how much audio is decoded
resident_decoders: true

decoder:                     # Kaldi search width; see Performance
  beam: 10.0
  max_active: 700
  lattice_beam: 1.0

models:
  en_US-rhasspy:
    directory: local/models/en_US-rhasspy
    language: en_US

defaults:
  theta_prefix: 0.85
  theta_last: 0.25
  theta_competitor: 0.13

wake_words:
  okay_nabu:                       # canonical id
    model: en_US-rhasspy
    description: Okay Nabu (Home Assistant Voice)

    names:                         # ids accepted in the `detect` message
      - ok_nabu
      - okay nabu

    phrases:                       # accepted spoken forms
      - okay nabu

    pronunciations:                # for words the lexicon does not have
      nabu: n ˈæ b u
      naruto: n ɑ ɹ ˈu t oʊ

    confusables:                   # must NOT be accepted as this wake word
      - okay now
      - okay maybe
      - okay naruto

    thresholds:                    # optional per-wake-word overrides
      theta_competitor: 0.10
```

### Wake word keys

| key | meaning |
|-----|---------|
| `model` | which entry of `models` to use |
| `names` | extra ids accepted in a `detect` message. The canonical key is always accepted, and every name also matches with spaces/dashes/underscores normalized and case folded, so `okay nabu`, `Okay-Nabu` and `okay_nabu` are the same name |
| `phrases` | accepted spoken forms, each a sequence of words. The first names the wake word in `info` |
| `pronunciations` | `word: pronunciation`, or `word: [pron, pron]` for alternatives. Required for any word not in the model's lexicon |
| `confusables` | phrases that must not be accepted. Each competes against the wake phrase in the `kwc` graph |
| `thresholds` | `theta_prefix`, `theta_last`, `theta_competitor` overrides |
| `description` | shown in `info` |

### Pronunciations

Pronunciations are written in the model's own phone set. For the bundled
`en_US-rhasspy` model that is IPA with stress marks:

* the inventory is `local/models/en_US-rhasspy/model/phones/nonsilence_phones.txt`
  (`ɑ ˈɑ ˌɑ` = unstressed / primary / secondary),
* example words are in `local/models/en_US-rhasspy/phoneme_examples.txt`.

A phoneme outside that inventory is a startup error listing the offending
symbols, so an ARPAbet pronunciation pasted in by mistake fails loudly rather
than quietly mis-training a graph.

A single token can also carry its pronunciation inline, between slashes:

```yaml
phrases:
  - okay /n ˈæ b u/
```

which is exactly equivalent to a `pronunciations:` entry.

### Thresholds

The shipped defaults were calibrated on labeled far-field recordings of
"okay nabu" (6/6 spoken positives, 7/7 spoken hard negatives). They are **not**
universal: a different wake word, microphone or room needs re-calibration. Run
with `--debug-dir` to collect windows and their scores, then adjust
`theta_prefix` / `theta_last` / `theta_competitor`.

## Adding a wake word

1. Add an entry under `wake_words` with its `model`, `names` and `phrases`.
2. Run `python -m wyoming_wake_kaldi --check`. Any word the model cannot
   pronounce is reported by name; add it under `pronunciations` using the
   model's phone inventory.
3. Add confusables — real false positives you have observed, plus near
   neighbours of the last word. `--check` reports pronunciation problems in
   those too.
4. Re-run `--check`; graphs rebuild automatically because the config signature
   changed.
5. Calibrate the thresholds against recordings.

## Testing with a live microphone

`scripts/mic_test.py` runs the microWakeWord model over live mic audio the way
a satellite does, and sends each detection here for verification. It is how you
see the two stages disagree, and the quickest way to collect calibration audio.

It needs `pymicro-wakeword`, which is deliberately **not** a project
dependency — install it yourself:

```bash
pip install pymicro-wakeword
```

Audio is captured with `arecord`, so there is no Python audio dependency.

```bash
python scripts/mic_test.py --uri tcp://192.168.68.70:10400
```

### From a Voice PE over USB

`--vpe` captures from a Home Assistant Voice PE instead of a local microphone,
which is the interesting case: the same microphone array and the same XMOS
pipeline a real satellite uses, so what microWakeWord sees and what this
service verifies are what the device would actually produce.

It needs the `mic_tap` capture firmware, which streams both XMOS taps over the
ESP32-S3's native USB serial port — no Wi-Fi, no network:

```bash
cd ~/opt/esphome && ./esphome_ci.sh run capture-vpe-1.yaml --device /dev/ttyACM0
```

```bash
python scripts/mic_test.py --vpe /dev/ttyACM0 --save-dir captures
```

| flag | meaning |
|------|---------|
| `--vpe [DEVICE]` | capture from a VPE (default `/dev/ttyACM0`) |
| `--vpe-channel {0,1}` | which XMOS tap to use — 0 is the firmware's AEC tap, 1 its NS tap (default 0) |
| `--vpe-stage CH:STAGE` | retune a channel's tap at startup: `NONE`, `AEC`, `IC`, `NS`, `AGC`. Repeatable |
| `--vpe-gain N` | multiply samples before use (default 1) |
| `--led-seconds S` | how long the LED ring stays green after an accepted detection (default 5; `0` disables it) |

### Running the command through Home Assistant

`--ha` completes the chain: on an accepted detection the **command audio** —
the part of the window after the split point — goes to Home Assistant's
speech-to-text and intent stages, and the transcript and response are printed.

```bash
HA_TOKEN='<long-lived access token>' \
  python scripts/mic_test.py --vpe --vpe-stage 0:AGC --ha
```

```
[1] microWakeWord fired: 'Okay Nabu' (probability 0.913 > 0.850)
    ACCEPTED as 'okay_nabu' in 412 ms -- wake word ends at 870 ms of 1400 ms
    heard  : 'turn on the lights'  (2.3 s of command audio)
    replied: 'Turned on the lights'
```

The wake word is **not** sent. It has already been verified here, and the split
point says exactly where it ends, so Home Assistant never has to transcribe it
or be told to ignore it — which is the whole point of reporting a split point.

The verification window only holds the first fraction of a second of the
command, so the window's tail is sent first and then live audio continues until
Home Assistant's own VAD says the command has finished (`stt-vad-end`), capped
by `--ha-seconds`.

The command is **endpointed locally**, with Silero. Home Assistant finalises
speech-to-text when the audio stream ends, so the sooner the stream ends after
the speaker stops, the sooner the command is acted on — and deciding that here
avoids waiting for Home Assistant to reach the same conclusion a round trip
later. Replaying one recording through a stub Home Assistant:

| | command audio sent |
|---|---|
| endpointed locally (`--vad-silence-seconds 0.5`) | **1.4 s** |
| left to the far end | 9.7 s |

(The stub has no VAD of its own, so the second row runs to the end of the
recording. A real Home Assistant would stop at its own `stt-vad-end`; the
saving is then its silence window plus a round trip, not the whole tail.)

`result.json` records which it was as `ended_by`: `endpoint` when the local VAD
called it, `vad` when Home Assistant did, `source` when the audio ran out,
`budget` when `--ha-seconds` was reached.

Home Assistant's **conversation id** is carried from one command to the next, so
the conversation agent keeps its context and a follow-up like "turn it off"
still knows what "it" was. Home Assistant assigns the id at `run-start` and
repeats it on `intent-end`; it is sent back on the following run, and replaced
whenever Home Assistant issues a new one (it expires them). `--no-conversation-id`
starts each command fresh instead. When the agent wants a follow-up without
another wake word, the run reports `continue_conversation` and the harness says
so — it does not act on it, since that would mean re-opening the microphone
without a verified wake word.

| flag | meaning |
|------|---------|
| `--ha` | enable it; needs `HA_TOKEN` in the environment |
| `--ha-url URL` | websocket API (default `ws://homeassistant.local:8123/api/websocket`) |
| `--ha-pipeline NAME` | pipeline to use (default: the preferred pipeline) |
| `--vad-silence-seconds S` | silence that ends the command (default 0.5) — the responsiveness knob |
| `--vad-minimum-command-seconds S` | shortest command accepted, so a pause right after the wake word does not end it (default 0.8) |
| `--no-endpoint` | leave endpointing to Home Assistant |
| `--ha-seconds S` | most command audio to send, whatever the VAD says (default 15) |

This needs `aiohttp` and `pysilero-vad`, which like `pymicro-wakeword` are
test-only dependencies: `pip install aiohttp pysilero-vad`. Without
`pysilero-vad` the harness warns once and leaves endpointing to Home Assistant.

The websocket client is in `scripts/ha_pipeline.py`, adapted from
`websocket-voice-assistant`'s `app.py` and cut down to `stt` → `intent`; the
endpointer is `scripts/command_vad.py`, adapted from `wake-word-split`.
`result.json` gains a `home_assistant` block with the transcript, response and
event sequence.

### Watching the latency on the ring

With `--vpe`, the device's LED ring shows the verification happening:

| | ring |
|---|---|
| microWakeWord fires | **white**, immediately — before the post-roll capture or the round trip |
| verifier accepts | **green**, then dark after `--led-seconds` |
| verifier rejects | **dark**, immediately |

The gap between the ring lighting and it changing is the verification latency,
so you can see it rather than read it. The console prints the same number
(`ACCEPTED as 'okay_nabu' in 412 ms`), and `result.json` records it as
`verify_ms`. Point `--uri` at a Pi to watch the difference against a desktop.

This needs the LED ring in the capture firmware (`capture-vpe-base.yaml` in the
esphome checkout): a `light:` on GPIO21 behind the `led_power` supply on
GPIO45, and `L0`/`L1`/`L2` commands in its `on_command` automation.

The host drives the capture over the same wire: `S` starts the stream, `X`
stops it, and `<channel><stage>` retunes the XMOS pipeline. The framing matches
`multicap.py` in the esphome checkout.

### Pick a loud enough tap

**This is the single most common cause of spurious rejections.** The verifier
needs normally levelled audio: the acoustic model was trained on it, and below
roughly −30 dBFS there are too few bits left in the int16 samples to describe
speech. A capture at −53 dBFS produced alignments like `okay[1.14-1.23]
nabu[1.23-1.35]` — a 0.09 s "okay" — and was rejected, correctly.

The taps differ by a lot. Ambient level on the device here, same room, stage
set per channel at runtime:

| stage | on channel 0 | on channel 1 |
|---|---|---|
| AGC | rms 165 | rms 299 |
| NS | rms 49 | rms 0 |
| AEC | rms 7 | rms 2 |

**Use AGC** unless you have a reason not to — it applies its gain inside the
XMOS DSP, at full precision, before the ESP32 ever sees a sample:

```bash
python scripts/mic_test.py --vpe --vpe-stage 0:AGC --vpe-channel 0
```

(This is also what the earlier wake-word-split work used: XMOS AGC for the
channel it verified.)

If you need NS or AEC and they come out quiet, raise the capture gain **in the
firmware**, not on the host. ESPHome's `MicrophoneSource` applies `gain_factor`
in the Q25 domain and only then truncates to 16 bits, so firmware gain recovers
resolution that host-side gain cannot:

```yaml
# capture-vpe-base.yaml
substitutions:
  gain_factor: "8"     # 1 = unmodified XMOS output; max 64
```

`--vpe-gain` is a host-side multiply on already-truncated samples. It is there
for experiments, not as a fix — measured, it flips quiet windows between
accepted and rejected unpredictably. It also does nothing for detection:
microWakeWord's frontend normalises, and gains of 1 through 16 all gave a peak
probability of 0.97 on the same recording.

`mic_test.py` warns when a window peaks below about −30 dBFS, and records
`peak` and `level_dbfs` in `result.json`.

The serial port is drained by a dedicated thread rather than the event loop:
microWakeWord inference blocks the loop for milliseconds at a time, which at
64 kB/s is long enough to overrun the kernel's tty buffer. With an event-loop
reader ~11 % of frames were lost; with the thread, none. The script reports
lost frames and device overflows on exit, so a stall is visible rather than
silently gapping the audio.

```
[1] microWakeWord fired: 'Okay Nabu' (probability 0.893 > 0.850)
    ACCEPTED as 'okay_nabu' -- wake word ends at 960 ms of 1500 ms
    wake: 0.00-0.96 s | command: 0.96-1.50 s (0.54 s)
    1/1 accepted so far
```

Only the buffered window is sent: `--buffer-seconds` of audio from *before* the
detection, plus `--after-seconds` captured after it.

| flag | meaning |
|------|---------|
| `--uri URI` | wake service to verify against (default `tcp://127.0.0.1:10400`) |
| `--threshold P` | microWakeWord probability cutoff, 0–1 (default: the model's own) |
| `--buffer-seconds S` | audio kept *before* the detection (default 1.0) |
| `--after-seconds S` | live audio captured *after* it (default 0.5) |
| `--model NAME` | built-in microWakeWord model (default `okay_nabu`) |
| `--model-config PATH` | a microWakeWord JSON config instead of a built-in model |
| `--name NAME` | name sent in the `detect` message (default: the model id) |
| `--device DEV` | ALSA capture device, e.g. `plughw:2,0` |
| `--vpe [DEVICE]` | capture from a Voice PE over USB (see below) |
| `--command CMD` | capture program to use instead of `arecord`; must write raw 16 kHz 16-bit mono PCM to stdout |
| `--refractory-seconds S` | ignore further detections for this long after one fires (default 2.0) |
| `--save-dir DIR` | write every window here (see below) |

`--save-dir` writes one timestamped directory per detection:

```
20261005T190015.579315/
  window.wav       1.50 s   exactly what was sent for verification
  wake_word.wav    0.96 s   the window up to the split point   } accepted
  command.wav      0.54 s   the window after it                } only
  result.json               probability, threshold, outcome, split_ms, durations
```

The two halves are cut on a sample boundary and concatenate back to
`window.wav` byte for byte, so `command.wav` is exactly what an STT stage would
receive. A rejected window gets `window.wav` and `result.json` only — there is
no split point to cut at.

The window must cover the wake phrase: `--buffer-seconds` has to reach back
past the start of the wake word from wherever microWakeWord fires, and the
total must stay within the service's `max_window_seconds` or the tail is
dropped. The defaults (1.0 + 0.5) suit "okay nabu".

`--command` also replays recordings, which is useful for repeatable runs:

```bash
sox recording.wav -t raw -r 16000 -c 1 -b 16 -e signed-integer feed.raw
python scripts/mic_test.py --command "cat feed.raw"
```

Lowering `--threshold` is the fastest way to generate the false positives you
need for calibration: microWakeWord fires on near-misses, and the console
shows which ones this service catches.


## Tests

```bash
python -m unittest
```

The config, grammar, scoring and model-relocation tests need only the bundled
model; the Home Assistant ones run against a mock of its websocket API and need
`aiohttp`. The end-to-end tests additionally need wake-word audio, which they look
for in this order:

1. `tests/wav/positive/*.wav` and `tests/wav/negative/*.wav`,
2. `$WAKE_KALDI_TEST_WAV_DIR/{positive,negative}/*.wav`,
3. the `wake-word-split` capture directory, if present.

They are skipped when none of those exist.

## Layout

```
config.yaml                 wake words, pronunciations, confusables, thresholds
wyoming_wake_kaldi/
  __main__.py               CLI, Wyoming info
  handler.py                Wyoming event handler (detect -> detection/not-detected)
  service.py                process-wide state: models, graphs, decoders
  config.py                 YAML loading and validation
  lexicon.py                lexicon.db lookup + phone-inventory checks
  model.py                  relocates the model's online-decoding config
  train.py                  the four grammars and their HCLG graphs
  verify.py                 decode -> likelihood ratios -> verdict + split point
  kaldi.py                  tool discovery and one grammar-constrained decode
  pool.py                   resident decoder processes (optional)
scripts/mic_test.py         live mic -> microWakeWord -> this service (test harness)
scripts/ha_pipeline.py      the accepted command -> Home Assistant stt + intent
scripts/command_vad.py      Silero endpointing, to close that stream promptly
decoderd/                   the resident decoder daemon (C++ source + build.sh)
local/models/               Kaldi acoustic model, lexicon, phones
local/tools/                Kaldi and OpenFST binaries and scripts
train/                      generated graphs (cached, safe to delete)
```
