# Voice Labs

Voice satellites with commands you make yourself, recognized on this machine.
A command says what to listen for, where, and what happens in Home Assistant:
speak a response, run a script, activate a scene, or run an action. It hears
and speaks English.

**The guide is in [OHF-Voice/voice-labs](https://github.com/OHF-Voice/voice-labs):**
everything the app does, page by page.

## Getting started

1. **Start the app.** The first time, it downloads its speech models -- about
   530 MB: Parakeet (speech), EmbeddingGemma 2 (meaning), GLiNER (the command
   fallback) and Piper's Kristin (its voice) -- into its own storage, and its
   page shows how far it got. Later starts and updates keep them.
2. **Install the Voice Labs integration** with [HACS](https://hacs.xyz): add
   `https://github.com/OHF-Voice/voice-labs` as a custom repository of type
   Integration, download **Voice Labs**, and restart Home Assistant. The app
   sets it up the next time it connects. It gives each satellite a device and
   its timers, and runs Assist's intents for the app.
3. **Flash a Home Assistant Voice Preview Edition** with the Voice Labs
   firmware from its
   [install page](https://ohf-voice.github.io/voice-pe-alt-firmware/voice-pe-voice-labs/)
   and give it your Wi-Fi. The app finds it on the network and connects to it.

Then open **Voice Labs** in the sidebar to make commands, name things, set up
satellites and look at sessions.

## Home Assistant's voice assistants

The app is also a Wyoming service, which Home Assistant finds (**Settings >
Devices & services**, Discovered). Add it, and its speech-to-text,
text-to-speech and conversation agent can be picked for any voice assistant
(**Settings > Voice assistants**): for satellites that aren't the app's, and
for what's typed in Assist.

- **Speech-to-text** writes what it heard as the command it heard, with your
  names spelled as on the Names page.
- **Text-to-speech** is Piper, with every voice it has: each is downloaded
  the first time it is used, and removed after 30 days unused.
- **The conversation agent** is your commands, skills and conversation agent,
  in the satellite's area. A command that would beep says its brief response
  instead, and a timer is set by Home Assistant's own agent, on the satellite.

Parakeet's speech-to-text alone is on the next port: add the **Wyoming
Protocol** integration with host `127.0.0.1` and port `10801`.

## Options

- `wake_cutoff`: the satellite detector's probability cutoff for the
  checked wake words (default: each its own -- 0.4 for Okay Nabu, 0.5 for Hey
  Jarvis, 0.7 for Hey Mycroft). Every detection of these is verified again
  here, so it can be generous. A cutoff set on the Wake words page wins.
- `speaker_threshold`: how closely a voice must match to be identified.
- `silence_seconds`: silence that ends a command. **Silence to end** in
  Settings -- the whole home's, an area's or a satellite's -- wins.
- `wyoming_port`: where Home Assistant's voice assistants reach the app
  (default 10800), with Parakeet's speech-to-text on the next port; 0 for
  neither.
- `debug_logging`: verbose logs.
