"""Experimental voice_labs component.

Streams microphone audio to a Home Assistant custom component over a plain TCP
connection and plays back the audio it sends in return.

Wake word detection runs here too, on mixednet-executor rather than TFLite. No
model is compiled in: the app pushes one over the same socket after connecting,
so the wake word can be changed without reflashing.
"""

from esphome import automation
import esphome.codegen as cg
from esphome.components import esp32, microphone, psram, socket, speaker
import esphome.config_validation as cv
from esphome.const import (
    CONF_ID,
    CONF_MICROPHONE,
    CONF_PORT,
    CONF_SPEAKER,
    CONF_TRIGGER_ID,
)

CODEOWNERS = ["@synesthesiam"]
DEPENDENCIES = ["esp32", "microphone", "network", "speaker"]
AUTO_LOAD = ["json", "ring_buffer"]

CONF_CALL_TIMEOUT = "call_timeout"
CONF_FEATURE_STEP_SIZE = "feature_step_size"
CONF_ON_CALL_END = "on_call_end"
CONF_ON_CALL_START = "on_call_start"
CONF_ON_CONNECTED = "on_connected"
CONF_ON_DISCONNECTED = "on_disconnected"
CONF_ON_EVENT = "on_event"
CONF_ON_MODEL_LOADED = "on_model_loaded"
CONF_OUTPUT_BUFFER = "output_buffer"
CONF_PREROLL = "preroll"
CONF_STATUS_INTERVAL = "status_interval"
CONF_TASK_STACK_IN_PSRAM = "task_stack_in_psram"
CONF_VOLUME = "volume"
CONF_AEC_MICROPHONE = "aec_microphone"
CONF_AEC_LEAD = "aec_lead"
CONF_WAKE_MICROPHONE = "wake_microphone"

# The only format the prototype speaks on the wire.
SAMPLE_RATE = 16000

voice_labs_ns = cg.esphome_ns.namespace("voice_labs")
VoiceLabs = voice_labs_ns.class_("VoiceLabs", cg.Component)
CallEventData = voice_labs_ns.struct("CallEventData")

StartCallAction = voice_labs_ns.class_("StartCallAction", automation.Action)
EndCallAction = voice_labs_ns.class_("EndCallAction", automation.Action)
CancelCallAction = voice_labs_ns.class_("CancelCallAction", automation.Action)
SendEventAction = voice_labs_ns.class_("SendEventAction", automation.Action)
EndIntercomAction = voice_labs_ns.class_("EndIntercomAction", automation.Action)
IsInCallCondition = voice_labs_ns.class_("IsInCallCondition", automation.Condition)
IsConnectedCondition = voice_labs_ns.class_("IsConnectedCondition", automation.Condition)
IsIntercomCondition = voice_labs_ns.class_("IsIntercomCondition", automation.Condition)

CallStartTrigger = voice_labs_ns.class_("CallStartTrigger", automation.Trigger.template())


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(VoiceLabs),
        # Buffered and streamed to the app. Passive: the wake word source below
        # is what actually keeps the microphone running.
        cv.Required(CONF_MICROPHONE): microphone.microphone_source_schema(
            min_bits_per_sample=16,
            max_bits_per_sample=16,
            min_channels=1,
            max_channels=1,
        ),
        # Fed to the wake word models. On a VPE this is the noise-suppressed
        # channel, which is quieter than the AGC channel streamed to the app.
        cv.Required(CONF_WAKE_MICROPHONE): microphone.microphone_source_schema(
            min_bits_per_sample=16,
            max_bits_per_sample=16,
            min_channels=1,
            max_channels=1,
        ),
        # Optional second capture of the same call, tapped earlier in the XMOS
        # pipeline. Recorded alongside the first; the command is still split
        # from the first.
        cv.Optional(CONF_AEC_MICROPHONE): microphone.microphone_source_schema(
            min_bits_per_sample=16,
            max_bits_per_sample=16,
            min_channels=1,
            max_channels=1,
        ),
        # How far the second capture runs ahead of the first, told to the app:
        # the XMOS's AEC tap leads its AGC tap by 56.5 ms (the app's default);
        # two channels delayed alike say 0ms.
        cv.Optional(CONF_AEC_LEAD): cv.positive_time_period_microseconds,
        # Must match what the pushed models were trained with; the device
        # rejects a model whose manifest disagrees.
        cv.Optional(CONF_FEATURE_STEP_SIZE, default=10): cv.int_range(min=5, max=30),
        cv.Optional(CONF_TASK_STACK_IN_PSRAM, default=False): cv.boolean,
        cv.Required(CONF_SPEAKER): cv.use_id(speaker.Speaker),
        cv.Optional(CONF_PORT, default=6055): cv.port,
        # Audio kept before the wake word fires, replayed at the head of the call.
        cv.Optional(CONF_PREROLL, default="1.5s"): cv.positive_time_period_milliseconds,
        # Playback jitter buffer; also the number reported back as free space.
        cv.Optional(
            CONF_OUTPUT_BUFFER, default="1s"
        ): cv.positive_time_period_milliseconds,
        cv.Optional(
            CONF_CALL_TIMEOUT, default="30s"
        ): cv.positive_time_period_milliseconds,
        cv.Optional(
            CONF_STATUS_INTERVAL, default="200ms"
        ): cv.positive_time_period_milliseconds,
        # The VPE's amplifier audibly distorts near full scale.
        cv.Optional(CONF_VOLUME, default=0.7): cv.percentage,
        cv.Optional(CONF_ON_CALL_START): automation.validate_automation(single=True),
        cv.Optional(CONF_ON_CALL_END): automation.validate_automation(single=True),
        cv.Optional(CONF_ON_EVENT): automation.validate_automation(single=True),
        cv.Optional(CONF_ON_CONNECTED): automation.validate_automation(single=True),
        cv.Optional(CONF_ON_DISCONNECTED): automation.validate_automation(single=True),
        cv.Optional(CONF_ON_MODEL_LOADED): automation.validate_automation(single=True),
    }
).extend(cv.COMPONENT_SCHEMA)


def _consume_sockets(config):
    socket.consume_sockets(1, "voice_labs", socket.SocketType.TCP_LISTEN)(config)
    socket.consume_sockets(1, "voice_labs")(config)
    return config


FINAL_VALIDATE_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.Required(CONF_MICROPHONE): microphone.final_validate_microphone_source_schema(
                "voice_labs", sample_rate=SAMPLE_RATE
            ),
            cv.Required(CONF_WAKE_MICROPHONE): microphone.final_validate_microphone_source_schema(
                "voice_labs", sample_rate=SAMPLE_RATE
            ),
            cv.Optional(CONF_AEC_MICROPHONE): microphone.final_validate_microphone_source_schema(
                "voice_labs", sample_rate=SAMPLE_RATE
            ),
        },
        extra=cv.ALLOW_EXTRA,
    ),
    _consume_sockets,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    # Passive: the wake word source is the one that starts and stops the
    # microphone, so the pre-roll buffer fills without a second start/stop ref.
    mic_source = await microphone.microphone_source_to_code(
        config[CONF_MICROPHONE], passive=True
    )
    cg.add(var.set_microphone_source(mic_source))

    wake_source = await microphone.microphone_source_to_code(
        config[CONF_WAKE_MICROPHONE], passive=False
    )
    cg.add(var.set_wake_microphone_source(wake_source))

    if CONF_AEC_MICROPHONE in config:
        aec_source = await microphone.microphone_source_to_code(
            config[CONF_AEC_MICROPHONE], passive=True
        )
        cg.add(var.set_aec_microphone_source(aec_source))
    if CONF_AEC_LEAD in config:
        cg.add(var.set_aec_lead_samples(round(config[CONF_AEC_LEAD].total_microseconds * SAMPLE_RATE / 1_000_000)))
    cg.add(var.set_feature_step_size(config[CONF_FEATURE_STEP_SIZE]))

    if config[CONF_TASK_STACK_IN_PSRAM]:
        cg.add(var.set_wake_task_stack_in_psram(True))
        psram.request_external_task_stack()

    # The spectrogram frontend the models were trained against. mixednet-executor
    # needs nothing else at runtime, so no TFLite interpreter is pulled in.
    esp32.add_idf_component(name="esphome/esp-micro-speech-features", ref="1.2.3")

    spk = await cg.get_variable(config[CONF_SPEAKER])
    cg.add(var.set_speaker(spk))

    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_preroll_ms(config[CONF_PREROLL].total_milliseconds))
    cg.add(var.set_output_buffer_ms(config[CONF_OUTPUT_BUFFER].total_milliseconds))
    cg.add(var.set_call_timeout_ms(config[CONF_CALL_TIMEOUT].total_milliseconds))
    cg.add(var.set_status_interval_ms(config[CONF_STATUS_INTERVAL].total_milliseconds))
    cg.add(var.set_volume(config[CONF_VOLUME]))

    if CONF_ON_CALL_START in config:
        await automation.build_automation(
            var.get_call_start_trigger(),
            [(cg.std_string, "wake_word")],
            config[CONF_ON_CALL_START],
        )

    if CONF_ON_CALL_END in config:
        await automation.build_automation(
            var.get_call_end_trigger(),
            [(cg.std_string, "reason")],
            config[CONF_ON_CALL_END],
        )

    if CONF_ON_EVENT in config:
        await automation.build_automation(
            var.get_event_trigger(),
            [(cg.std_string, "event_type"), (CallEventData, "event_data")],
            config[CONF_ON_EVENT],
        )

    if CONF_ON_CONNECTED in config:
        await automation.build_automation(
            var.get_connected_trigger(), [], config[CONF_ON_CONNECTED]
        )

    if CONF_ON_DISCONNECTED in config:
        await automation.build_automation(
            var.get_disconnected_trigger(), [], config[CONF_ON_DISCONNECTED]
        )

    if CONF_ON_MODEL_LOADED in config:
        await automation.build_automation(
            var.get_model_loaded_trigger(),
            [(cg.std_string, "model_id")],
            config[CONF_ON_MODEL_LOADED],
        )


VOICE_LABS_ACTION_SCHEMA = cv.Schema({cv.GenerateID(): cv.use_id(VoiceLabs)})


@automation.register_action(
    "voice_labs.start",
    StartCallAction,
    cv.maybe_simple_value(
        {
            cv.GenerateID(): cv.use_id(VoiceLabs),
            cv.Optional("wake_word", default=""): cv.templatable(cv.string),
        },
        key="wake_word",
    ),
    synchronous=True,
)
async def start_call_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    templ = await cg.templatable(config["wake_word"], args, cg.std_string)
    cg.add(var.set_wake_word(templ))
    return var


@automation.register_action(
    "voice_labs.send_event",
    SendEventAction,
    cv.maybe_simple_value(
        {
            cv.GenerateID(): cv.use_id(VoiceLabs),
            cv.Required("type"): cv.templatable(cv.string),
        },
        key="type",
    ),
    synchronous=True,
)
async def send_event_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    templ = await cg.templatable(config["type"], args, cg.std_string)
    cg.add(var.set_type(templ))
    return var


@automation.register_action(
    "voice_labs.end",
    EndCallAction,
    cv.maybe_simple_value(
        {
            cv.GenerateID(): cv.use_id(VoiceLabs),
            cv.Optional("reason", default="device"): cv.templatable(cv.string),
        },
        key="reason",
    ),
    synchronous=True,
)
async def end_call_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    templ = await cg.templatable(config["reason"], args, cg.std_string)
    cg.add(var.set_reason(templ))
    return var


@automation.register_action(
    "voice_labs.cancel", CancelCallAction, VOICE_LABS_ACTION_SCHEMA, synchronous=True
)
async def cancel_call_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


@automation.register_condition(
    "voice_labs.is_in_call", IsInCallCondition, VOICE_LABS_ACTION_SCHEMA
)
async def is_in_call_to_code(config, condition_id, template_arg, args):
    var = cg.new_Pvariable(condition_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


@automation.register_condition(
    "voice_labs.is_connected", IsConnectedCondition, VOICE_LABS_ACTION_SCHEMA
)
async def is_connected_to_code(config, condition_id, template_arg, args):
    var = cg.new_Pvariable(condition_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


@automation.register_action(
    "voice_labs.end_intercom", EndIntercomAction, VOICE_LABS_ACTION_SCHEMA, synchronous=True
)
async def end_intercom_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


@automation.register_condition(
    "voice_labs.is_intercom", IsIntercomCondition, VOICE_LABS_ACTION_SCHEMA
)
async def is_intercom_to_code(config, condition_id, template_arg, args):
    var = cg.new_Pvariable(condition_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var
