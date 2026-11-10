# Firmware set aside

The ESPHome firmware the satellites run is in
[OHF-Voice/firmware-voice-incubator](https://github.com/OHF-Voice/firmware-voice-incubator),
which builds it and publishes its install page: `devices/voice-pe.yaml` (the
Voice PE firmware: the XMOS taps, the voice_incubator component, Sendspin
music, the LED ring, the dial and the center button), `components/voice_incubator/`
(the external component: the protocol -- PROTOCOL.md there, the app's side is
`voice_incubator/protocol.py` -- wake words on mixednet-executor with models the
app pushes, monitoring, and the intercom), `voice-pe.yaml` (as the install page
builds it, Wi-Fi over improv) and `voice-pe.local.yaml` (from a checkout, Wi-Fi
from `secrets.yaml`; its README says how to flash satellites in development).
Before November 2026 it was the Voice Labs firmware, in
[OHF-Voice/voice-pe-alt-firmware](https://github.com/OHF-Voice/voice-pe-alt-firmware).

What is kept here was set aside:

| Path | What |
|---|---|
| `experiments/learn-tv/` | The learn-tv wake word front end, taken out of the firmware on 2026-10-27 (its own README) |
| `archive/esphome-native-voice-labs/` | An earlier design, as patches against ESPHome: voice calls over the native API |

The Home Assistant side, the Voice Incubator integration, is
[OHF-Voice/integration-voice-incubator](https://github.com/OHF-Voice/integration-voice-incubator).
