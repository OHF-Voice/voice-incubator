"""Microphone platform: learn-tv on a stereo (two-mic) source.

    microphone:
      - platform: learn_tv
        id: learn_tv_mics
        source: i2s_mics      # stereo, 32-bit, 16 kHz: the XMOS's two mics after AEC
        tau: 20s              # how long the inter-mic phase is averaged over
        warmup: 2s            # mic 0 alone until then

Output: four channels, 32-bit, 16 kHz, one for each of voice_labs's uses --
0 wake words, 1 the call, 2 the second capture, 3 the fallback detector --
delayed alike (16 ms). In learn mode (the XMOS's channels: both mics after
AEC) 0 and 1 are mic 0 with a null on the source that has played most of
the last ~tau (a TV), 2 and 3 mic 0 untouched; in stock mode (the XMOS's
AGC and NS taps) 0 and 2 are NS, 1 AGC, 3 silence.
"""

import esphome.codegen as cg
from esphome.components import audio, microphone
import esphome.config_validation as cv
from esphome.const import CONF_ID

from . import learn_tv_ns

DEPENDENCIES = ["microphone"]

CONF_SOURCE = "source"
CONF_TAU = "tau"
CONF_WARMUP = "warmup"
CONF_MODE = "mode"

LearnTVMicrophone = learn_tv_ns.class_("LearnTVMicrophone", microphone.Microphone, cg.Component)


def _stream_limits(config):
    # Always four channels, 32-bit, 16 kHz (what microphone sources check against).
    audio.set_stream_limits(
        min_bits_per_sample=32, max_bits_per_sample=32,
        min_channels=4, max_channels=4,
        min_sample_rate=16000, max_sample_rate=16000,
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    microphone.MICROPHONE_SCHEMA.extend(
        {
            cv.GenerateID(): cv.declare_id(LearnTVMicrophone),
            cv.Required(CONF_SOURCE): cv.use_id(microphone.Microphone),
            cv.Optional(CONF_TAU, default="20s"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_WARMUP, default="2s"): cv.positive_time_period_milliseconds,
            # stock: the XMOS's AGC and NS taps, routed only (set_mode at runtime).
            cv.Optional(CONF_MODE, default="learn"): cv.enum({"stock": 0, "learn": 1}, lower=True),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _stream_limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await microphone.register_microphone(var, config)
    source = await cg.get_variable(config[CONF_SOURCE])
    cg.add(var.set_source(source))
    cg.add(var.set_tau_ms(config[CONF_TAU].total_milliseconds))
    cg.add(var.set_warmup_ms(config[CONF_WARMUP].total_milliseconds))
    cg.add(var.set_mode(config[CONF_MODE]))
    cg.add(var.set_stream(32, 4, 16000))
    cg.add_define("USE_LEARN_TV")
