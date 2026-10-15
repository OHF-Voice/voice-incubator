"""Human-readable and JSON renderings of a recognition."""

from __future__ import annotations

from typing import Any

from .matcher import IntentResult, Recognition, SpanUse
from .numbers import to_python
from .spotter import Hit


def _span(start: int, end: int, frame_seconds: float) -> str:
    return f"{start * frame_seconds:.2f}-{(end + 1) * frame_seconds:.2f}s"


def render_text(recognition: Recognition, show_spans: bool = False) -> str:
    spotting = recognition.spotting
    fs = spotting.frame_seconds
    lines = [
        f'heard: "{recognition.transcript}"  '
        f"({recognition.duration:.2f}s, {spotting.num_frames} frames of {fs * 1000:.0f} ms)",
        "timing: "
        + ", ".join(f"{name} {seconds * 1000:.0f} ms" for name, seconds in recognition.timings.items())
        + f"  ({spotting.visits:,} arc updates)",
    ]
    if recognition.lead_in:
        lines.append(f"lead-in ignored: '{recognition.lead_in}'")
    if recognition.tdt_transcript is not None:
        lines.append(f'TDT heard: "{recognition.tdt_transcript}"')
    lines.append("")

    if recognition.matches:
        lines.append("MATCHED")
        for rank, result in enumerate(recognition.matches, 1):
            lines.extend(_render_match(rank, result, fs))
    else:
        lines.append("NO INTENT MATCHED")
    lines.append("")

    if recognition.rejected:
        lines.append("REJECTED")
        for result in recognition.rejected:
            lines.append(f"   {result.intent}")
            for reason in result.reasons:
                lines.append(f"      - {reason}")

    if show_spans:
        lines.append("")
        lines.append("SPANS (gate-passing placements of every phrase)")
        for hit in spotting.hits:
            lines.append("   " + _render_hit(hit, fs))
    return "\n".join(lines).rstrip() + "\n"


def _render_match(rank: int, result: IntentResult, fs: float) -> list[str]:
    if result.example_source == "TDT":
        return [f"{rank:>2}. {result.intent:<24} example '{result.example}' (TDT transcript)"]
    if result.example_source == "embedding":
        return [
            f"{rank:>2}. {result.intent:<24} example '{result.example}' "
            f"(embedding, similarity {result.similarity:.2f})",
            *(f"      note: {note}" for note in result.notes),
        ]
    parts = [f"acoustic {result.acoustic:.2f}"]
    if result.prior:
        parts.append(f"prior {result.prior:.2f}")
    parts.append(f"unexplained {result.unexplained_cost:.2f}")
    head = f"{rank:>2}. {result.intent:<24} cost {result.cost:6.2f}  ({', '.join(parts)})"
    if result.ambiguous_with:
        head += f"  AMBIGUOUS with {', '.join(result.ambiguous_with)}"
    lines = [head]
    if result.example is not None:
        lines.append(f"      example '{result.example}' (CTC evidence)")
    for use in result.spans:
        lines.append("      " + _render_use(use, fs))
    if result.slots:
        slots = ", ".join(f"{k}={v}" for k, v in result.slots.items())
        lines.append(f"      slots: {slots}")
    for word in result.unexplained:
        kind = "unused" if word.important else "unexplained"
        if word.name_cue is not None:
            kind = "unusable name" if word.heard_as else "unknown name"
        heard_as = f" [{', '.join(word.heard_as)}]" if word.heard_as else ""
        lines.append(
            f"      {kind}: '{word.text}' at {_span(word.start, word.end, fs)}{heard_as}"
        )
    for note in result.notes:
        lines.append(f"      note: {note}")
    return lines


def _render_use(use: SpanUse, fs: float) -> str:
    meaning = use.meaning
    what = f"'{meaning.phrase}'"
    if use.role == "name":
        what += f" -> {meaning.name_type} {meaning.name_id}"
    elif use.role == "number":
        what += f" -> {to_python(meaning.value)}"
    if use.part is not None:
        what += f" (part {use.part[1] + 1}/{use.part[2]})"
    hit = use.hit
    return (
        f"{use.role:<8} {what:<40} {_span(hit.start, hit.end, fs):>13}  "
        f"cost {hit.penalty:5.2f}  ({hit.per_token:.2f}/token)"
    )


def _render_hit(hit: Hit, fs: float) -> str:
    labels = ", ".join(dict.fromkeys(m.label() for m in hit.meanings))
    if hit.dominated_by:
        status = f"outscored by '{hit.dominated_by.phrase}' ({hit.dominated_by.penalty:.2f})"
    elif hit.inside:
        status = f"part of '{hit.inside.phrase}'"
    else:
        status = "heard"
    return (
        f"{_span(hit.start, hit.end, fs):>13}  {hit.penalty:5.2f} ({hit.per_token:.2f}/tok)  "
        f"{labels:<44} {status}"
    )


def to_json(recognition: Recognition, include_spans: bool = False) -> dict[str, Any]:
    fs = recognition.spotting.frame_seconds

    def times(start: int, end: int) -> dict[str, float]:
        return {"start": round(start * fs, 3), "end": round((end + 1) * fs, 3)}

    def use_json(use: SpanUse) -> dict[str, Any]:
        out: dict[str, Any] = {
            "role": use.role,
            "phrase": use.meaning.phrase,
            **times(use.hit.start, use.hit.end),
            "cost": round(use.hit.penalty, 3),
            "cost_per_token": round(use.hit.per_token, 3),
        }
        if use.group is not None:
            out["group"] = use.group
        if use.part is not None:
            out["part"] = use.part[1] + 1
            out["parts"] = use.part[2]
        if use.role == "name":
            out["type"] = use.meaning.name_type
            out["id"] = use.meaning.name_id
        if use.role == "number":
            out["value"] = to_python(use.meaning.value)
            if use.meaning.unit:
                out["unit"] = use.meaning.unit
        return out

    def result_json(result: IntentResult) -> dict[str, Any]:
        out: dict[str, Any] = {"intent": result.intent, "matched": result.matched}
        if result.cost != float("inf"):
            out.update(
                cost=round(result.cost, 3),
                acoustic=round(result.acoustic, 3),
                prior=round(result.prior, 3),
                unexplained_cost=round(result.unexplained_cost, 3),
                slots=result.slots,
                spans=[use_json(u) for u in result.spans],
                unexplained=[
                    {
                        "text": w.text,
                        **times(w.start, w.end),
                        "important": w.important,
                        "heard_as": w.heard_as,
                        **({"name_after": w.name_cue} if w.name_cue else {}),
                    }
                    for w in result.unexplained
                ],
            )
        if result.reasons:
            out["reasons"] = result.reasons
        if result.missing:
            out["missing"] = result.missing
        if not result.matched:
            out["heard_groups"] = result.heard_groups
        if result.notes:
            out["notes"] = result.notes
        if result.ambiguous_with:
            out["ambiguous_with"] = result.ambiguous_with
        if result.example is not None:
            out["example"] = result.example
            out["example_source"] = result.example_source
        if result.similarity is not None:
            out["similarity"] = round(result.similarity, 4)
        return out

    data: dict[str, Any] = {
        "transcript": recognition.transcript,
        "duration": round(recognition.duration, 3),
        "frame_seconds": fs,
        "best": recognition.best.intent if recognition.best else None,
        "lead_in": recognition.lead_in,
        "tdt_transcript": recognition.tdt_transcript,
        "matches": [result_json(r) for r in recognition.matches],
        "rejected": [result_json(r) for r in recognition.rejected],
        "timings": {k: round(v, 4) for k, v in recognition.timings.items()},
        "arc_updates": recognition.spotting.visits,
    }
    if include_spans:
        data["spans"] = [
            {
                "phrase": hit.phrase,
                "meanings": [m.label() for m in hit.meanings],
                **times(hit.start, hit.end),
                "cost": round(hit.penalty, 3),
                "cost_per_token": round(hit.per_token, 3),
                "outscored_by": hit.dominated_by.phrase if hit.dominated_by else None,
                "part_of": hit.inside.phrase if hit.inside else None,
            }
            for hit in recognition.spotting.hits
        ]
    return data


def render_wake(wake) -> str:
    """One line on a wake word verdict."""
    fs = wake.frame_seconds
    if wake.phrase is None:
        return f"wake: REJECTED ({wake.reason})"
    rival = (
        f", margin {wake.margin:+.2f} over '{wake.confusable}'" if wake.confusable else ""
    )
    head = "accepted" if wake.accepted else f"REJECTED ({wake.reason})"
    return (
        f"wake: {head}  '{wake.phrase}' at {_span(wake.start, wake.end, fs)}, "
        f"{wake.cost_per_token:.2f}/token{rival}"
    )


def wake_json(wake) -> dict[str, Any]:
    fs = wake.frame_seconds
    return {
        "accepted": wake.accepted,
        "reason": wake.reason or None,
        "phrase": wake.phrase,
        "start": round(wake.start * fs, 3),
        "end": round((wake.end + 1) * fs, 3),
        "cost": None if wake.phrase is None else round(wake.cost, 3),
        "cost_per_token": None if wake.phrase is None else round(wake.cost_per_token, 3),
        "confusable": wake.confusable,
        "margin": None if wake.confusable is None else round(wake.margin, 3),
    }
