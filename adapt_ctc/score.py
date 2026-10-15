"""``adapt-ctc-score MANIFEST``: score recorded commands against what they should do.

A manifest names a config, says what each sentence should do, and lists the
recordings (files or globs, relative to the manifest):

    config: ../examples/home.yaml
    sentences:
      turn on the lights: {expect: HassTurnOn}
      turn the kitchen lights on: {expect: HassTurnOnArea, slots: {area: [kitchen]}}
      turn up the music: {expect: none}          # must not match
    recordings:
      - {path: "close/*/*.wav", tags: [close]}
      - {path: "close/*/command-91*.wav", tags: [music]}
      - {path: oov/blablablabla.wav, sentence: blablablabla}

A recording's sentence is its entry's ``sentence``, else its directory name,
else its file name, whichever the ``sentences`` table knows. Tags from every
entry that matches a file are combined. ``slots`` is checked only when given.

An entry may instead name a mic_tap corpus: one prompt captured on every
microphone at once, each prompt starting with the wake word.

    - corpus: /data/office-multicap/*-merged/prompts   # dirs with index.json
      alignments: alignments/          # <session>.jsonl: the wake word's span
      corrections: corrections.csv     # session,index,verdict,corrected
      channels: [pc-vpe.agc, mark2-sj201.ch1]           # default: every channel
      strip: "okay nabu,"              # prefix removed from each prompt
      split: wake                      # or: alignment
      tags: [corpus]

With ``split: alignment`` each prompt is cut where its alignment says the wake
word ends, on every channel. With ``split: wake`` (the default) the whole prompt
is decoded and the config's wake phrases placed in it, as ``--call`` does, with
the alignment only bounding where the wake word may end: the alignments come
from one channel, and other devices run up to ~0.2 s late against it. A correction
``fix`` replaces the prompt with what was actually said, and ``segment`` (a
window with nothing scorable in it) drops the prompt. Recordings are tagged
with their session, device and channel.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import glob
import hashlib
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .acoustic import SAMPLE_RATE, CtcModel
from .audio import load_wav
from .cli import DEFAULT_MODEL
from .config import ConfigError, _Loader, load_config
from .matcher import IntentResult, Recognition, Recognizer
from .embedding import DEFAULT_EMBEDDING_MODEL, load_embedder
from .tdt import DEFAULT_TDT_MODEL, load_transcriber

CORRECT = "correct"
FALSE_ACCEPT = "false accept"
MISSED = "missed"
WRONG_INTENT = "wrong intent"
WRONG_SLOTS = "wrong slots"
UNKNOWN = "unknown sentence"
WAKE_REJECTED = "wake rejected"

SPLIT_BOUND = 0.3
"""Seconds past an alignment's wake word end that the wake word may still end."""


@dataclass
class Expectation:
    intent: str | None
    slots: dict | None = None


@dataclass
class Recording:
    path: Path
    name: str
    """Path relative to the manifest, for display."""

    sentence: str | None
    tags: list[str] = field(default_factory=list)
    start: int | None = 0
    """Sample the command starts at: after the wake word for a corpus prompt,
    None when its alignment found no wake word."""

    split: str = "alignment"
    """``wake``: find where the wake word ends in the model's output, no later
    than shortly after ``start``, rather than cutting at ``start``."""

    @property
    def label(self) -> str:
        """The sentence, or for one the manifest does not know, its directory."""
        return self.sentence or self.path.parent.name


@dataclass
class Manifest:
    path: Path
    config: Path | None
    sentences: dict[str, Expectation]
    recordings: list[Recording]


@dataclass
class Scored:
    recording: Recording
    expect: Expectation | None
    outcome: str
    recognition: Recognition | None = None
    got: IntentResult | None = None
    detail: str = ""


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    with open(path, encoding="utf-8") as manifest_file:
        data = yaml.load(manifest_file, Loader=_Loader)  # noqa: S506 - a SafeLoader
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping")
    base = path.parent

    sentences: dict[str, Expectation] = {}
    for sentence, spec in (data.get("sentences") or {}).items():
        spec = spec or {}
        if not isinstance(spec, dict) or "expect" not in spec:
            raise ConfigError(f"sentences.{sentence}: needs 'expect' (an intent or none)")
        expect = spec["expect"]
        intent = None if expect in (None, "none", False) else str(expect)
        slots = spec.get("slots")
        if slots is not None and not isinstance(slots, dict):
            raise ConfigError(f"sentences.{sentence}.slots: expected a mapping")
        sentences[_key(sentence)] = Expectation(intent, slots)

    found: dict[Path, Recording] = {}
    corpus: list[Recording] = []
    for index, entry in enumerate(data.get("recordings") or []):
        if isinstance(entry, dict) and "corpus" in entry:
            corpus.extend(_corpus(entry, base, f"recordings[{index}]"))
            continue
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict) or "path" not in entry:
            raise ConfigError(f"recordings[{index}]: needs a 'path'")
        pattern = str(entry["path"])
        if any(c in pattern for c in "*?["):
            paths = sorted(Path(f) for f in glob.glob(str(base / pattern)))
        else:
            paths = [base / pattern]
        if not paths:
            print(f"adapt-ctc-score: warning: {pattern!r} matches nothing", file=sys.stderr)
        tags = [str(t) for t in entry.get("tags") or []]
        for file in paths:
            if not file.is_file():
                raise ConfigError(f"recordings[{index}]: {file} is not a file")
            recording = found.get(file)
            if recording is None:
                recording = found[file] = Recording(
                    file, os.path.relpath(file, base), None
                )
            for tag in tags:
                if tag not in recording.tags:
                    recording.tags.append(tag)
            explicit = entry.get("sentence")
            if explicit is not None:
                recording.sentence = _key(str(explicit))
            elif recording.sentence is None:
                for candidate in (file.parent.name, file.stem):
                    if _key(candidate) in sentences:
                        recording.sentence = _key(candidate)
                        break

    config = data.get("config")
    return Manifest(
        path=path,
        config=(base / config) if config else None,
        sentences=sentences,
        recordings=sorted(found.values(), key=lambda r: r.name) + corpus,
    )


def _corpus(entry: dict, base: Path, where: str) -> list[Recording]:
    """Every prompt of every matching mic_tap session, on the chosen channels."""
    known = {"corpus", "alignments", "corrections", "channels", "strip", "split", "tags"}
    unknown = set(entry) - known
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    if "alignments" not in entry:
        raise ConfigError(f"{where}: a corpus needs 'alignments' (where the wake word ends)")
    roots = sorted(Path(d) for d in glob.glob(str(base / str(entry["corpus"]))))
    if not roots:
        print(f"adapt-ctc-score: warning: {entry['corpus']!r} matches nothing", file=sys.stderr)
    alignments = base / str(entry["alignments"])
    fixes: dict[tuple[str, int], tuple[str, str]] = {}
    if entry.get("corrections"):
        with open(base / str(entry["corrections"]), encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                fixes[(row["session"], int(row["index"]))] = (
                    row["verdict"].strip(),
                    (row.get("corrected") or "").strip(),
                )
    channels = entry.get("channels")
    split = str(entry.get("split", "wake"))
    if split not in ("wake", "alignment"):
        raise ConfigError(f"{where}.split: expected 'wake' or 'alignment'")
    strip = _key(str(entry.get("strip") or ""))
    tags = [str(t) for t in entry.get("tags") or []]

    out = []
    for root in roots:
        session = root.parent.name
        speaker = session.removesuffix("-merged")
        with open(root / "index.json", encoding="utf-8") as handle:
            index = json.load(handle)
        spans = {}
        with open(alignments / f"{session}.jsonl", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                spans[row["prompt"]] = row["wake"]["end"] if row["wake"] else None
        for prompt in index["phrases"]:
            number = prompt["index"]
            verdict, corrected = fixes.get((speaker, number), ("ok", ""))
            if verdict == "segment":
                continue
            sentence = _key(corrected if verdict == "fix" else prompt["phrase"])
            if strip and sentence.startswith(strip):
                sentence = sentence[len(strip) :].strip()
            for channel, file in sorted(prompt["files"].items()):
                if channels is not None and channel not in channels:
                    continue
                device = channel.split(".", 1)[0]
                out.append(
                    Recording(
                        root / file,
                        f"{speaker}/{channel}/{number:04d}",
                        sentence,
                        [*tags, speaker, device, channel],
                        spans.get(number),
                        split,
                    )
                )
    return out


def check(manifest: Manifest, intents: set[str]) -> None:
    """Every expected intent must exist, or a typo would count as misses."""
    for sentence, expect in manifest.sentences.items():
        if expect.intent is not None and expect.intent not in intents:
            raise ConfigError(
                f"sentences.{sentence}: expect {expect.intent!r} is not an intent in the "
                "config (use 'none' for must-not-match)"
            )


def _key(sentence: str) -> str:
    return " ".join(sentence.lower().split())


def judge(expect: Expectation | None, recognition: Recognition) -> tuple[str, IntentResult | None, str]:
    """``(outcome, the match it is about, detail)`` for one recording."""
    best = recognition.best
    if expect is None:
        return UNKNOWN, best, ""
    if expect.intent is None:
        if best is None:
            return CORRECT, None, ""
        return FALSE_ACCEPT, best, ""
    if best is None:
        wanted = next((r for r in recognition.rejected if r.intent == expect.intent), None)
        reason = wanted.reasons[0] if wanted and wanted.reasons else "intent not in config"
        return MISSED, None, reason
    if best.intent != expect.intent:
        ranked = [m.intent for m in recognition.matches]
        if expect.intent in ranked:
            detail = f"{expect.intent} ranked {ranked.index(expect.intent) + 1}"
        else:
            wanted = next((r for r in recognition.rejected if r.intent == expect.intent), None)
            detail = wanted.reasons[0] if wanted and wanted.reasons else ""
        return WRONG_INTENT, best, detail
    if expect.slots is not None and _slots(best.slots) != _slots(expect.slots):
        return WRONG_SLOTS, best, f"expected {expect.slots}"
    return CORRECT, best, ""


def _slots(slots: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in slots.items():
        if isinstance(value, list):
            out[str(key)] = sorted(str(v) for v in value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[str(key)] = float(value)
        else:
            out[str(key)] = value
    return out


def score(
    manifest: Manifest,
    recognizer: Recognizer,
    calls: Path | None = None,
    verifier=None,
    cache: Path | None = None,
) -> list[Scored]:
    """Recognize every recording. With ``calls``, a recording ``command-N.wav``
    is replaced by its whole call ``calls/call-N.wav`` (when there is one): the
    wake word is verified and the command taken after it, instead of the cut
    the recording was made with."""
    from .wake import recognize_call  # pylint: disable=import-outside-toplevel

    results = []
    for recording in manifest.recordings:
        expect = manifest.sentences.get(recording.sentence) if recording.sentence else None
        call_path = None
        if calls is not None and recording.path.stem.startswith("command-"):
            call_path = calls / f"call-{recording.path.stem.split('-', 1)[1]}.wav"
            if not call_path.exists():
                call_path = None
        if call_path is not None:
            call = recognize_call(recognizer, verifier, load_wav(call_path))
            if call.command is None:
                results.append(
                    Scored(recording, expect, WAKE_REJECTED, None, None, call.wake.reason)
                )
                continue
            recognition = call.command
        elif recording.start is None:
            results.append(
                Scored(recording, expect, WAKE_REJECTED, None, None, "no wake word alignment")
            )
            continue
        elif recording.split == "wake":
            log_probs, frame_seconds = _log_probs(recognizer, recording.path, 0, cache)
            # The alignment's end, plus its slack, bounds the search.
            bound = int((recording.start / SAMPLE_RATE + SPLIT_BOUND) / frame_seconds)
            wake = verifier.verify_log_probs(log_probs, frame_seconds, max_end=bound)
            end = wake.end if wake.phrase is not None else int(
                recording.start / SAMPLE_RATE / frame_seconds
            )
            recognition = recognizer.recognize_log_probs(log_probs[end + 1 :], frame_seconds)
            _match_examples(
                recognizer, recognition, recording.path, int(round((end + 1) * frame_seconds * SAMPLE_RATE))
            )
        else:
            log_probs, frame_seconds = _log_probs(
                recognizer, recording.path, recording.start, cache
            )
            recognition = recognizer.recognize_log_probs(log_probs, frame_seconds)
            _match_examples(recognizer, recognition, recording.path, recording.start)
        outcome, got, detail = judge(expect, recognition)
        results.append(Scored(recording, expect, outcome, recognition, got, detail))
    return results


def _match_examples(
    recognizer: Recognizer, recognition: Recognition, path: Path, start: int
) -> None:
    """The example sentence fallback, reading the audio only when it runs."""
    audio = np.zeros(0, dtype=np.float32)
    if recognizer.transcriber is not None and recognizer.examples and not recognition.matches:
        audio = load_wav(path)[start:]
    recognizer.match_examples(recognition, audio)


def _log_probs(
    recognizer: Recognizer, path: Path, start: int, cache: Path | None
) -> tuple[np.ndarray, float]:
    """The model's output for a recording from sample ``start``, from
    ``cache`` when it was seen before: the acoustics don't change while a
    config is being tuned."""
    if cache is not None:
        key = hashlib.sha1(
            f"{path.resolve()}:{start}:{recognizer.model.model_dir}".encode()
        ).hexdigest()
        cached = cache / f"{key}.npz"
        if cached.is_file():
            with np.load(cached) as saved:
                return saved["log_probs"], float(saved["frame_seconds"])
    log_probs, frame_seconds = recognizer.model.log_probs(load_wav(path)[start:])
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        np.savez(cached, log_probs=log_probs, frame_seconds=frame_seconds)
    return log_probs, frame_seconds


# -- reporting -------------------------------------------------------------


def _match_text(result: IntentResult | None) -> str:
    if result is None:
        return "no match"
    if result.example_source == "TDT":
        return f"{result.intent} example '{result.example}' (TDT)"
    if result.example_source == "embedding":
        return f"{result.intent} example '{result.example}' (embedding {result.similarity:.2f})"
    if result.example is not None:
        return f"{result.intent} example '{result.example}' cost {result.cost:.2f}"
    required = ", ".join(
        f"{u.meaning.phrase} {u.hit.per_token:.2f}/tok" for u in result.spans if u.role == "require"
    )
    slots = f" {result.slots}" if result.slots else ""
    return f"{result.intent}{slots} cost {result.cost:.2f}" + (f" [{required}]" if required else "")


def _spread(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values):<3d} min {min(values):5.2f}  median {statistics.median(values):5.2f}"
        f"  max {max(values):5.2f}"
    )


def render(
    results: list[Scored], recognizer: Recognizer, manifest: Manifest, failures: bool = False
) -> str:
    settings = recognizer.config.settings
    lines = [
        f"manifest: {manifest.path}   config: {manifest.config}   search: {settings.search}",
        f"{len(results)} recordings, {len({r.recording.label for r in results})} sentences",
        "",
    ]

    width = max((len(r.recording.name) for r in results), default=10)
    for item in results:
        if failures and item.outcome in (CORRECT, UNKNOWN):
            continue
        mark = "ok " if item.outcome == CORRECT else "-- " if item.outcome == UNKNOWN else "XX "
        expect = item.expect.intent or "none" if item.expect else "?"
        tags = f" [{', '.join(item.recording.tags)}]" if item.recording.tags else ""
        transcript = item.recognition.transcript if item.recognition else ""
        line = f"{mark}{item.recording.name:<{width}}{tags}\n      expect {expect:<26} got {_match_text(item.got)}"
        if item.outcome not in (CORRECT, UNKNOWN):
            line += f"   <- {item.outcome.upper()}"
        line += f'\n      heard "{transcript}"'
        if item.recognition is not None and item.recognition.tdt_transcript is not None:
            line += f'\n      TDT heard "{item.recognition.tdt_transcript}"'
        if item.detail:
            line += f"\n      {item.detail}"
        lines.append(line)
    lines.append("")

    # By sentence and by tag.
    def table(title: str, groups: dict[str, list[Scored]], label: str) -> None:
        lines.append(title)
        name_width = max([len(label)] + [len(g) for g in groups])
        lines.append(f"   {label:<{name_width}}  expect                       n  correct  problems")
        for group, items in groups.items():
            counts = Counter(i.outcome for i in items)
            expect = items[0].expect.intent or "none" if items[0].expect else "?"
            if title.startswith("BY TAG"):
                expect = ""
            problems = ", ".join(
                f"{n} {o}" for o, n in counts.items() if o not in (CORRECT,)
            )
            lines.append(
                f"   {group:<{name_width}}  {expect:<26} {len(items):3d}  {counts[CORRECT]:7d}  {problems}"
            )
        lines.append("")

    by_sentence: dict[str, list[Scored]] = defaultdict(list)
    by_tag: dict[str, list[Scored]] = defaultdict(list)
    for item in results:
        by_sentence[item.recording.label].append(item)
        for tag in item.recording.tags or ["(untagged)"]:
            by_tag[tag].append(item)
    table("BY SENTENCE", dict(by_sentence), "sentence")
    table("BY TAG", dict(sorted(by_tag.items())), "tag")

    scored = [i for i in results if i.outcome != UNKNOWN]
    counts = Counter(i.outcome for i in scored)
    lines.append(
        "TOTAL   "
        + ", ".join(f"{counts[o]} {o}" for o in (CORRECT, FALSE_ACCEPT, MISSED, WRONG_INTENT, WRONG_SLOTS, WAKE_REJECTED) if counts[o] or o != WAKE_REJECTED)
        + f"   of {len(scored)}"
    )
    unknown = sorted({i.recording.label for i in results if i.outcome == UNKNOWN})
    if unknown:
        lines.append(f"not scored (add to the manifest's sentences): {', '.join(unknown)}")
    lines.append("")

    # Calibration: where the thresholds would have to sit on these recordings.
    # Keyword matches only: example matches have their own limit.
    good = [
        i.got for i in scored if i.outcome == CORRECT and i.got is not None and i.got.example is None
    ]
    bad = [
        i.got
        for i in scored
        if i.outcome in (FALSE_ACCEPT, WRONG_INTENT) and i.got is not None and i.got.example is None
    ]
    good_cost = [m.cost for m in good]
    bad_cost = [m.cost for m in bad]
    good_tok = [u.hit.per_token for m in good for u in m.spans if u.role == "require"]
    bad_tok = [u.hit.per_token for m in bad for u in m.spans if u.role == "require"]
    lines.append("CALIBRATION (on these recordings only)")
    lines.append(f"   cost of correct matches          {_spread(good_cost)}")
    lines.append(f"   cost of wrong matches            {_spread(bad_cost)}")
    if good_cost and bad_cost:
        if max(good_cost) < min(bad_cost):
            lines.append(
                f"   -> max_cost in [{max(good_cost):.2f}, {min(bad_cost):.2f}) rejects every wrong "
                f"match and keeps every correct one (now {settings.max_cost:g})"
            )
        else:
            lines.append("   -> no max_cost separates them: the costs overlap")
    lines.append(f"   required keyword, correct, /tok  {_spread(good_tok)}")
    lines.append(f"   required keyword, wrong, /tok    {_spread(bad_tok)}")
    if good_tok and bad_tok:
        if max(good_tok) < min(bad_tok):
            lines.append(
                f"   -> span_gate in [{max(good_tok):.2f}, {min(bad_tok):.2f}) would stop the wrong "
                f"keywords being heard at all (now {settings.span_gate:g})"
            )
        else:
            lines.append("   -> no span_gate separates them: the per-token costs overlap")
    return "\n".join(lines) + "\n"


def to_json(results: list[Scored]) -> dict[str, Any]:
    def match(result: IntentResult | None) -> dict | None:
        if result is None:
            return None
        return {
            "intent": result.intent,
            "slots": result.slots,
            "cost": round(result.cost, 3),
            **(
                {
                    "example": result.example,
                    "example_source": result.example_source,
                    "similarity": result.similarity,
                }
                if result.example is not None
                else {}
            ),
            "required": [
                {"phrase": u.meaning.phrase, "cost_per_token": round(u.hit.per_token, 3)}
                for u in result.spans
                if u.role == "require"
            ],
        }

    return {
        "recordings": [
            {
                "file": item.recording.name,
                "sentence": item.recording.sentence,
                "tags": item.recording.tags,
                "expect": item.expect.intent if item.expect else None,
                "outcome": item.outcome,
                "transcript": item.recognition.transcript if item.recognition else None,
                "tdt_transcript": item.recognition.tdt_transcript if item.recognition else None,
                "got": match(item.got),
                "detail": item.detail or None,
            }
            for item in results
        ],
        "totals": dict(Counter(item.outcome for item in results)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="adapt-ctc-score", description="Score recordings listed in a manifest."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--config", type=Path, help="Override the manifest's config")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(os.environ.get("ADAPT_CTC_MODEL", DEFAULT_MODEL)),
        help="CTC model directory (default: $ADAPT_CTC_MODEL or voice_call's int8 Parakeet)",
    )
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--tdt-model",
        type=Path,
        default=Path(os.environ.get("ADAPT_CTC_TDT_MODEL", DEFAULT_TDT_MODEL)),
        help="TDT model directory for intents with example sentences "
        "(default: $ADAPT_CTC_TDT_MODEL or voice_call's int8 Parakeet TDT)",
    )
    parser.add_argument(
        "--embedding-model",
        type=Path,
        default=Path(os.environ.get("ADAPT_CTC_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)),
        help="EmbeddingGemma 2 ONNX snapshot, for examples said in other words "
        "(default: $ADAPT_CTC_EMBEDDING_MODEL or the Hugging Face cache)",
    )
    parser.add_argument("--full-search", action="store_true")
    parser.add_argument(
        "--calls",
        type=Path,
        help="Directory of whole calls (call-N.wav for command-N.wav): verify the wake "
        "word and split with the CTC model instead of using the recorded cut",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Keep each recording's model output here, so re-scoring with another "
        "config skips the acoustic model",
    )
    parser.add_argument(
        "--failures", action="store_true", help="List only recordings that went wrong"
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Leave out sentences that expect an intent the config does not have "
        "(an opt-in one), instead of stopping",
    )
    parser.add_argument(
        "--only",
        metavar="GLOB",
        action="append",
        help="Score only recordings whose name or sentence matches (repeatable)",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print each recording's full recognition, as adapt-ctc does",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        config_path = args.config or manifest.config
        if config_path is None:
            raise ConfigError("no config: give one in the manifest or with --config")
        manifest.config = config_path
        config = load_config(config_path)
        if args.skip_missing:
            missing = {
                sentence
                for sentence, expect in manifest.sentences.items()
                if expect.intent is not None and expect.intent not in config.intents
            }
            manifest.recordings = [r for r in manifest.recordings if r.sentence not in missing]
            for sentence in missing:
                del manifest.sentences[sentence]
            if missing:
                print(
                    f"adapt-ctc-score: left out {len(missing)} sentence(s) expecting intents "
                    "the config does not have",
                    file=sys.stderr,
                )
        check(manifest, set(config.intents))
        if args.only:
            manifest.recordings = [
                r
                for r in manifest.recordings
                if any(
                    fnmatch.fnmatch(r.name, pattern) or fnmatch.fnmatch(r.label, pattern)
                    for pattern in args.only
                )
            ]
    except (ConfigError, OSError) as err:
        print(f"adapt-ctc-score: {err}", file=sys.stderr)
        return 2
    if args.full_search:
        config = replace(config, settings=replace(config.settings, search="full"))

    transcriber = embedder = None
    if any(intent.examples for intent in config.intents.values()):
        transcriber = load_transcriber(args.tdt_model, args.threads, "adapt-ctc-score")
        embedder = load_embedder(args.embedding_model, args.threads, "adapt-ctc-score")
    recognizer = Recognizer(
        config,
        CtcModel(args.model, num_threads=args.threads),
        transcriber=transcriber,
        embedder=embedder,
    )
    verifier = None
    if args.calls is not None or any(r.split == "wake" for r in manifest.recordings):
        from .wake import WakeVerifier  # pylint: disable=import-outside-toplevel

        if config.wake is None:
            print(
                "adapt-ctc-score: --calls and corpus 'split: wake' need a `wake` section "
                "in the config",
                file=sys.stderr,
            )
            return 2
        verifier = WakeVerifier(config.wake, recognizer.vocab, config.language)
    results = score(manifest, recognizer, args.calls, verifier, args.cache)
    if args.explain:
        from .report import render_text  # pylint: disable=import-outside-toplevel

        for item in results:
            if item.recognition is not None and not (args.failures and item.outcome == CORRECT):
                print(f"== {item.recording.name}: {item.recording.label} -> {item.outcome}")
                print(render_text(item.recognition))
    if args.json:
        print(json.dumps(to_json(results), indent=2))
    else:
        print(render(results, recognizer, manifest, args.failures), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
