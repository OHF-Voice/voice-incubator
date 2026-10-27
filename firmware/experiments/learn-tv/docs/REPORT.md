# "Okay Nabu" with TV noise: Voice PE vs. reSpeaker XVF3800

Recorded 2026-10-20/21 on capture1: two Voice PEs (NS + AEC, NS + AGC) and a
reSpeaker XVF3800 (Seeed I2S firmware v1.0.9 16 kHz), side by side. Wake words
come from deepness's FR speaker and TV audio from FL, at about 90° to each
other. Both speakers were calibrated to 87 dB at the devices with a 1 kHz sine
at amplitude 0.707, the reference test's method, which puts wake words at
~70 dB. Every recording is scored offline with the Voice PE's own model
(`okay_nabu_20241226.3`; the same file, by SHA-256, as the reference test's
Arduino firmware) and ESPHome's detection logic, at the VPE's default cutoff
(0.85) with the VAD gate. Each detection is matched to a wake word by local
alignment (below). [REPORT-tables.md](REPORT-tables.md) has every session and
the per-speaker and per-language breakdowns; README.md has the method.

## Overnight: every XVF3800 setup on four playlists (quiet room)

Recorded in one night, with room noise steady at −86 to −88 dBFS, so these
compare directly. Each cell is wake words only / TV −10 dB / TV 0 dB.

| output | reference file (112) | office, validated (104) | Wake Word Collective (112) | synthetic (112) |
| --- | --- | --- | --- | --- |
| *source audio, scored directly* | *111* | *95* | *111* | *111* |
| VPE 1 NS ×4 (stock wake word path) | 58–70 / 52–59 / 15–17 | 20–26 / 22–28 / 4–7 | 83–90 / 77–83 / 27–29 | 69–76 / 55–59 / 12–13 |
| VPE 2 NS ×4 | 72–80 / 42–46 / 6–7 | 21–23 / 14–17 / 2–3 | 90–92 / 73–75 / 17–20 | 71–79 / 44–49 / 3–6 |
| VPE 1 AEC ×4 | 86–92 / 28–30 / 3–4 | 62–65 / 16–17 / 1–3 | 100–103 / 49–51 / 6–8 | 92–96 / 28–33 / 2–3 |
| XVF3800 ASR, auto-select (7,3; **XMOS's recommendation**) | 102 / 38 / 7 | 81 / 20 / 1 | 108 / 58 / 6 | 106 / 38 / 3 |
| XVF3800 processed, auto-select (6,3) | 98 / 43 / 9 | 86 / 35 / 4 | 108 / 53 / 7 | 105 / 38 / 4 |
| XVF3800 ASR, focused beam holding the speaker (unprimed) | 109 / 55 / 13 | 83 / 48 / 3 | 110 / 93 / 16 | 104 / 65 / 8 |
| XVF3800 ASR, focused beam holding the TV | 38 / 0 / 0 | 18 / 1 / 0 | 58 / 2 / 0 | 24 / 2 / 0 |
| XVF3800 ASR, **beam fixed at the speaker** | 104 / **89 / 55** | 86 / **66 / 47** | 109 / **103 / 81** | 107 / **91 / 62** |
| XVF3800 processed, beam fixed at the speaker | 102 / **89 / 63** | 85 / **75 / 48** | 108 / **100 / 78** | 104 / **90 / 63** |

The VPE ranges cover the three overnight sessions of each. The reference
test, for comparison: PE 43–46 / 34–36 / 3–4; 3800 91–105 / 87–89 / 11–14.

## Findings

1. **The VPE replicates the reference test.** The reference "PE" is the NS tap
   at ×4 (its firmware sets XMOS channel 1 to stage 3). In the same room noise
   as the first sessions (−80 dBFS), NS ×4 gets 47–53 / 31–37 / 11–18 over six
   sessions on two units, against the reference's 43–46 / 34–36 / 3–4. In the
   quieter night it gets more (finding 7).

2. **Fixed beams aimed at the speaker are the only XVF3800 setup that holds
   up under TV, on every playlist.** At TV −10 dB they keep 66–103, and at
   0 dB 47–81, where every adaptive output falls to 1–16. They match the
   reference 3800's −10 dB result (87–89) and beat its 0 dB result (11–14). The
   processed fixed beam (with noise suppression) is equal or a little better
   at 0 dB.

3. **XMOS's recommended wake word output (ASR, auto-select beam) is the
   weakest XVF3800 option under TV.** The auto-select beam bounces to each
   command about 0.5 s in and back to the TV in the gaps, so it misses the
   start of "okay nabu". In quiet it's fine (102–108; 81 on the office takes).

4. **Seeed's default output (focused beam 1) depends on what it heard
   first.** A focused beam locks onto a talker and holds it through hours of
   sessions, and resetting the ESP32 doesn't reset it. When beam 1 has the TV,
   the output is useless (67 / 4 / 0 in the first sessions). When it has the
   speaker, as it did overnight and in the reference test (whose quiet runs
   came first), it's the best adaptive output (109 / 55 / 13). Recording both
   focused beams (7,0 and 7,1) and taking a detection on either removes the
   dependence, but it's still far below fixed beams under TV.

5. **On real crowd-sourced voices, the VPE's NS tap is competitive with every
   adaptive XVF3800 output.** On the Wake Word Collective playlist it keeps
   77–83 at −10 dB and 27–29 at 0 dB, ahead of every adaptive XVF3800 output
   at 0 dB (6–16). It's well behind only the fixed beams (78–81).

6. **Replayed far-field recordings suit the VPE's NS tap badly.** On the
   validated office takes (each detected when scored directly), NS ×4 gets
   only 20–26 of 104 with no TV, while the same VPE's AEC tap gets 62–65 and
   the XVF3800 81–86. The office recordings are already reverberant and noisy,
   and noise suppression on top seems to remove too much. Of the original
   office takes, only 47 of 112 pass direct scoring at all.

7. **Room noise moves every result.** deepness's fans quieted overnight
   (load ~16 → ~6), dropping the room from −80 to −87 dBFS, and the VPE's
   scores rose by 10–20 detections. Compare sets recorded in the same room
   noise (table at the end of REPORT-tables.md).

8. **No false accepts** from TV alone in six ~10-minute sessions, on any
   output. That is too little to estimate a rate.

9. **AGC at ×4 clips** (12% of samples at TV 0 dB). Score AGC at ×1; NS ×4 is
   what the stock firmware actually feeds microWakeWord.

## Corrections to earlier results

- **Device overflows.** The ESP32s' ring buffers overflowed in 28 sessions,
  often on all three devices at once (the Pi stalling), and once 174 times on
  the XVF3800 alone. Each overflow silently drops ~16 ms, and up to 3.6 s went
  missing in a session, so a single alignment per recording put later
  detections in the wrong windows. `mww_eval.py` now aligns each recording
  locally, every 10 s from a ±20 s window. One session went from 72 detected
  with 37 false accepts to 109 with none. Every result here is re-scored that
  way.
- The false accepts that appeared earlier in the fixed set's office TV −10 dB
  session came from that misalignment, not an outside sound as first reported.
  They're gone after re-scoring.
- **Why overflows happen** is still open. Three ESP32s at 128 kB/s each plus
  the ReSpeaker HAT on one Pi 4 may be too much. Worth fixing before the next
  long runs.

## XVF3800 setups

`xvf_ctl.py` (through `capture-xvf3800.yaml`'s I2C commands) reads and writes
any XVF3800 control parameter, and every session logs its setup and beam
directions to `playback/NAME.xvf.txt`. The angles are as the XVF3800 reports
them in this placement: wake-word speaker at ~344°, TV at ~101°.

| set | sessions | left channel | right channel |
| --- | --- | --- | --- |
| default | `oknabu2-*` | ASR, focused beam 1 (7,0), on the TV | the same |
| locked | `xl-*` | ASR of the adaptive beam locked on the speaker (7,k) | processed auto-select (6,3) |
| fixed | `xf-*`, `xf2-*` | ASR, fixed beam 1 at 344° (7,0) | processed fixed beam 1 (6,0) |
| xmos | `xm-*` | ASR, auto-select (7,3) | processed auto-select (6,3) |
| dual | `xd-*` | ASR, focused beam 1 (7,0) | ASR, focused beam 2 (7,1) |

The XVF3800 was left on Seeed's defaults afterwards: adaptive beams, 7,0 on
both channels.

## What next

- **Wake-word-informed fixed beams.** Fixed beams are the clear winner, so
  steering them from the wake word is the obvious design to try. Keep the
  adaptive auto-select output for the first detection. At each confirmed
  wake-up, read `AUDIO_MGR_SELECTED_AZIMUTHS[0]` (XMOS's speech direction) and
  aim fixed beam 1 there. The first test: log the direction at each detection
  and measure its accuracy offline.
- **Fix the overflows** (capture1's USB load, or a second Pi).
- **A rate for false accepts** needs hours of TV and other background, not
  10-minute sessions.
- **Close-talk recordings** would separate how NS handles real users from how
  it handles replayed far-field audio (finding 6).
