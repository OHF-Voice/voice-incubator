"""learn-tv: a two-mic null on the long-running source (a microphone platform).

Ported from mic_tap's my_components_mic_tap/learn_tv; the spec is
mic_tap/okay-nabu-tv/LEARN_TV_IMPLEMENTATION.md.
"""

import esphome.codegen as cg

CODEOWNERS = ["@synesthesiam"]

learn_tv_ns = cg.esphome_ns.namespace("learn_tv")
