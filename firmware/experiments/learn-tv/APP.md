# learn-tv: the app's side

What the app did with learn-tv, as the app's README had it until 2026-10-28,
when this side was taken out too. `app.patch` puts it back (below).

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

When the null's own detector does hear it, barely, the call capture can still
have lost the talker: a child's call, its wake word at 0.41 against a cutoff
of 0.40, then 15 s of "no speech was heard" -- her voice was there at -30
dBFS, but with 6-10 dB less of it below 1 kHz than mic 0 had, and Silero
heard none of it. So the app looks at the wake word first, the pre-roll's last
1.2 s in each capture (`voice_call/endpoint.py` `wake_speech`): under a tenth
of it speech in the call capture and a quarter or more in the second, and the
talker is in the null (`talker_in_null`). The call is then verified, ended,
recognized and streamed to speech-to-text from the second capture, lined up
with the first; and verified on the call capture too, when the second one's
verdict is a refusal, so that nothing is refused that was taken before.
Ordinary calls hear 0.3-0.8 of their wake word as speech in both captures.

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

## Putting it back

`app.patch` is the change that took it out, reversed. From the repository's
root (`ohf-apps-experimental`):

```bash
git apply --3way voice_call/firmware/experiments/learn-tv/app.patch
```

It brings back, in `voice_call/server.py`, `voice_call/endpoint.py` and the web
UI:

- **HELLO `aec_lead_samples`**: how far the second capture leads the call
  (learn-tv says 0; the stock XMOS taps are 905 samples apart). Every call's
  `call_start` step keeps it as `aec_lead`, which replays still read.
- **CALL_START `detector` and `swapped`**, kept in `call_start` and shown in a
  session ("mic 0 only").
- **The talker in the null** (`_check_null`, `wake_speech`, `Call.in_null`):
  the wake word's speech in each capture; when the call capture has lost the
  talker, verifying, endpointing, recognition and speech-to-text on the second
  capture, lined up with the first (`_aec_lined_up`, `_aec_since`).
- Their tests, in `tests/test_app.py`, `tests/test_speakers.py`,
  `tests/test_endpoint.py` and `tests/fake_satellite.py`.
