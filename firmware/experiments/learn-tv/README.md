# learn-tv (set aside)

A wake word front end for a room with a TV: a two-mic null steered at whatever
has been playing for the last ~20 s, with a second detector on mic 0 alone for a
talker the null takes out along with the TV. It ran on the second satellite
(`voice-call-vpe-2`) until 2026-10-27, when it was taken out of the firmware to
come back to later. Everything it needed is here.

## What it did

The stock wake word path is the XMOS's noise-suppressed output (AEC, then the
interference canceller, then NS). With a TV playing from one direction, it misses
most wake words. learn-tv:

- **The XMOS** runs a modified firmware (1.3.101, "aec-both-mics") that also
  puts out mic 0 after echo cancelling. Stock firmware puts out only mic 1's,
  since the interference canceller overwrites mic 0's in place.
- **The `learn_tv` microphone platform** makes four channels of the two:
  - the null, for the wake words;
  - the null at a higher gain, for the call;
  - mic 0, for the second capture;
  - mic 0, for the fallback detector.
- **The voice_call component** ran the wake words on a second stream, the
  fallback microphone, without the VAD gate.
  - A wake word only the fallback heard waited 300 ms for the main detector,
    then started a call with the two captures swapped, so the app heard mic 0.
  - The component also told the app how the captures line up (`aec_lead: 0ms`).
  - Its source gains could be changed at runtime.
- **Home Assistant** got:
  - a `learn-tv` switch, to go back to the stock taps and compare;
  - "Null direction", where the null points;
  - "Null coherence", how steady it is.

The app still understands all of it. A HELLO with `aec_lead_samples` and
`fallback`, and a CALL_START with `detector` and `swapped`, are read and
recorded. Nothing in the app needs to change to bring it back.

## How it did

From `docs/LEARN_TV_IMPLEMENTATION.md`: okay_nabu, cutoff 0.85, VAD gate, 112 wake
words from one speaker, the TV from another 90 degrees away.

| condition | learn-tv | mic 0 after AEC | stock NS |
|---|---:|---:|---:|
| TV -10 dB (offline) | 60 | 25 | 48 |
| TV 0 dB (offline) | 27 | 3 | 19 |
| Wake Word Collective clips, TV 0 dB (offline) | 44 | 2 | 28 |
| wake words only, no TV (offline) | 94 | 84 | - |
| TV -10 dB + the VPE's own music (on device) | 56 | - | 41 |
| TV 0 dB + the VPE's own music (on device) | 23 | - | 9 |

TV alone, at 0 dB, made no false accepts on either path. For scale:
- **The XVF3800** on fixed beams got about 80 on the TV -10 dB + music sessions.
- **A fixed null** on the TV's known direction got 70 where learn-tv got 60.

So learn-tv closes part of the gap, not all of it. Live, with no TV, 19 wake
words: learn-tv 10, the fallback 13, either 13, stock NS on the other unit 11.

## What's here

| Path | What |
|---|---|
| `voice-call-vpe-learn-tv.yaml` | The config: `packages: base: !include voice-call-vpe.yaml`, plus learn_tv, the XMOS firmware, the gains and the switch |
| `components/learn_tv/` | The microphone platform (C++ and its schema) |
| `components/voice_call/` | The voice_call component as it was when learn-tv was taken out: the live one plus the fallback microphone, the swapped captures and the runtime gains |
| `xmos/0001-Output-both-mics-echo-cancelled-PIPELINE_STAGE_AEC_M.patch` | The XMOS firmware change (`voice-kit-xmos-firmware`, branch `aec-both-mics`, commit `7ffef53`) |
| `xmos/ffva_v1.3.101_aec_both_mics_upgrade.bin` | That firmware, built (md5 `8f93b6ac71c4bda417d1611d253447c4`, as the config pins it) |
| `docs/LEARN_TV_IMPLEMENTATION.md` | The spec it was built from |
| `docs/REPORT.md`, `docs/REPORT-tables.md` | The test rig's results |
| `docs/RIG_README.md` | The test rig's README (the rig itself, recordings and scripts: `~/opt/mic_tap/okay-nabu-tv`) |

## Bringing it back

1. Copy `components/learn_tv/` into `my_components_voice_call/`.
2. Bring back the voice_call component's fallback support. Either merge it from
   `components/voice_call/`, or copy that over the live component if nothing
   else has changed since.
3. Copy `voice-call-vpe-learn-tv.yaml` next to `voice-call-vpe.yaml`.
4. Serve the XMOS firmware while it builds. The config fetches it from
   `http://localhost:8765/`, so name the file as the config does:

   ```bash
   cp xmos/ffva_v1.3.101_aec_both_mics_upgrade.bin /tmp/xmos/example_ffva_int_fixed_delay_upgrade.bin
   cd /tmp/xmos && python3 -m http.server 8765
   ```
5. Flash it, keeping the satellite's name:

   ```bash
   esphome -s name voice-call-vpe-2 -s friendly_name "Voice Call VPE 2" run voice-call-vpe-learn-tv.yaml --device voice-call-vpe-2.local
   ```

Flashing the plain `voice-call-vpe.yaml` again puts the stock XMOS firmware
(1.3.1) back.
