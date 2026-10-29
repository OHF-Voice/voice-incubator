"""One wake word, several satellites: the one that heard it best answers."""

from __future__ import annotations

import asyncio

import numpy as np

from voice_labs.arbitration import Arbiter, levels


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_alone_it_answers_at_once():
    arbiter = Arbiter(clock=Clock())
    utterance = arbiter.heard("kitchen", "okay_nabu", 0.9)
    assert asyncio.run(arbiter.answers(utterance, "kitchen", True)) is True
    assert asyncio.run(arbiter.answers(None, "kitchen", True)) is True  # a replay: no one else
    assert asyncio.run(Arbiter().answers(Arbiter().heard("x", "w"), "x", False)) is False


def test_the_one_that_heard_it_best_answers_whichever_is_verified_first():
    for first in ("kitchen", "office"):
        clock = Clock()
        arbiter = Arbiter(clock=clock)
        utterance = arbiter.heard("kitchen", "okay_nabu", 0.8)
        clock.now += 0.3
        assert arbiter.heard("office", "okay_nabu", 0.6) is utterance
        arbiter.measured(utterance, "kitchen", 9.0)
        arbiter.measured(utterance, "office", 21.5)
        second = "office" if first == "kitchen" else "kitchen"

        async def verified():
            return [await arbiter.answers(utterance, first, True), await arbiter.answers(utterance, second, True)]

        answered = dict(zip((first, second), asyncio.run(verified())))
        assert answered == {"office": True, "kitchen": False} and utterance.winner == "office"


def test_one_that_starts_after_the_window_or_another_word_is_its_own():
    clock = Clock()
    arbiter = Arbiter(clock=clock)
    first = arbiter.heard("kitchen", "okay_nabu")
    assert arbiter.heard("office", "hey_jarvis") is not first  # another word: another person
    clock.now += 1.5
    later = arbiter.heard("office", "okay_nabu")
    assert later is not first
    assert asyncio.run(arbiter.answers(later, "office", True)) is True


def test_the_one_chosen_refused_the_next_best_answers():
    arbiter = Arbiter(clock=Clock())
    utterance = arbiter.heard("kitchen", "okay_nabu")
    arbiter.heard("office", "okay_nabu")
    arbiter.measured(utterance, "kitchen", 8.0)
    arbiter.measured(utterance, "office", 20.0)

    async def verified():
        kitchen = asyncio.ensure_future(arbiter.answers(utterance, "kitchen", True))
        await asyncio.sleep(0.05)
        assert not kitchen.done()  # the office was chosen, and is still being verified
        office = await arbiter.answers(utterance, "office", False)
        return await kitchen, office

    assert asyncio.run(verified()) == (True, False)


def test_one_heard_after_the_choice_answers_nothing():
    clock = Clock()
    arbiter = Arbiter(clock=clock)
    utterance = arbiter.heard("kitchen", "okay_nabu")
    arbiter.measured(utterance, "kitchen", 8.0)
    assert asyncio.run(arbiter.answers(utterance, "kitchen", True)) is True
    clock.now += 0.4
    assert arbiter.heard("office", "okay_nabu") is utterance
    arbiter.measured(utterance, "office", 30.0)  # nearer, but too late
    assert asyncio.run(arbiter.answers(utterance, "office", True)) is False


def test_levels_say_how_far_the_wake_word_stands_above_the_background():
    rng = np.random.default_rng(1)
    rate = 16000
    noise = rng.normal(0, 0.003, int(1.5 * rate))
    word = np.zeros_like(noise)
    word[int(0.6 * rate):] = 0.2 * np.sin(np.arange(int(0.9 * rate)) * 2 * np.pi * 200 / rate)

    def pcm(gain):
        return (np.clip(noise + gain * word, -1, 1) * 32767).astype("<i2").tobytes()

    near, far = levels(pcm(1.0)), levels(pcm(0.1))
    assert near[0] - near[1] > far[0] - far[1] + 15  # 20 dB nearer
    assert levels(b"\x00\x00" * 100) is None
