"""Where the command starts: after the wake word, as Kaldi aligned it --
unless Kaldi refused it and the ratio overturned that, when its alignment
can end inside the wake word."""

from __future__ import annotations

from voice_labs.protocol import BYTE_RATE
from voice_labs.server import Call, VoiceLabsApp, WakeVerdict
from voice_labs.wake import VerifyResult

PREROLL = int(1.5 * BYTE_RATE)


def placed(split: float, accepted: bool, overturned: bool = False, offset: int = 0) -> float:
    call = Call(preroll_bytes=PREROLL, wake_word="okay_nabu")
    result = VerifyResult("okay_nabu", accepted=accepted, rejected_by=None if accepted else "prefix", split=split)
    verdict = WakeVerdict(accepted or overturned, result, offset, overturned=overturned)
    VoiceLabsApp._place_split(call, verdict)  # pylint: disable=protected-access
    return call.split_bytes / BYTE_RATE


def test_after_the_wake_word():
    assert placed(1.44, accepted=True) == 1.44
    assert placed(1.2, accepted=True) == 1.2  # as Kaldi aligned it
    assert placed(0.3, accepted=True, offset=PREROLL // 2) == 1.05  # the retry's window, later


def test_an_overturned_rejection_starts_at_the_detection():
    # "okay nabu, turn on overhead light", fired early: Kaldi put the wake
    # word's end at 1.17 s and refused it; overturned, the command began with
    # "bu" ("Able to turn on overhead light?").
    assert placed(1.17, accepted=False, overturned=True) == 1.5
    assert placed(1.6, accepted=False, overturned=True) == 1.6  # its end, when later
