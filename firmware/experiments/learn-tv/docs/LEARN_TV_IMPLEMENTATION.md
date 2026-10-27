# Implementing learn-tv (+ mic 0 fallback) on the Home Assistant Voice PE

A spec for porting the learn-tv wake word front end from this test rig into a
Voice PE firmware. Everything here runs today on a test unit ("VPE 2",
`capture1b.yaml`); the reference code is in this repository and named in each
section. Read the whole document before starting: the production questions at
the end change the design.

## What it does and why

The Voice PE's stock wake word path is the XMOS's NS output (AEC -> IC -> NS),
fed to microWakeWord at gain 4. With a TV playing from one direction, that path
misses most wake words. learn-tv replaces it with a two-mic null steered at
**whatever source has been playing for the last ~20 s**, on the assumption that
the wake word comes from somewhere else than the TV that has been talking for
a while. It needs no calibration and no knowledge of where the TV is.

A two-mic null cannot tell a direction from its mirror (a cone around the mic
axis), and when nothing but the user has been talking it learns the user's
voice as "the TV". So a second detector runs on mic 0 alone (after AEC, no
null); a wake word counts if either detector fires.

Evidence (okay_nabu_20241226.3, cutoff 0.85, with the VAD gate; reference
file: 112 wake words from one speaker, TV from another 90 degrees away):

| condition | learn-tv | mic 0 after AEC | stock NS | where |
|---|---:|---:|---:|---|
| TV -10 dB | 60 | 25 | 48 | offline, `results-beamform-aec/` |
| TV 0 dB | 27 | 3 | 19 | offline, `results-beamform-aec/` |
| Wake Word Collective clips, TV 0 dB | 44 | 2 | 28 | offline, `results-beamform-aec/` |
| wake words only (no TV) | 94 | 84 | - | offline, `results-beamform-aec/` |
| TV only, 0 dB: false accepts | 0 (max p 0.12) | 0 (max p 0.26) | - | offline, `results-beamform-aec/` |
| TV -10 dB + VPE's own music | 56 | - | 41 | on device, `results-tvmusic/` |
| TV 0 dB + VPE's own music | 23 | - | 9 | on device, `results-tvmusic/` |

Live, in a moved placement (`README.md`, "Live tests"), 19 wake words with no
TV: learn-tv 10, fallback 13, either 13, stock NS on the other VPE 11. A
firmware swap between the two units confirmed the gain follows the firmware,
not the unit or its position. For scale: the XVF3800 on fixed beams got ~80
on the TV -10 dB + music sessions, and a fixed null placed on the TV's known
direction (`null-tv`, offline) got 70 where learn-tv got 60. So learn-tv closes
part of the gap, not all of it.

## Architecture

```
XMOS (aec-both-mics firmware 1.3.101)
  ch0 = stage 5: mic 0 after AEC   ch1 = stage 1 (AEC): mic 1 after AEC
        |  I2S, 16 kHz, stereo, 32-bit
        v
ESP32-S3: i2s_audio microphone (stereo)
        v
learn_tv microphone platform  ---- mic 0 (x4, int16) ----> ring buffer -> fallback task:
  ch0 = learn-tv output                                     micro_wake_word front end +
  ch1 = mic 0, same delay                                   WakeWordModel (okay_nabu)
        v                                                   -> fallback_detections()++
micro_wake_word (channel 0, gain 4, VAD, okay_nabu)
        v
on_wake_word_detected / interval polling fallback_detections()
```

Four pieces of work:

1. XMOS firmware: emit both mics after AEC.
2. ESP32: the `learn_tv` microphone platform (the algorithm).
3. ESP32: the fallback detector inside it.
4. ESPHome config: wiring, XMOS stage selection, detection handling, and
   (optional) the LED readout and mode switch.

## 1. XMOS firmware: both mics after AEC

Reference: `~/opt/voice-kit-xmos-firmware`, branch `aec-both-mics`, commit
`7ffef53` (local only, not pushed). Upgrade image
`build-aec0/example_ffva_int_fixed_delay_upgrade.bin`, md5
`8f93b6ac71c4bda417d1611d253447c4`.

The stock pipeline runs AEC on both mics, then IC merges them to mono, so
only mic 1 after AEC is reachable (`PIPELINE_STAGE_AEC`). The change:

- `modules/audio_pipelines/reference/fixed_delay/audio_pipeline_dsp.h`: add
  `int32_t aec_mic0_samples[appconfAUDIO_PIPELINE_FRAME_ADVANCE];` to
  `frame_data_t` **directly after** `mic_samples_passthrough`. The output
  stage treats the struct's sample arrays as consecutive channel rows, so it
  becomes row 6.
- `audio_pipeline_t0.c`: at the top of `stage_vnr_and_ic()`, before IC
  overwrites it, `memcpy(frame_data->aec_mic0_samples, frame_data->samples[0], ...)`;
  in `audio_pipeline_output_i()` output 7 channels instead of 6.
- `configuration_servicer.h`: `PIPELINE_STAGE_AEC_MIC0 = 5` in
  `e_pipeline_processing_stages`.
- `src/ffva/src/main.c` `audio_pipeline_output()`: for either output channel,
  stage 5 reads `*(tmpptr + j + (6 * frame_count))`.
- `app_conf.h`: `APP_VERSION_PATCH 101`, so ESPHome's `voice_kit` installs it
  over stock 1.3.1 and reinstalls 1.3.1 if the config asks for it.

(The same commit also adds an `FFVA_L71_FLASH` CMake option and a W25Q64JW
flash spec, only for smoke tests on an XK-VOICE-L71 dev board. Leave it out
of a production build.)

Stage selection: ESPHome's `voice_kit` component only knows stages up to AGC,
so the test config writes the stage over I2C itself: address `0x42`, bytes
`{241, 0x30, 1, 5}` (configuration servicer resid 241, cmd 0x30 = channel 0
stage, 1 byte, value 5), once, 15 s after boot, with `channel_0_stage: NONE`
and `channel_1_stage: AEC` in the `voice_kit` config. Write it only once: a
second write during startup left the stage wrong in an earlier test. For
production, add `AEC_MIC0` (5) to `voice_kit`'s stage enum instead and set it
in YAML.

Note that the AEC'd output lags the raw mics by 511 samples. That doesn't
matter here, since both channels are AEC'd alike.

## 2. The learn_tv microphone platform

Reference: `my_components_mic_tap/learn_tv/` (`microphone.py`, `learn_tv.h`,
`learn_tv.cpp`). The offline original is `learn_tv()` in
`okay-nabu-tv/beamform.py` (numpy, 20 lines). Use it to check a port
sample-for-sample.

### Interface

An ESPHome `microphone` platform that wraps a stereo source microphone:

- Input: interleaved stereo, 32-bit little-endian Q31, 16 kHz (ch0 = mic 0,
  ch1 = mic 1). Calls `audio.set_stream_limits(32 bits, 2 channels, 16000 Hz)`
  in the config validator so source checks pass.
- Output: the same format. ch0 = learn-tv output; ch1 = mic 0 delayed by the
  same amount (for comparison, and so consumers on channel 1 still get audio).
- `start()`/`stop()` forward to the source; `loop()` follows the source's
  state (STARTING -> RUNNING, STOPPING -> STOPPED).
- Options: `source`, `tau` (20 s), `warmup` (2 s), `mode` (learn/passthrough),
  `max_delay` (0.25 ms, LED readout only), `led_axis` (0..11),
  `fallback_model` (section 3). Defines `USE_LEARN_TV` (and
  `USE_LEARN_TV_FALLBACK`).
- Runtime: `set_mode(0|1)` (0 = passthrough: ch0 mic 0, ch1 mic 1, through
  the same framing), `reset()` (forget, warm up again),
  `null_direction(&tau_ms, &coherence)`, `take_stats()` (processing time).
  A global instance pointer serves command handlers and lambdas.

### Algorithm

STFT: N = 512, hop 256 (16 ms), window sqrt of a periodic Hann
(`sqrt(0.5 - 0.5 cos(2 pi i / N))`), so analysis x synthesis sums to 1 at 50 %
overlap. Per bin k = 0..256, with X0, X1 the two mics' spectra:

```
a = HOP / 16000 / tau                         # 0.0008 for tau = 20 s
c  = X0 * conj(X1)
C[k] = (1 - a) C[k] + a * c / |c|             # unit cross-spectrum: every frame
                                              #   counts once, however loud
L[k] = (1 - a) L[k] + a * log(|X0| / |X1|)    # log level ratio
if frames_learned * HOP / 16000 > warmup:
    h = exp(L[k]) * C[k] / |C[k]|
    Y[k] = X0 - h * X1
else:
    Y[k] = X0
```

(1e-30 guards every division and log.) Then the inverse FFT of the Hermitian
spectrum, times the window, overlap-added. Emit one hop per frame: ch0 = the
completed overlap-add, ch1 = `in0` from the start of the same frame, so both
channels carry the same delay (N - HOP = 256 samples, 16 ms).

What matters in this design:

- **Unit-magnitude averaging.** Normalising `c` before averaging makes the
  estimate follow the source that is present in the most frames, not the
  loudest one. A wake word lasts ~1 s against tau = 20 s, so it barely moves
  the null. Don't replace it with a power-weighted average (an adaptive
  filter), which follows loud speech; `adapt-*` in `beamform.py` did worse (19-43 at TV
  -10 dB).
- **Learn only in learn mode.** C and L update and `frames_learned` counts
  only while mode = 1; in passthrough nothing is learned.
- **No VAD or gating of the learning.** Gating learn-tv's own learning was
  never tried; a gated adaptive filter (`adapt-gated`) got 43 offline at TV
  -10 dB, against learn-tv's 60.

Implementation notes from the ESP32-S3 port:

- Both mics go through one complex FFT (z = x0 + j x1). Unpack with
  X0 = (Z[k] + conj Z[N-k]) / 2, X1 = (Z[k] - conj Z[N-k]) / 2j. Write Y to
  separate buffers, since Z[N-k] is still needed for later bins.
- A plain iterative radix-2 float FFT with precomputed tables runs at
  **~2.4 ms per 16 ms frame**. esp-dsp would be faster but wasn't needed.
- Buffers: 13 float arrays of N, N/2 or N/2+1 (~20 KB), internal RAM preferred
  with fallback to any 8-bit-capable heap.
- Processing runs in the source's data callback (the I2S microphone task),
  and the output goes to the platform's own data callbacks.
- Convert back to Q31 with saturation.

### Null direction (for the LED readout)

Phase slope over 300-1800 Hz, where the phase can't wrap for this spacing:

```
tau_ms = 1000 * sum_k |C_k| * angle(C_k) / (2 pi f_k) / sum_k |C_k|
coherence = mean_k |C_k|                    # 0..1
```

tau > 0 means the source reaches mic 0 first; +-max_delay (0.25 ms on the VPE)
are the two ends of the mic axis. Only valid after the warm-up.

## 3. The fallback detector (mic 0 alone)

Reference: `fallback_task_()` and the end of `process_frame_()` in
`learn_tv.cpp`.

ESPHome's `micro_wake_word` takes one microphone stream, so the second
detector runs inside learn_tv using micro_wake_word's own classes:

- In `process_frame_()`, while mode = 1 only (in passthrough ch0 is already
  mic 0), write the hop of mic 0 to a `ring_buffer::RingBuffer` (HOP x 2 bytes
  x 64, ~1 s) as int16 with gain 4 and saturation (`in0 * 4 * 32768`),
  matching microWakeWord's `gain_factor: 4`.
- A FreeRTOS task (stack 8192, priority 3) reads it and runs:
  - the micro_wake_word front end, `FrontendConfig` populated exactly as
    `micro_wake_word`'s preprocessor does (`FEATURE_DURATION_MS` window, 10 ms
    step, `PREPROCESSOR_FEATURE_SIZE` channels, the `FILTERBANK_*`,
    `NOISE_REDUCTION_*`, `PCAN_GAIN_CONTROL_*` and `LOG_SCALE_*` constants);
  - feature scaling as micro_wake_word does: `v = (out * 256 + 333) / 666 - 128`,
    clamped to int8;
  - `micro_wake_word::WakeWordModel("fallback", model_data, cutoff, window,
    "Okay Nabu", arena, true, true)`, then `perform_streaming_inference()`,
    and when `get_unprocessed_probability_status()` is set,
    `determine_detected()`. On a detection, increment an atomic counter and
    `reset_probabilities()`.
- The model is the same flatbuffer micro_wake_word already embeds: in YAML
  give the micro_wake_word model a `raw_data_id` (e.g. `okay_nabu_data`) and
  pass that id to `fallback_model: id:`. Cutoff 216 and window 5 match the
  manifest's "slightly" setting; tensor arena 37000.
- No VAD gate on the fallback, and no refractory period beyond
  `reset_probabilities()`.

The `WakeWordModel` constructor signature and the frontend constants come
from the ESPHome version in use. Check them against that tree; this was built
against the ESPHome checkout `esphome_ci.sh` uses.

Combining the two: the config polls `fallback_detections()` every 50 ms. A
fallback detection with no main detection in the last 1.5 s counts as a wake
word (it flashes yellow in the test). A main detection always counts.

## 4. ESPHome configuration

Reference: `capture1b.yaml` (with `capture-vpe-base.yaml`). The relevant parts:

```yaml
voice_kit:
  channel_0_stage: NONE      # rewritten to 5 (AEC mic 0) over I2C after boot
  channel_1_stage: AEC       # mic 1 after AEC
  firmware: {url: ..., version: "1.3.101", md5: 8f93b6ac71c4bda417d1611d253447c4}

microphone:
  - platform: learn_tv
    id: learn_tv_mics
    source: i2s_mics          # the stereo XMOS input
    tau: 20s
    warmup: 2s
    max_delay: 0.25ms
    led_axis: 0
    fallback_model:
      id: okay_nabu_data

micro_wake_word:
  microphone: {microphone: learn_tv_mics, channels: 0, gain_factor: 4}
  models:
    - model: .../okay_nabu_20241226.3/okay_nabu.json
      id: okay_nabu
      raw_data_id: okay_nabu_data
  vad:
```

Optional, from the test config:

- **Mode switch** (center button in the test): stage 5 + learn mode + reset,
  or stage 3 (NS) + passthrough. This gives the stock path back for A/B tests.
- **LED readout** ("Null direction" addressable_lambda effect in
  `capture-vpe-base.yaml`, 100 ms): two red LEDs at
  `axis +- round(acos(clamp(tau / max_delay)) / 30 deg)`, brightness
  80 + 400 x coherence, hidden below coherence 0.1 or before the warm-up.
  `led_axis` must be calibrated per enclosure: it's the LED at the +max_delay
  end of the mic axis. In the test it's still uncalibrated (0).

## Production questions to settle first

1. **The voice assistant's audio.** The test VPE uses both XMOS channels for
   the two AEC mics, so no NS/AGC stream is left for speech-to-text, and the
   test never ran the voice assistant. Options: send learn-tv's ch0 (or
   learn-tv + a light NS) to STT; or a further XMOS change that adds the AEC
   mics to the output without giving up NS/AGC (the I2S link carries only two
   channels at 16 kHz, so this means a different frame format). Check what
   the production config feeds STT today before choosing.
2. **Which audio follows a fallback-only detection.** In that case the user is
   in or near the null, so learn-tv's output will have cancelled them; STT
   should get mic 0 (or NS) for that interaction.
3. **Learning during a conversation.** Over a long voice assistant exchange,
   learn-tv starts learning the user. Freezing C and L while the voice
   assistant is listening, or resetting them afterwards, is untested.
4. **Other wake words and the stop word.** The fallback only runs okay_nabu.
   Production also has other models and "stop". Decide whether they run on
   learn-tv, on mic 0, or on both (each extra model in the fallback adds a
   tensor arena and inference time).
5. **CPU and memory headroom.** With the full production firmware (media
   player, voice assistant, other models), measure the cost on top: the
   learn-tv frame (~15 % of one core at 2.4 ms / 16 ms), plus a second model
   inference every 10-30 ms, plus ~20 KB of buffers, a ~32 KB ring buffer, an
   8 KB stack and a 37 KB arena.
6. **XMOS release.** Version 1.3.101 is a local build. It needs a proper
   release in `esphome/voice-kit-xmos-firmware` and a version that won't
   collide with upstream patch numbers.

## How to verify a port

1. **Passthrough parity.** With mode 0, ch0/ch1 should equal the two
   AEC mics delayed 256 samples (the test recorded this via `mic_tap`).
2. **Algorithm parity.** Record the two AEC mics and the device's learn-tv
   output in one session (`T0`/`T1` in the test firmware). Run
   `beamform.py`'s `learn_tv()` on the mics and compare it with the device
   output: they should match to float rounding once both start from the same
   frame.
3. **Detections.** Play the reference file (`oknabu_tvnoise_{0db,-10db}.wav`,
   TV on one speaker, wake words on another at 90 degrees) and score with
   `mww_eval.py` (with `--wallclock` when the VPE plays its own audio). Expect
   the table above within a few counts in the same room geometry.
4. **False accepts.** TV-only sessions with both detectors running: zero in
   the tests so far, but the fallback has no VAD gate, so check it over hours.
5. **Fallback coverage.** Wake words with no TV. learn-tv nulls the talker and
   the fallback should catch what it misses (live: 10 main, 13 fallback).
