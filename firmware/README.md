# Voice Labs firmware

The ESPHome firmware the satellites run, kept with the app. The working copy is
`~/opt/esphome/my_components_voice_labs` (the `esphome` configs repo); this is a
copy of it, brought up to date when it changes.

| Path | What |
|---|---|
| `my_components_voice_labs/voice-labs-vpe.yaml` | The Voice PE firmware: the XMOS taps, the voice_labs component, Sendspin music, the LED ring, the dial and the center button |
| `my_components_voice_labs/voice_labs/` | The voice_labs external component: the socket protocol (the app's side is `voice_labs/protocol.py`), wake words on mixednet-executor with models the app pushes, monitoring, and the intercom |
| `my_components_voice_labs/mww-only.yaml` | An older experiment: microWakeWord on the device, voice_labs only streaming |
| `my_components_voice_labs/voice_labs_test.py` | A throwaway stand-in for the app: records a call and plays it back |
| `my_components_voice_labs/secrets.yaml.example` | The secrets the configs use; copy it to `secrets.yaml` |
| `experiments/learn-tv/` | The learn-tv wake word front end, taken out of the firmware on 2026-10-27 (its own README) |
| `archive/esphome-native-voice-labs/` | An earlier design, as patches against ESPHome: voice calls over the native API |

The Home Assistant side, the Voice Labs integration, is
[OHF-Voice/voice-labs](https://github.com/OHF-Voice/voice-labs).

## Building and flashing

Built with ESPHome 2026.11.0-dev (kahrendt's fork at `01fc4432ef`, plus
voice_assistant work this firmware doesn't use); 2026.10.0-dev builds it too. Besides voice_labs it takes
`voice_kit` from esphome/home-assistant-voice-pe (`dev`). The XMOS runs the stock
1.3.1 firmware, which `voice_kit` installs from its release on a device that has
another.

```bash
cd my_components_voice_labs
cp secrets.yaml.example secrets.yaml
esphome run voice-labs-vpe.yaml --device voice-labs-vpe.local
```

Every satellite runs this config. Each after the first needs its own name, or
it comes up as `voice-labs-vpe` too and clashes with the first on the network:

```bash
esphome -s name voice-labs-vpe-2 -s friendly_name "Voice Labs VPE 2" run voice-labs-vpe.yaml --device voice-labs-vpe-2.local
```

An early prototype VPE has 8 MB of flash, not 16 (`esptool flash-id` says
which); built for 16 MB, it can't read its partition table and restarts over
and over. Flash it with `-s flash_size 8MB`:

```bash
esphome -s name voice-labs-vpe-3 -s friendly_name "Voice Labs VPE 3" -s flash_size 8MB run voice-labs-vpe.yaml --device /dev/ttyACM1
```
