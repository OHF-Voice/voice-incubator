# Voice Labs firmware

The ESPHome firmware the satellites run is in
[OHF-Voice/voice-pe-alt-firmware](https://github.com/OHF-Voice/voice-pe-alt-firmware),
which builds it and publishes its install page: `voice-labs.yaml` (the Voice PE
firmware: the XMOS taps, the voice_labs component, Sendspin music, the LED
ring, the dial and the center button), `components/voice_labs/` (the external
component: the socket protocol -- the app's side is `voice_labs/protocol.py` --
wake words on mixednet-executor with models the app pushes, monitoring, and
the intercom), `voice-pe-voice-labs.yaml` (as the install page builds it,
Wi-Fi over improv) and `voice-labs-local.yaml` (from a checkout, Wi-Fi from
`secrets.yaml`; its README says how to flash satellites in development).

What is kept here was set aside:

| Path | What |
|---|---|
| `experiments/learn-tv/` | The learn-tv wake word front end, taken out of the firmware on 2026-10-27 (its own README) |
| `archive/esphome-native-voice-labs/` | An earlier design, as patches against ESPHome: voice calls over the native API |

The Home Assistant side, the Voice Labs integration, is
[OHF-Voice/voice-labs](https://github.com/OHF-Voice/voice-labs).
