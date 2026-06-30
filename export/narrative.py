"""Deterministic prose engine — turn the structured breakdown / style-profile dicts
into editorial copy, with zero free-text models.

Two entry points produce ``list[(heading, [paragraphs])]`` sections, each section
*conditional* on the data being present, and phrased by thresholds (dominant vs
competitive) so the words always agree with the numbers they describe:

    match_narrative(build_match_breakdown(...))   -> the per-bout article body
    profile_narrative(build_style_profile(...))   -> the "Grapple like X" dossier body

``render_markdown(sections)`` flattens either to a Markdown string. Pure + import-free
(no DB, no I/O) so it unit-tests off plain fixtures.
"""

from __future__ import annotations

from typing import Any

Section = tuple[str, list[str]]


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _name(side_block: dict[str, Any]) -> str:
    return str(side_block.get("name", "?"))


def _chain(sequence: list[dict[str, Any]], side: str, limit: int = 5) -> list[str]:
    """Distinct consecutive labels a side ran, most recent ``limit`` (the finishing path)."""
    labels: list[str] = []
    for e in sequence:
        if e.get("side") != side:
            continue
        lb = str(e.get("label", "")).strip()
        if lb and (not labels or labels[-1] != lb):
            labels.append(lb)
    return labels[-limit:]


# ── match article ────────────────────────────────────────────────────────────
def _decision_space_section(bd: dict[str, Any]) -> Section | None:
    """Narrate the bout as decision-space reduction (the Danaher systems lens): control isn't
    about activity, it's about removing the opponent's viable options. Uses the per-bout
    ``decision_space.reductions`` (DS-05/07) already computed in build_match_breakdown."""
    ds = bd.get("decision_space") or {}
    reds = [r for r in (ds.get("reductions") or []) if r.get("reduction_pct", 0) > 0]
    if not reds:
        return None
    a, b = bd["fighters"]["a"], bd["fighters"]["b"]
    top = max(reds, key=lambda r: r.get("total_reduction", 0.0))
    actor, foe = (a, b) if top["actor"] == "a" else (b, a)
    foe_key = "b" if top["actor"] == "a" else "a"
    before = top["ds_before"][foe_key]
    after = top["ds_after"][foe_key]
    pct = round(top.get("reduction_pct", 0.0) * 100)
    para = (
        f"The bout turned on decision space, not activity. {_name(actor)}'s {top['label']} was "
        f"the decisive constraint — it collapsed {_name(foe)}'s viable options from "
        f"{before:.2f} to {after:.2f} (a {pct}% reduction) while widening {_name(actor)}'s own "
        f"attack. Whoever removes the other's choices fastest dictates the exchange."
    )
    if len(reds) > 1:
        para += f" {_name(actor)} forced {len(reds)} such option-collapses across the match."
    return ("Decision space", [para])


def match_narrative(bd: dict[str, Any]) -> list[Section]:
    meta = bd["meta"]
    stats = bd["stats"]
    a, b = bd["fighters"]["a"], bd["fighters"]["b"]
    sa, sb = stats["a"], stats["b"]
    sections: list[Section] = []

    # Lede — outcome + the loudest single-stat disparity.
    winner = meta.get("winner")
    if winner:
        win_name = winner["name"]
        lose = b if winner["side"] == "a" else a
        lede = f"{win_name} defeated {_name(lose)} by {meta['method'].lower()}"
    else:
        lede = f"{_name(a)} and {_name(b)} fought to no decision"
    if meta.get("event"):
        lede += f" at {meta['event']}"
    if meta.get("year"):
        lede += f" ({meta['year']})"
    lede += "."
    disparities = [
        ("takedowns", sa["takedowns_landed"], sb["takedowns_landed"]),
        ("control positions", sa["controls"], sb["controls"]),
        ("logged transitions", sa["transitions"], sb["transitions"]),
    ]
    label, va, vb = max(disparities, key=lambda d: abs(d[1] - d[2]))
    if va != vb:
        more_side = "a" if va > vb else "b"
        more, mv, lv = (_name(a), va, vb) if va > vb else (_name(b), vb, va)
        # Don't contradict the result: if the busier side didn't win, frame it as activity that
        # didn't convert to control (the decision-space section explains who actually dictated).
        if winner and winner["side"] != more_side:
            lede += (f" {more} logged more {label} ({mv}–{lv}), but volume didn't translate into "
                     f"control.")
        else:
            lede += f" {more} led {mv}–{lv} in {label}."
    sections.append(("Overview", [lede]))

    # Takedown battle.
    if sa["takedowns_attempted"] or sb["takedowns_attempted"]:
        sections.append(("The takedown battle", [
            f"{_name(a)} hit {sa['takedowns_landed']} of {sa['takedowns_attempted']} "
            f"takedown attempts; {_name(b)} {sb['takedowns_landed']} of "
            f"{sb['takedowns_attempted']}."
        ]))

    # Positional conversion — entries that reached a dominant position.
    if sa["positional_entries"] or sb["positional_entries"]:
        ca, cb = sa["positional_conversion"], sb["positional_conversion"]
        edge = (f"{_name(a)} converted the cleaner — {_pct(ca)} of entries to "
                f"{_pct(cb)}" if ca >= cb else
                f"{_name(b)} converted the cleaner — {_pct(cb)} of entries to "
                f"{_pct(ca)}")
        sections.append(("Positional conversion", [
            f"Position, not aggression, decided the exchanges. {edge} reached a "
            f"dominant spot."
        ]))

    # Submission threats.
    if sa["submission_attempts"] or sb["submission_attempts"]:
        sections.append(("Submission threats", [
            f"{_name(a)} threatened {sa['submission_attempts']} submission(s) "
            f"({sa['submissions_finished']} finished); {_name(b)} "
            f"{sb['submission_attempts']} ({sb['submissions_finished']} finished)."
        ]))

    # Momentum.
    mom = stats["momentum"]
    lead_side, lead = ("a", mom["a"]) if mom["a"] >= mom["b"] else ("b", mom["b"])
    lead_name = _name(a if lead_side == "a" else b)
    tone = "controlled the flow" if lead >= 0.65 else "edged the flow"
    sections.append(("Momentum", [
        f"By scoring share, {lead_name} {tone} with {_pct(lead)} of the action."
    ]))

    # Decisive sequence — the winner's (or busier side's) finishing chain.
    chain_side = winner["side"] if winner else lead_side
    chain = _chain(bd["sequence"], chain_side)
    if len(chain) >= 2:
        who = _name(a if chain_side == "a" else b)
        sections.append(("The decisive sequence", [
            f"{who}'s closing chain ran " + " → ".join(chain) + "."
        ]))

    # Decision space — the strategic "why" (option-collapse), the product's core lens.
    ds_section = _decision_space_section(bd)
    if ds_section is not None:
        sections.append(ds_section)

    # Grappling-ELO context — relative % move, never the raw rating.
    da, db = a.get("elo_delta_pct"), b.get("elo_delta_pct")
    if da is not None or db is not None:
        bits = []
        if da is not None:
            bits.append(f"{_name(a)} {da:+.1f}%")
        if db is not None:
            bits.append(f"{_name(b)} {db:+.1f}%")
        sections.append(("Grappling ELO", [
            "This bout moved each fighter's Grappling ELO: " + "; ".join(bits) + "."
        ]))

    return sections


# ── "Grapple like X" dossier ─────────────────────────────────────────────────
def profile_narrative(p: dict[str, Any]) -> list[Section]:
    f = p["fighter"]
    name = f["name"]
    sections: list[Section] = []

    # Archetype + style mix.
    arche = p.get("archetype")
    mix = p.get("style_mix", {})
    top_buckets = sorted(
        ((k, v) for k, v in mix.items() if k != "offense_ratio"),
        key=lambda kv: kv[1], reverse=True,
    )[:3]
    bucket_str = ", ".join(f"{k} {_pct(v)}" for k, v in top_buckets if v > 0)
    opener = f"{name} grapples as a {arche.lower()}." if arche else f"{name}'s game, mapped."
    if bucket_str:
        opener += f" His mat time skews {bucket_str}."
    rank, pctile = f.get("elo_rank"), f.get("elo_percentile")
    if rank:
        opener += f" He sits #{rank} by Grappling ELO in his division"
        opener += f" (top {pctile}% overall)." if pctile else "."
    sections.append(("The system", [opener]))

    # Signature game.
    sig = p.get("signature_techniques", [])[:3]
    trans = p.get("signature_transitions", [])[:2]
    if sig:
        line = "His most-traveled entries: " + ", ".join(
            f"{s['label']} ({_pct(s['pct'])})" for s in sig
        ) + "."
        if trans:
            line += " The spine of the game runs " + "; ".join(
                f"{t['from']} → {t['to']}" for t in trans
            ) + "."
        sections.append(("Signature game", [line]))

    # Response patterns.
    resp = p.get("responses", {})
    if resp:
        lines = []
        for sit, data in resp.items():
            if not data["moves"]:
                continue
            top = data["moves"][0]
            lines.append(
                f"When {sit}, {name} most often answers with {top['move']} "
                f"({_pct(top['pct'])} of the time)."
            )
        if lines:
            sections.append(("How he responds", lines))

    # Finishing.
    fin = p.get("finishing", {})
    fam = fin.get("submission_family", {})
    fin_bits = []
    if fin.get("finish_rate"):
        fin_bits.append(f"He finishes {_pct(fin['finish_rate'])} of his wins")
    if fam.get("dominant"):
        fin_bits.append(f"mostly via {fam['dominant'].lower()}")
    elite = fin.get("record_vs_elite", {})
    if elite.get("wins") or elite.get("losses"):
        fin_bits.append(
            f"and is {elite['wins']}–{elite['losses']} against top-tier opposition"
        )
    if fin_bits:
        sections.append(("Where it ends", [", ".join(fin_bits) + "."]))

    return sections


# ── event (card) article ─────────────────────────────────────────────────────
def event_narrative(ep: dict[str, Any]) -> list[Section]:
    """Prose for a whole card from ``build_event_profile`` — the event read as one story."""
    name = ep["event"]
    sections: list[Section] = []

    # Lede — the card at a glance.
    lede = f"{name} ran {ep['bout_count']} bouts"
    if ep.get("year"):
        lede += f" in {ep['year']}"
    lede += "."
    if ep["decided"]:
        lede += f" {_pct(ep['finish_rate'])} of the decided bouts ended in a finish."
    sections.append(("The card", [lede]))

    # Headline bout.
    hb = ep.get("headline_bout")
    if hb:
        sections.append(("Headline bout", [
            f"The marquee matchup pitted {hb['a']} against {hb['b']}, "
            f"taken by {hb['winner']} ({hb['method'].lower()})."
        ]))

    # How they finished.
    subs = ep.get("submissions") or []
    if ep["decided"]:
        line = f"{ep['finishes']} of {ep['decided']} decided bouts were finishes"
        if subs:
            line += f"; the most-seen finish was the {subs[0][0].lower()} ({subs[0][1]}×)"
        line += "."
        sections.append(("How they finished", [line]))

    # Stylistic trend across the whole card.
    mix = ep.get("style_mix") or {}
    if mix:
        top = sorted(mix.items(), key=lambda kv: kv[1], reverse=True)[:3]
        bstr = ", ".join(f"{k} {_pct(v)}" for k, v in top if v > 0)
        line = f"Across the card the action skewed {bstr}."
        tech = ep.get("top_techniques") or []
        if tech:
            line += " Most-logged techniques: " + ", ".join(
                f"{t[0]} ({t[1]}×)" for t in tech[:4]
            ) + "."
        sections.append(("How the card was won", [line]))

    # Headliners.
    hl = ep.get("headliners") or []
    if hl:
        sections.append(("Who showed up", [
            "Top names on the card by Grappling ELO: " + ", ".join(hl) + "."
        ]))

    return sections


def render_markdown(sections: list[Section]) -> str:
    out: list[str] = []
    for heading, paras in sections:
        out.append(f"## {heading}")
        out.extend(paras)
    return "\n\n".join(out)
