# "Okay Nabu" with TV noise: Voice PE vs. reSpeaker XVF3800

A replication of a reference test that compared the Voice PE ("PE") with a
reSpeaker XVF3800 4-Mic Array: 112 "okay nabu" samples (16 kHz, normalised to
−23 dBFS active-speech RMS, 5 s lead, ~4 s gaps) from a speaker in front of the
devices, with TV audio from a second speaker at 90°.

## Setup

- **Audio:** `/media/cache/okay-nabu/noise/oknabu_tvnoise_{0db,-10db}.wav`, 16 kHz
  stereo: left is the TV (−24.0 / −34.0 dBFS), right the wake words
  (−23.1 dBFS active, the same in both files). Copied to deepness as
  `~/okay-nabu-noise/`.
- **Playback:** deepness, `surround51:CARD=Generic`. The TV channel goes to
  FL and the wake words to FR, one file at 48 kHz from `/dev/shm` with a 4 s
  buffer: streaming it through ffmpeg under heavy memory pressure on deepness
  underran (~5 s of gaps in the first session, since deleted).
- **Calibration** (`calibration.json`): `tone.py FR` / `tone.py FL` play a
  1 kHz sine at amplitude 0.707, as the reference test did. Both read 87 dB at
  the devices with Master at −20.25 dB, so the wake words play at ~70 dB, the
  TV at ~69 dB (0 dB file) or ~59 dB (−10 dB file).
- **Devices** (all on capture1, `pi/capture1.args`): Voice PE `capture1`
  (NS + AEC), Voice PE `capture1b` (NS + AGC), and the XVF3800 (ASR beam 0 on
  both channels, Seeed I2S slave firmware v1.0.9 16 kHz). MULTICAP.md has
  the firmware details.

## Recording

`run_session.sh NAME FILE PAN` resets the three ESP32s over USB (a VPE resets
its XMOS at boot), makes a 3 s warm-up recording to absorb first-capture frame
loss, then records NAME with `rig.sh` while deepness plays FILE through the
ffmpeg pan PAN (`c0=c0|c1=c1` both, `c0=c0` TV only, `c1=c1` wake words only).
Playback start and end times go to `playback/NAME.playback.txt` and into the
session directory.

## Scoring

`mww_eval.py OUT_DIR SESSION...` replays each recording through the model the
Voice PE firmware pins, `okay_nabu_20241226.3` (`prodmodel/`, from
github.com/kahrendt/microWakeWord releases), with ESPHome's exact detection
logic: 5-window average, 1 s cool-down, the VAD gate (`prodmodel/vad.*`, from
esphome/micro-wake-word-models v2), and the firmware's three sensitivity
cutoffs (0.85 default, 0.69, 0.56). NS is scored at gain ×4, as the stock
firmware feeds microWakeWord. AEC and AGC are scored at ×1 and ×4: no
production path feeds them to the wake word, and AGC clips at ×4. The XVF3800
is scored at ×1, as in the reference test. Each detection is matched to a wake
word in the source file by cross-correlating the recording with it.

Runs in `/media/cache/two-stage-wake-word/live-venv` (pymicro_wakeword 2.4.1).

- `results-session1/`: the first sessions (`oknabu-*`), VPEs only (AEC + AGC),
  at the devices' previous, uncalibrated position.
- `results-session2/`: `oknabu2-*`, both VPEs (NS + AEC, NS + AGC) and the
  XVF3800 at the calibrated position, each device rebooted before each session.
- `results-xl*/`, `results-xf*/`: the locked and fixed XVF3800 sets
  (`-office`: office playlist; `-officev`: validated playlist).
- `results-xm*/`, `results-xd*/`, `results-xf2*/`: the overnight xmos, dual
  and second fixed sets (`-officev`, `-wwc`, `-syn`: the other playlists).
- `results-direct-*/`: each playlist's own audio, scored directly.

## XVF3800 setups

`xvf_ctl.py DEVICE NAME...` reads (`NAME`) or writes (`NAME=V,V`) XVF3800
control parameters through `capture-xvf3800.yaml`'s mic_tap I2C commands,
and `--watch` polls the beams' directions. It runs on capture1 while nothing
records, since the port has one owner. `run_xvf_sessions.sh MODE PREFIX [SET]`
records the conditions with the XVF3800 in `locked` mode (an adaptive beam
locked on the wake words before each session) or `fixed` mode (beams fixed
at the two speakers), each with two outputs, one per I2S channel. It logs the
setup and beam directions to `playback/NAME.xvf.txt`. `report.py` gathers
every `results-*/` into `REPORT-tables.md`. [REPORT.md](REPORT.md) has the
findings.

## Two-mic beamforming offline

`raw-tv-m10db` and `raw-tv-0db` recorded both VPE 2 raw mics and the XVF3800's
diagonal raw mics 0 and 2 (`AUDIO_MGR_OP` 1,0 and 1,2), with VPE 1 on NS + AEC
alongside. `beamform.py OUT_DIR SESSION...` builds two-mic outputs from each
pair (delay-and-sum, fixed and learned nulls on the TV, a null bank, adaptive
cancellers, each with and without a simple noise suppressor) and scores them
with the production model; results in `results-beamform/`.

`run_aec_batch.sh` repeats that with VPE 2 on both mics after AEC (the
aec-both-mics XMOS firmware): wake words only, the reference file at TV -10 dB,
0 dB and TV only, and the Wake Word Collective and validated office playlists.
Results in `results-beamform-aec/` (beamformed) and `results-aec*/` (VPE 1's
stock NS tap in the same sessions).

## Echo tests

`run_echo_batch.sh` loops a music clip through VPE 2's own speaker
(`echo_test`) while the wake words play, VPE 2's setup in each session per
`playback/NAME.vpe2.txt` (raw mics, both AEC mics, learn-tv on the device, or
the stock NS tap). The music drowns the wake words' timing in VPE 2's own
recording, so these are scored with `mww_eval.py --wallclock`, which aligns by
the session clocks (aplay starts 0.362 s after the logged playback start; 12
clean sessions agree to +-30 ms). `beamform.py` does the same for echo*
sessions. Results in `results-echo/`, `results-echo-partial/` (echo6-ns: the
Pi's SD card filled up 413 s in) and `results-beamform-echo/`.

## TV + music

`run_tvmusic_batch.sh`: the reference file at TV -10 dB and 0 dB while VPE 2
plays music through its own speaker (volume 0.6, about level with the wake
words at its mics), VPE 2 on its AEC pair, learn-tv on the device, or the
stock NS tap; the XVF3800 on its fixed beams. Results in `results-tvmusic/`
(`--wallclock`) and `results-beamform-tvmusic/`.

## Live tests

VPE 2 (`capture1b.yaml`) runs microWakeWord on learn-tv on the device (green
flash; the centre button toggles to the stock NS tap, white flash), plus a
fallback detector on mic 0 after AEC (yellow flash, when the main one missed),
so a wake word nulled along with the TV still counts. Between detections the
ring shows the learned null as two red LEDs (a two-mic null can't tell a
direction from its mirror; brighter = more coherent). `vpe_cmd.py PORT A<n>`
sets the LED at the +tmax end of the mic axis, `TD` reads the null back
(marker 114 = 100 + 100 tau/tmax, 115 = coherence x 100). VPE 1
(`capture1.yaml`) runs the stock NS tap (magenta flash). `play_tv.sh SPEAKER
[0db|-10db] [ww]` on deepness loops the TV (or the wake words) on one speaker.

Marker 130 is a main detection, 131 a fallback one. With the devices moved
(2026-10-21, TV from FL now ~60 degrees off the mic axis), 98 s of the
reference file (19 wake words), in `pi-sessions/capture1/live-fallback-*`:

| run | VPE 1 NS | VPE 2 learn-tv | VPE 2 fallback | either on VPE 2 |
|---|---|---|---|---|
| wake words only | 11 | 10 | 13 | 13 |
| TV 0 dB on FL | 0 | 1 | 0 | 1 |

At TV 0 dB mic 0 alone peaks at 0.82 offline, under the 0.85 cutoff, so the
fallback has nothing to add there; with the wake words alone learn-tv nulls
their speaker and the fallback catches them.

## Office-multicap playlist

`build_office.py office` builds `office/office_tv_{0db,-10db}.wav` (git
ignores the WAVs; the build is deterministic) and `office/manifest.json`: 28
commands per speaker (jenn, mike, natalie, oliver) from the S3-BOX-3's mic0 in
office-multicap, trimmed and set to -23 dBFS active speech, shuffled, 5 s lead,
4 s gaps, TV looped underneath. Score with
`mww_eval.py --source office/office_tv_0db.wav --manifest office/manifest.json`.
`build_office.py office-validated --validated` builds the validated variant
from takes the model detects directly (on deepness as `officev_tv_*.wav`), and
`mww_eval.py --direct` scores a playlist's own audio with no playback.

`build_playlist.py {wwc,synthetic} DIR` builds validated playlists the same
way from the Wake Word Collective (28 each English, German, Dutch/French,
other) and the synthetic positives (14 per TTS language). They're on deepness
as `wwc_tv_*.wav` and `syn_tv_*.wav`.

## Overnight runs and re-scoring

`overnight.sh MODE:PREFIX...` runs `run_xvf_sessions.sh`'s overnight set (the
reference file and the validated office, WWC and synthetic playlists, 13
sessions) for each XVF3800 setup, scoring after each. `rescore.sh` scores every
session set again after a change to `mww_eval.py`. `mww_eval.py` aligns each
recording locally, every 10 s, because device overflows silently drop audio.
`room_noise.py` measures each session's room noise before playback, since it
moves every result.
