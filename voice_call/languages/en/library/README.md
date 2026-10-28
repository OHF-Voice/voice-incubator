# The command library

Example commands, offered in the web UI's library. Adding one copies it into
the home's commands, where it can be changed like any other command.

Each entry is a command (see voice_call/commands.py) plus:
```
  title:     what the library calls it
  needs:     what to set after adding it, shown in the UI
  variants:  its versions, each added on its own, for what it acts on:
               here    the satellite it is said to, or its area (nothing named)
               device  a device by name
               place   an area or floor by name
             Each is the command's keys with its own on top (null removes
             one), with the id <command>_<variant>.
  formerly:  ids it had before it had versions (where the Commands page
             lists a command added then)
```

Keywords follow adapt-ctc's core intents (adapt_ctc/examples/intents/core.yaml),
which were tuned on the office-multicap corpus.

What acts on devices runs Home Assistant's intents (intent:), as Assist
would: only on what is exposed to Assist, and the unavailable said.
