# Voice Call firmware

The ESPHome firmware the satellites run, kept with the app. The working copy is
`~/opt/esphome/my_components_voice_call` (the `esphome` configs repo); this is a
copy of it, brought up to date when it changes.

| Path | What |
|---|---|
| `my_components_voice_call/voice-call-vpe.yaml` | The Voice PE firmware: the XMOS taps, the voice_call component, Sendspin music, the LED ring, the dial and the center button |
| `my_components_voice_call/voice_call/` | The voice_call external component: the socket protocol (the app's side is `voice_call/protocol.py`), wake words on mixednet-executor with models the app pushes, monitoring, and the intercom |
| `my_components_voice_call/mww-only.yaml` | An older experiment: microWakeWord on the device, voice_call only streaming |
| `my_components_voice_call/voice_call_test.py` | A throwaway stand-in for the app: records a call and plays it back |
| `my_components_voice_call/secrets.yaml.example` | The secrets the configs use; copy it to `secrets.yaml` |
| `experiments/learn-tv/` | The learn-tv wake word front end, taken out of the firmware on 2026-10-27 (its own README) |
| `archive/esphome-native-voice-call/` | An earlier design, as patches against ESPHome: voice calls over the native API |

The Home Assistant side, the Voice Call integration, is `custom_components/voice_call`
in this repo.

## Building and flashing

Built with ESPHome 2026.10.0-dev (kahrendt's fork at `cb1b4b0bd7`, plus one
voice_assistant commit this firmware doesn't use). Besides voice_call it takes
`voice_kit` from esphome/home-assistant-voice-pe (`dev`). The XMOS runs the stock
1.3.1 firmware, which `voice_kit` installs from its release on a device that has
another.

```bash
cd my_components_voice_call
cp secrets.yaml.example secrets.yaml
esphome run voice-call-vpe.yaml --device voice-call-vpe.local
```

Both satellites run this config. The second needs its own name, or it comes up
as `voice-call-vpe` too and clashes with the first on the network:

```bash
esphome -s name voice-call-vpe-2 -s friendly_name "Voice Call VPE 2" run voice-call-vpe.yaml --device voice-call-vpe-2.local
```
