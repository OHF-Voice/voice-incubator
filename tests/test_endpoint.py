"""End of speech: hysteresis on a VAD's probabilities, the settings, and both
VADs on a recorded call."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

import numpy as np

from voice_call.endpoint import (
    MAX_VAD_GAIN,
    SETTLE_SECONDS,
    EndOfSpeech,
    Endpointer,
    MicroDetector,
    SileroDetector,
    micro_available,
    parse,
    vad_gain,
)

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


def tone(dbfs: float, seconds: float = 1.0) -> bytes:
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 440 * t) * np.sqrt(2) * 10 ** (dbfs / 20) * 32768).astype("<i2").tobytes()


def test_a_quiet_wake_word_raises_what_silero_hears():
    # The wake word's loudest 32 ms to -30 dBFS: a quiet one 22 dB up, a loud
    # one left alone, nothing at all (or a capture far quieter) at most 16x.
    assert vad_gain(tone(-52.0)) == pytest.approx(10 ** (22 / 20), rel=0.01)
    assert vad_gain(tone(-20.0)) == 1.0
    assert vad_gain(bytes(32000)) == MAX_VAD_GAIN == vad_gain(tone(-80.0))
    assert vad_gain(b"") == 1.0

    class Heard:
        chunk_bytes = CHUNK

        def __init__(self):
            self.chunks = []

        def __call__(self, chunk):
            self.chunks.append(np.frombuffer(chunk, "<i2"))
            return 0.1

    # Silero's copy, raised and clipped; microVAD's as it came.
    for vad, expected in (("silero", [400, -32768, 32767]), ("micro", [100, -20000, 20000])):
        heard = Heard()
        endpointer = Endpointer(EndOfSpeech(vad=vad), lambda _: heard, gain=4.0)
        endpointer.process(np.array([100, -20000, 20000] * (CHUNK // 6) + [0], "<i2").tobytes())
        assert list(heard.chunks[0][:3]) == expected and endpointer.gain == (4.0 if vad == "silero" else 1.0)


def test_silero_hears_the_wake_word_first():
    # The wake word goes through the detector (10 chunks here) and counts for
    # nothing; nor do the 0.2 s after it, what Silero carries over from it.
    settle = round(SETTLE_SECONDS / 0.01)

    def run_warm(probabilities, vad="silero"):
        scripted = Scripted(probabilities)
        endpointer = Endpointer(
            EndOfSpeech(vad=vad, speech_seconds=0.2, silence_seconds=0.3), lambda _: scripted, warm=bytes(CHUNK * 10)
        )
        for i in range(len(scripted.probabilities)):
            if endpointer.process(bytes(CHUNK)):
                return i
        return None

    assert run_warm([0.9] * 10 + [0.9] * settle + [0.1] * 40) is None
    assert run_warm([0.9] * 10 + [0.9] * settle + [0.9] * 20 + [0.1] * 40) == settle + 20 + 30 - 1
    # microVAD starts as it did: its own window fills first.
    assert run_warm([0.9] * 20 + [0.1] * 40, vad="micro") == 20 + 30 - 1


@pytest.mark.skipif(not CALL.exists(), reason="no recorded call")
@pytest.mark.parametrize("pause", [0.0, 1.5])
def test_a_warm_start_waits_for_the_command(pause):
    # Straight after the wake word or after a pause, the command ends where it
    # did from a cold start: what the wake word left in Silero doesn't start it.
    pcm = read(CALL)
    room = pcm[: int(0.6 * 32000)] * 4
    audio = room[: int(pause * 32000)] + pcm[int(1.6 * 32000) :] + room
    wake = pcm[int(0.4 * 32000) : int(1.6 * 32000)]
    endpointer = Endpointer(EndOfSpeech(vad="silero", silence_seconds=0.5), warm=wake)
    ended = next(((i + 1024) / 32000 for i in range(0, len(audio), 1024) if endpointer.process(audio[i : i + 1024])), None)
    assert ended is not None and 2.8 <= 1.6 + ended - pause <= 3.1, ended


def run_watched(probabilities, score, watch=True, **settings):
    """Where the command ended (seconds), answering each check of a window
    ``(start, end)`` with ``score(start, end)`` as the server would, and the endpointer."""
    scripted = Scripted(probabilities)
    endpointer = Endpointer(EndOfSpeech(**settings), lambda _: scripted)
    endpointer.watch(watch)
    for _ in range(len(probabilities)):
        if endpointer.process(bytes(CHUNK)):
            return round(endpointer.elapsed, 2), endpointer
        due = endpointer.due()
        if due is not None and endpointer.verdict(score(*due), due[1]):
            return round(endpointer.elapsed, 2), endpointer
    return None, endpointer


def test_someone_else_talking_ends_the_command():
    # 3 s of speech, someone else's with no pause: the last second is checked
    # every 0.2 s from 1 s in, and two low scores in a row (the windows ending
    # 1.8 and 2.0 s) mean someone else. The scores fell halfway from 0.6 to
    # 0.1 at 1.7 s, so the speaker stopped half a window before, at 1.2 s --
    # quiet since for 0.8 s, so it ends there and then, not after them.
    talk = [0.9] * 300 + [0.1] * 100
    voice = lambda start, end: 0.6 if end <= 1.6 + 1e-9 else 0.1  # noqa: E731
    ended, endpointer = run_watched(talk, voice, speech_seconds=0.2, silence_seconds=0.5)
    assert ended == 2.0
    assert endpointer.cut == {"last_word": 1.2, "at": 2.0, "scores": [0.1, 0.1]}
    assert endpointer.other_from == pytest.approx(1.2)
    # After a pause (1.5-1.7 s): its start is their last word.
    paused = [0.9] * 150 + [0.1] * 20 + [0.9] * 180 + [0.1] * 100
    voice = lambda start, end: 0.6 if end <= 2.0 + 1e-9 else 0.1  # noqa: E731
    ended, endpointer = run_watched(paused, voice, speech_seconds=0.2, silence_seconds=0.5)
    assert ended == 2.4 and endpointer.cut["last_word"] == 1.5
    # Not watched (nobody identified): as before.
    assert run_watched(talk, voice, watch=False, speech_seconds=0.2, silence_seconds=0.5)[0] == 3.5
    # The speaker going on, or one low score at a time: never cut.
    for score in (lambda start, end: 0.6, lambda start, end: 0.1 if round(end * 5) % 2 else 0.6):  # noqa: E731
        ended, endpointer = run_watched(talk, score, speech_seconds=0.2, silence_seconds=0.5)
        assert ended == 3.5 and endpointer.cut is None


def test_a_pause_starts_a_run_over():
    # 1.2 s of speech, a 1 s pause, 1.2 s more: the windows in the pause are
    # too little speech to check, and the low score before it and the one
    # after it are not a run.
    talk = [0.9] * 120 + [0.1] * 100 + [0.9] * 120 + [0.1] * 200
    voice = lambda start, end: 0.1 if round(end, 1) in (1.4, 3.2) else 0.6  # noqa: E731
    ended, endpointer = run_watched(talk, voice, speech_seconds=0.2, silence_seconds=1.5)
    assert ended == 4.9 and endpointer.cut is None
    # The command's first second is checked; nothing before it.
    scripted = Scripted([0.9] * 99)
    endpointer = Endpointer(EndOfSpeech(), lambda _: scripted)
    endpointer.watch()
    for _ in range(99):
        endpointer.process(bytes(CHUNK))
        assert endpointer.due() is None


@pytest.mark.skipif(not CALL.exists(), reason="no recorded call")
def test_a_quiet_command_ends():
    # The recorded call 24 dB down, as a capture without AGC carries it:
    # Silero never hears the command start, until it hears it at the level
    # its wake word says.
    pcm = (np.frombuffer(read(CALL), "<i2") // 16).astype("<i2").tobytes()
    audio = pcm[int(1.6 * 32000) :] + pcm[: int(0.6 * 32000)] * 4
    gain = vad_gain(pcm[int(0.4 * 32000) : int(1.6 * 32000)])
    assert 8 < gain <= MAX_VAD_GAIN
    for gain, ends in ((1.0, False), (gain, True)):
        endpointer = Endpointer(EndOfSpeech(vad="silero", silence_seconds=0.5), gain=gain)
        ended = next((1.6 + (i + 1024) / 32000 for i in range(0, len(audio), 1024) if endpointer.process(audio[i : i + 1024])), None)
        assert (ended is not None and 2.8 <= ended <= 3.1) if ends else ended is None, (gain, ended)


def test_why_a_call_timed_out():
    import time  # pylint: disable=import-outside-toplevel

    from voice_call.server import Call, VoiceCallApp  # pylint: disable=import-outside-toplevel

    call = Call(preroll_bytes=48000, wake_word="okay_nabu")
    call.audio += bytes(32000 * 3)
    assert VoiceCallApp._timed_out(call)[0] == "the audio stopped arriving 3.0 s in"  # pylint: disable=protected-access
    call.last_audio = time.monotonic() - 5
    assert VoiceCallApp._timed_out(call)[0] == "the audio stopped arriving 3.0 s in"  # pylint: disable=protected-access
    call.last_audio = time.monotonic()
    assert VoiceCallApp._timed_out(call)[0] == "the wake word was still being checked"  # pylint: disable=protected-access
    call.endpointer = Endpointer(EndOfSpeech(), lambda vad: Scripted([0.1] * 10))
    call.endpointer.process(bytes(CHUNK * 10))
    why, details = VoiceCallApp._timed_out(call)  # pylint: disable=protected-access
    assert why == "no speech was heard" and details["speech_started"] is False
    call.endpointer = Endpointer(EndOfSpeech(), lambda vad: Scripted([0.9] * 300))
    call.endpointer.process(bytes(CHUNK * 300))
    why, details = VoiceCallApp._timed_out(call)  # pylint: disable=protected-access
    assert why == "3.0 s of speech, never 1 s of silence" and details["speech_seconds"] == 3.0


def test_stt_hears_the_call_from_kaldis_split():
    import numpy as np  # pylint: disable=import-outside-toplevel

    from voice_call.server import AEC_LEAD_SAMPLES, stt_audio_for  # pylint: disable=import-outside-toplevel

    rng = np.random.default_rng(1)
    sound = (rng.standard_normal(64000) * 3000).clip(-32767, 32767)
    # AEC runs ahead of AGC, ~25 dB lower; both pre-rolls end at the detection.
    for preroll, aec_preroll in ((24000, 24000), (24000, 25600)):
        extra = aec_preroll - preroll
        agc = np.concatenate([np.zeros(AEC_LEAD_SAMPLES), sound])[:60000]
        aec = np.concatenate([np.zeros(extra), sound * 0.056])[: 60000 + extra]
        split = 30000  # Kaldi's: the engine cut it earlier, at 28000
        command = agc[28000:] / 32768.0
        agc_pcm, aec_pcm = agc.astype("<i2").tobytes(), aec.astype("<i2").tobytes()
        wanted = agc.astype("<i2")[split:] / 32768.0
        heard, info = stt_audio_for(agc_pcm, aec_pcm, split, preroll, aec_preroll)(command)
        assert info == {"channel": "agc", "from": 1.875} and np.allclose(heard, wanted)
        heard, info = stt_audio_for(agc_pcm, aec_pcm, split, preroll, aec_preroll, "aec")(command)
        assert info == {"channel": "aec", "from": 1.875} and len(heard) == len(wanted)
        assert np.corrcoef(heard, wanted)[0, 1] > 0.99  # the same stretch, as captured
        assert abs(np.abs(heard).max() / np.abs(wanted).max() - 0.056) < 0.001
        heard, info = stt_audio_for(agc_pcm, aec_pcm, split, preroll, aec_preroll, "aec", boost=True)(command)
        assert info["channel"] == "aec" and 20 <= info["gain_db"] <= 30
        assert abs(20 * np.log10(np.abs(heard).max()) + 3) < 0.1  # a -3 dBFS peak
    # Captures delayed alike (learn-tv's: the satellite says lead 0).
    aec = (sound[:60000] * 0.056).astype("<i2").tobytes()
    agc_pcm = sound[:60000].astype("<i2").tobytes()
    heard, info = stt_audio_for(agc_pcm, aec, split, preroll, preroll, "aec", lead=0)(command)
    assert np.corrcoef(heard, sound[split:60000] / 32768.0)[0, 1] > 0.99
    agc_pcm = agc.astype("<i2").tobytes()
    # Without an AEC capture: the AGC one, and why.
    heard, info = stt_audio_for(agc_pcm, b"", split, preroll, preroll, "aec")(command)
    assert info == {"channel": "agc", "from": 1.875, "why": "no AEC capture"} and np.allclose(heard, wanted)
