"""End of speech: hysteresis on a VAD's probabilities, the settings, and both
VADs on a recorded call."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from voice_call.endpoint import EndOfSpeech, Endpointer, MicroDetector, SileroDetector, micro_available, parse

CALL = Path(__file__).resolve().parent.parent / "calls/call-15788.wav"
CHUNK = 320  # bytes: 10 ms


class Scripted:
    """Probabilities given in advance, one per 10 ms chunk."""

    chunk_bytes = CHUNK

    def __init__(self, probabilities):
        self.probabilities = list(probabilities)

    def __call__(self, chunk):
        return self.probabilities.pop(0)


def run(probabilities, **settings):
    """The chunk the command ended on (its index), or None."""
    endpointer = Endpointer(EndOfSpeech(**settings), lambda vad: Scripted(probabilities))
    for i in range(len(probabilities)):
        if endpointer.process(bytes(CHUNK)):
            return i
    return None


def test_speech_then_silence():
    # 0.2 s of speech starts it; 0.3 s of silence after it ends it.
    probabilities = [0.9] * 20 + [0.1] * 40
    assert run(probabilities, speech_seconds=0.2, silence_seconds=0.3) == 20 + 30 - 1
    # Silence before speech ends nothing; too little speech never starts it.
    assert run([0.1] * 50 + [0.9] * 10 + [0.1] * 40, speech_seconds=0.2, silence_seconds=0.3) is None


def test_thresholds_have_hysteresis():
    # Between the thresholds a chunk is what the one before was: 0.4 holds
    # speech after speech, and is silence after silence.
    held = [0.9] * 20 + [0.4] * 40 + [0.1] * 40
    assert run(held, speech_seconds=0.2, silence_seconds=0.3, speech_threshold=0.5, silence_threshold=0.3) == 60 + 30 - 1
    assert run(held, speech_seconds=0.2, silence_seconds=0.3, speech_threshold=0.5, silence_threshold=0.5) == 20 + 30 - 1
    assert run([0.4] * 30 + [0.1] * 40, speech_seconds=0.2, speech_threshold=0.5, silence_threshold=0.3) is None


def test_a_short_sound_only_pauses_the_silence():
    # 0.2 s of silence, a 50 ms click, then silence: the click doesn't start
    # it over when speech must last 0.1 s to; it does when any speech will.
    probabilities = [0.9] * 20 + [0.1] * 20 + [0.9] * 5 + [0.1] * 40
    assert run(probabilities, speech_seconds=0.2, silence_seconds=0.3, resume_seconds=0.1) == 20 + 20 + 5 + 10 - 1
    assert run(probabilities, speech_seconds=0.2, silence_seconds=0.3, resume_seconds=0.0) == 20 + 20 + 5 + 30 - 1


def test_nothing_said_yet_is_skipped():
    # microVAD says nothing (None) while its window fills.
    assert run([None] * 30 + [0.9] * 20 + [0.1] * 40, speech_seconds=0.2, silence_seconds=0.3) == 30 + 20 + 30 - 1


def test_settings():
    base = EndOfSpeech(silence_seconds=0.8)
    assert parse(None, base) == base
    assert parse({"vad": "micro", "speech_threshold": "0.6", "silence_seconds": None}, base) == EndOfSpeech(
        vad="micro", speech_threshold=0.6, silence_seconds=0.8
    )
    for bad, why in (
        ({"vad": "webrtc"}, "one of silero, micro"),
        ({"loud": 1}, "unknown loud"),
        ({"silence_seconds": 0.1}, "from 0.3 to 5"),
        ({"speech_threshold": "high"}, "a number"),
        ({"silence_threshold": 0.6}, "above the speech threshold"),
    ):
        with pytest.raises(ValueError, match=why):
            parse(bad, base)


def read(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        return wav.readframes(wav.getnframes())


@pytest.mark.skipif(not CALL.exists(), reason="no recorded call")
@pytest.mark.parametrize("vad", ["silero", "micro"])
def test_a_recorded_command_ends(vad):
    if vad == "micro" and not micro_available():
        pytest.skip("no pymicro-vad")
    pcm = read(CALL)
    # The command, then the room: speech ends at about 2.35 s.
    audio = pcm[int(1.6 * 32000) :] + pcm[: int(0.6 * 32000)] * 4
    endpointer = Endpointer(EndOfSpeech(vad=vad, silence_seconds=0.5))
    assert isinstance(endpointer._detector, MicroDetector if vad == "micro" else SileroDetector)  # pylint: disable=protected-access
    for i in range(0, len(audio), 1024):
        if endpointer.process(audio[i : i + 1024]):
            ended = 1.6 + (i + 1024) / 32000
            break
    else:
        pytest.fail("never ended")
    # Silero ends half a second after the speech; microVAD, about half a second later still.
    assert (2.8 <= ended <= 3.1) if vad == "silero" else (3.2 <= ended <= 3.7), ended
