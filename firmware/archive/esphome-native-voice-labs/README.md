# Voice calls over ESPHome's native API (archived)

An earlier design, before the voice_labs external component and its own
socket. It was a `voice_labs` component inside ESPHome:
- full-duplex calls carried by new native API messages (`api.proto`);
- bounded direct I2S playback, with completion tracking;
- hooks into microWakeWord and the microphone source;
- a stock Voice Preview Edition config, with tests.

It lived only as three local commits on branch `synesthesiam-voice-labs-component`
(`~/opt/copilot-worktrees/esphome/synesthesiam-solid-meme`), pushed nowhere, so
they are kept here as patches:

| Patch | Commit |
|---|---|
| `0001-Add-native-duplex-voice-labss-with-bounded-direct-I2.patch` | `848304365c`, the component, API messages, I2S playback, example and tests |
| `0002-Fix-VPE-initial-mute-callbacks-running-before-LED-se.patch` | `9f5eb35a8a` |
| `0003-Document-observed-VPE-echo-runner-level-tuning.patch` | `a760b38252` |

They apply to ESPHome `dev` at `BASE` (`918b7d2553`):

```bash
git checkout -b voice-labs-native $(cat BASE)
git am 000*.patch
```
