"""Deterministic prose engine — turn the structured breakdown / style-profile dicts
into editorial copy, with zero free-text models.

Three entry points produce ``list[(heading, [paragraphs])]`` sections, each section
*conditional* on the data being present, and phrased by thresholds (dominant vs
competitive) so the words always agree with the numbers they describe:

    match_narrative(build_match_breakdown(...))   -> the per-bout article body
    profile_narrative(build_style_profile(...))   -> the "Grapple like X" dossier body
    event_narrative(build_event_profile(...))     -> the card article body

Every one takes ``lang`` ("en" | "pt") and is called once per language by the exporter,
which emits both into the page behind ``data-lang-*``. Portuguese is the product's other
first-class locale (the app ships pt-BR), so the copy is written in Brazilian jiu-jitsu
vocabulary — *queda*, *raspagem*, *finalização*, *guarda* — not translated word for word.

``render_markdown(sections)`` flattens either to a Markdown string. Pure + import-free
(no DB, no I/O) so it unit-tests off plain fixtures.
"""

from __future__ import annotations

from typing import Any

Section = tuple[str, list[str]]

LANGS = ("en", "pt")


def _t(lang: str, en: str, pt: str) -> str:
    """Pick a variant. Every user-visible sentence in this module goes through here."""
    return pt if lang == "pt" else en


# Style-mix buckets and technique types are English slugs in the data; these are the
# nouns a Brazilian reader expects to see instead.
_BUCKET_PT: dict[str, str] = {
    "submission": "finalização", "takedown": "queda", "guard": "guarda",
    "pass": "passagem", "sweep": "raspagem", "control": "controle",
    "escape": "fuga", "transition": "transição", "scramble": "scramble",
}


# The recurring problems style_profile tracks, as a Brazilian reader would say them.
_SITUATION_PT: dict[str, str] = {
    "taken down": "é derrubado", "guard passed": "tem a guarda passada",
    "back taken": "tem as costas tomadas", "swept": "é raspado",
}


# Archetype names are generated in English by analysis.archetype.name_archetype and
# persisted, so translate the pieces back rather than storing a second column.
_ARCH_WORD_PT: dict[str, str] = {
    "guard": "guarda", "passing": "passagem", "sweep": "raspagem",
    "submission": "finalização", "takedown": "queda", "control": "controle",
    "escape": "fuga", "scramble": "scramble", "specialist": "especialista",
    "based": "de base", "balanced": "equilibrado",
}


def archetype_label(lang: str, name: str | None) -> str:
    """"Control / Guard Specialist" -> "especialista em controle / guarda"."""
    if not name:
        return ""
    if lang != "pt":
        return name
    # "Passing Specialist · Scramble-Based" keeps its qualifier, translated in place
    def _noun(word: str) -> str:
        return _ARCH_WORD_PT.get(word.strip().lower(), word.strip().lower())

    head, sep, tail = name.partition("·")
    head = head.strip()
    if "Specialist" in head:
        parts = [w for w in head.replace("Specialist", "").split("/") if w.strip()]
        out = "especialista em " + " / ".join(_noun(w) for w in parts)
    elif head.endswith("-Based"):
        out = f"base {_noun(head[:-len('-Based')])}"
    else:
        out = _noun(head)
    if sep:
        q = tail.strip()
        q = q[:-len("-Based")] if q.endswith("-Based") else q
        out += f" · base {_noun(q)}"
    return out


def _bucket(lang: str, key: str) -> str:
    return _BUCKET_PT.get(key, key) if lang == "pt" else key


def _method(lang: str, method: str) -> str:
    """Result method as prose ("Submission (Guillotine)" -> "finalização (guilhotina)")."""
    m = str(method or "").strip()
    if lang != "pt":
        return m.lower()
    low = m.lower()
    if low.startswith("submission"):
        inner = m[m.find("(") + 1:m.rfind(")")].strip().lower() if "(" in m else ""
        return f"finalização ({inner})" if inner else "finalização"
    return {"decision": "decisão", "points": "pontos", "draw": "empate",
            "referee decision": "decisão do árbitro", "overtime": "prorrogação"}.get(low, low)


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
def _decision_space_section(bd: dict[str, Any], lang: str = "en") -> Section | None:
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
    para = _t(
        lang,
        f"The bout turned on decision space, not activity. {_name(actor)}'s {top['label']} was "
        f"the decisive constraint — it collapsed {_name(foe)}'s viable options from "
        f"{before:.2f} to {after:.2f} (a {pct}% reduction) while widening {_name(actor)}'s own "
        f"attack. Whoever removes the other's choices fastest dictates the exchange.",
        f"A luta se decidiu no espaço de decisão, não no volume. O {top['label']} de "
        f"{_name(actor)} foi a restrição decisiva — reduziu as opções viáveis de "
        f"{_name(foe)} de {before:.2f} para {after:.2f} (queda de {pct}%) enquanto abria o "
        f"próprio ataque. Quem tira as escolhas do outro mais rápido dita a troca.",
    )
    if len(reds) > 1:
        para += _t(
            lang,
            f" {_name(actor)} forced {len(reds)} such option-collapses across the match.",
            f" {_name(actor)} forçou {len(reds)} colapsos de opção como esse ao longo da luta.",
        )
    return (_t(lang, "Decision space", "Espaço de decisão"), [para])


def match_narrative(bd: dict[str, Any], lang: str = "en") -> list[Section]:
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
        method = _method(lang, meta["method"])
        lede = _t(lang,
                  f"{win_name} defeated {_name(lose)} by {method}",
                  f"{win_name} venceu {_name(lose)} por {method}")
    else:
        lede = _t(lang,
                  f"{_name(a)} and {_name(b)} fought to no decision",
                  f"{_name(a)} e {_name(b)} não tiveram decisão")
    if meta.get("event"):
        lede += _t(lang, f" at {meta['event']}", f" no {meta['event']}")
    if meta.get("year"):
        lede += f" ({meta['year']})"
    lede += "."
    disparities = [
        ("takedowns", "quedas", sa["takedowns_landed"], sb["takedowns_landed"]),
        ("control positions", "posições de controle", sa["controls"], sb["controls"]),
        ("logged transitions", "transições registradas", sa["transitions"], sb["transitions"]),
    ]
    en_lb, pt_lb, va, vb = max(disparities, key=lambda d: abs(d[2] - d[3]))
    label = _t(lang, en_lb, pt_lb)
    if va != vb:
        more_side = "a" if va > vb else "b"
        more, mv, lv = (_name(a), va, vb) if va > vb else (_name(b), vb, va)
        # Don't contradict the result: if the busier side didn't win, frame it as activity that
        # didn't convert to control (the decision-space section explains who actually dictated).
        if winner and winner["side"] != more_side:
            lede += _t(
                lang,
                f" {more} logged more {label} ({mv}–{lv}), but volume didn't translate into "
                f"control.",
                f" {more} registrou mais {label} ({mv}–{lv}), mas volume não virou controle.")
        else:
            lede += _t(lang,
                       f" {more} led {mv}–{lv} in {label}.",
                       f" {more} liderou {mv}–{lv} em {label}.")
    sections.append((_t(lang, "Overview", "Visão geral"), [lede]))

    # Takedown battle.
    if sa["takedowns_attempted"] or sb["takedowns_attempted"]:
        sections.append((_t(lang, "The takedown battle", "A disputa de queda"), [_t(
            lang,
            f"{_name(a)} hit {sa['takedowns_landed']} of {sa['takedowns_attempted']} "
            f"takedown attempts; {_name(b)} {sb['takedowns_landed']} of "
            f"{sb['takedowns_attempted']}.",
            f"{_name(a)} acertou {sa['takedowns_landed']} de {sa['takedowns_attempted']} "
            f"tentativas de queda; {_name(b)}, {sb['takedowns_landed']} de "
            f"{sb['takedowns_attempted']}.")]))

    # Positional conversion — entries that reached a dominant position.
    if sa["positional_entries"] or sb["positional_entries"]:
        ca, cb = sa["positional_conversion"], sb["positional_conversion"]
        who, hi, lo = (_name(a), ca, cb) if ca >= cb else (_name(b), cb, ca)
        sections.append((_t(lang, "Positional conversion", "Conversão posicional"), [_t(
            lang,
            f"{who} converted the cleaner — {_pct(hi)} of entries to {_pct(lo)} reached a "
            f"dominant spot.",
            f"{who} converteu melhor — {_pct(hi)} das entradas contra {_pct(lo)} chegaram a "
            f"uma posição dominante.")]))

    # Submission threats.
    if sa["submission_attempts"] or sb["submission_attempts"]:
        sections.append((_t(lang, "Submission threats", "Ameaças de finalização"), [_t(
            lang,
            f"{_name(a)} threatened {sa['submission_attempts']} submission(s) "
            f"({sa['submissions_finished']} finished); {_name(b)} "
            f"{sb['submission_attempts']} ({sb['submissions_finished']} finished).",
            f"{_name(a)} ameaçou {sa['submission_attempts']} finalização(ões) "
            f"({sa['submissions_finished']} concluída(s)); {_name(b)}, "
            f"{sb['submission_attempts']} ({sb['submissions_finished']} concluída(s)).")]))

    # Momentum.
    mom = stats["momentum"]
    lead_side, lead = ("a", mom["a"]) if mom["a"] >= mom["b"] else ("b", mom["b"])
    lead_name = _name(a if lead_side == "a" else b)
    tone = _t(lang, "controlled the flow", "controlou o ritmo") if lead >= 0.65 else \
        _t(lang, "edged the flow", "levou o ritmo por pouco")
    sections.append((_t(lang, "Momentum", "Momento"), [_t(
        lang,
        f"By scoring share, {lead_name} {tone} with {_pct(lead)} of the action.",
        f"Por participação nas ações, {lead_name} {tone}, com {_pct(lead)} do total.")]))

    # Decisive sequence — the winner's (or busier side's) finishing chain.
    chain_side = winner["side"] if winner else lead_side
    chain = _chain(bd["sequence"], chain_side)
    if len(chain) >= 2:
        who = _name(a if chain_side == "a" else b)
        joined = " → ".join(chain)
        sections.append((_t(lang, "The decisive sequence", "A sequência decisiva"), [_t(
            lang,
            f"{who}'s closing chain ran {joined}.",
            f"A cadeia final de {who} foi {joined}.")]))

    # Decision space — the strategic "why" (option-collapse), the product's core lens.
    ds_section = _decision_space_section(bd, lang)
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
        joined = "; ".join(bits)
        sections.append(("Grappling ELO", [_t(
            lang,
            f"This bout moved each fighter's Grappling ELO: {joined}.",
            f"A luta mexeu no Grappling ELO de cada um: {joined}.")]))

    return sections


# ── "Grapple like X" dossier ─────────────────────────────────────────────────
def profile_narrative(p: dict[str, Any], lang: str = "en") -> list[Section]:
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
    bucket_str = ", ".join(f"{_bucket(lang, k)} {_pct(v)}" for k, v in top_buckets if v > 0)
    if arche:
        opener = _t(lang,
                    f"{name} grapples as a {arche.lower()}.",
                    f"{name} joga como {archetype_label('pt', arche)}.")
    else:
        opener = _t(lang, f"{name}'s game, mapped.", f"O jogo de {name}, mapeado.")
    if bucket_str:
        opener += _t(lang,
                     f" His mat time skews {bucket_str}.",
                     f" O tempo de tatame dele pende para {bucket_str}.")
    rank, pctile = f.get("elo_rank"), f.get("elo_percentile")
    if rank:
        opener += _t(lang,
                     f" He sits #{rank} by Grappling ELO in his division",
                     f" Ele é o #{rank} em Grappling ELO na divisão dele")
        opener += (_t(lang, f" (top {pctile}% overall).", f" (top {pctile}% no geral).")
                   if pctile else ".")
    sections.append((_t(lang, "The system", "O sistema"), [opener]))

    # Signature game.
    sig = p.get("signature_techniques", [])[:3]
    trans = p.get("signature_transitions", [])[:2]
    if sig:
        entries = ", ".join(f"{s['label']} ({_pct(s['pct'])})" for s in sig)
        line = _t(lang,
                  f"His most-traveled entries: {entries}.",
                  f"As entradas mais percorridas dele: {entries}.")
        if trans:
            spine = "; ".join(f"{t['from']} → {t['to']}" for t in trans)
            line += _t(lang,
                       f" The spine of the game runs {spine}.",
                       f" A espinha do jogo passa por {spine}.")
        sections.append((_t(lang, "Signature game", "Jogo de assinatura"), [line]))

    # Response patterns.
    resp = p.get("responses", {})
    if resp:
        lines = []
        for sit, data in resp.items():
            if not data["moves"]:
                continue
            top = data["moves"][0]
            lines.append(_t(
                lang,
                f"When {sit}, {name} most often answers with {top['move']} "
                f"({_pct(top['pct'])} of the time).",
                f"Quando {_SITUATION_PT.get(sit, sit)}, {name} responde mais com {top['move']} "
                f"({_pct(top['pct'])} das vezes)."))
        if lines:
            sections.append((_t(lang, "How he responds", "Como ele responde"), lines))

    # Finishing.
    fin = p.get("finishing", {})
    fam = fin.get("submission_family", {})
    fin_bits = []
    if fin.get("finish_rate"):
        fin_bits.append(_t(lang,
                           f"He finishes {_pct(fin['finish_rate'])} of his wins",
                           f"Ele finaliza {_pct(fin['finish_rate'])} das vitórias"))
    if fam.get("dominant"):
        fin_bits.append(_t(lang,
                           f"mostly via {fam['dominant'].lower()}",
                           f"na maioria por {fam['dominant'].lower()}"))
    elite = fin.get("record_vs_elite", {})
    if elite.get("wins") or elite.get("losses"):
        fin_bits.append(_t(
            lang,
            f"and is {elite['wins']}–{elite['losses']} against top-tier opposition",
            f"e tem {elite['wins']}–{elite['losses']} contra adversários de elite"))
    if fin_bits:
        sections.append((_t(lang, "Where it ends", "Onde termina"),
                         [", ".join(fin_bits) + "."]))

    # Systems — community decomposition of the career graph (stashed as _systems
    # by build_fighters; appended last so sections[0]/[1] keep their meaning).
    sysd = p.get("_systems") or {}
    systems = sysd.get("systems") or []
    if systems:
        top = systems[0]
        n = sysd.get("system_count", len(systems))
        line = _t(
            lang,
            f"Run community detection over the career graph and it separates into "
            f"{n} self-contained system{'s' if n != 1 else ''}. The biggest orbits "
            f"{top['hub']}: {top['size']} techniques wired together by "
            f"{top['transition_count']} internal transitions.",
            f"Rodando detecção de comunidades no grafo da carreira, ele se separa em "
            f"{n} sistema{'s' if n != 1 else ''} autocontido{'s' if n != 1 else ''}. O maior "
            f"orbita {top['hub']}: {top['size']} técnicas ligadas por "
            f"{top['transition_count']} transições internas.")
        dom = sysd.get("dominant_type")
        if dom:
            line += _t(lang,
                       f" The whole game leans {_bucket(lang, dom)}.",
                       f" O jogo inteiro pende para {_bucket(lang, dom)}.")
        sections.append((_t(lang, "The systems", "Os sistemas"), [line]))

    # Dilemmas — the forks where every branch hurts (path-to-victory model; raw PtV
    # numbers never surface, only the structure).
    forks = p.get("_dilemmas") or []
    if forks:
        top = forks[0]
        branches = [b[0] for b in top.get("branches", [])][:2]
        if len(branches) == 2:
            line = _t(
                lang,
                f"The sharpest fork in the game sits at {top['node']}: commit to stopping "
                f"{branches[0]} and {branches[1]} opens up — both branches carry real "
                f"finishing value, which is what makes it a true dilemma.",
                f"A bifurcação mais dura do jogo está em {top['node']}: comprometa-se a parar "
                f"{branches[0]} e {branches[1]} se abre — os dois caminhos têm valor real de "
                f"finalização, e é isso que faz dele um dilema de verdade.")
            if len(forks) > 1:
                others = ", ".join(fk["node"] for fk in forks[1:3])
                line += _t(lang,
                           f" Secondary forks: {others}.",
                           f" Bifurcações secundárias: {others}.")
            sections.append((_t(lang, "The dilemmas", "Os dilemas"), [line]))

    return sections


# ── event (card) article ─────────────────────────────────────────────────────
def event_narrative(ep: dict[str, Any], lang: str = "en") -> list[Section]:
    """Prose for a whole card from ``build_event_profile`` — the event read as one story."""
    name = ep["event"]
    sections: list[Section] = []

    # Lede — the card at a glance.
    lede = _t(lang,
              f"{name} ran {ep['bout_count']} bouts",
              f"{name} teve {ep['bout_count']} lutas")
    if ep.get("year"):
        lede += _t(lang, f" in {ep['year']}", f" em {ep['year']}")
    lede += "."
    if ep["decided"]:
        lede += _t(lang,
                   f" {_pct(ep['finish_rate'])} of the decided bouts ended in a finish.",
                   f" {_pct(ep['finish_rate'])} das lutas decididas terminaram em finalização.")
    sections.append((_t(lang, "The card", "O card"), [lede]))

    # Headline bout.
    hb = ep.get("headline_bout")
    if hb:
        method = _method(lang, hb["method"])
        sections.append((_t(lang, "Headline bout", "Luta principal"), [_t(
            lang,
            f"The marquee matchup pitted {hb['a']} against {hb['b']}, "
            f"taken by {hb['winner']} ({method}).",
            f"O confronto principal colocou {hb['a']} contra {hb['b']}, "
            f"levado por {hb['winner']} ({method}).")]))

    # How they finished.
    subs = ep.get("submissions") or []
    if ep["decided"]:
        line = _t(lang,
                  f"{ep['finishes']} of {ep['decided']} decided bouts were finishes",
                  f"{ep['finishes']} de {ep['decided']} lutas decididas foram finalizações")
        if subs:
            line += _t(
                lang,
                f"; the most-seen finish was the {subs[0][0].lower()} ({subs[0][1]}×)",
                f"; a finalização mais vista foi {subs[0][0].lower()} ({subs[0][1]}×)")
        line += "."
        sections.append((_t(lang, "How they finished", "Como finalizaram"), [line]))

    # Stylistic trend across the whole card.
    mix = ep.get("style_mix") or {}
    if mix:
        top = sorted(mix.items(), key=lambda kv: kv[1], reverse=True)[:3]
        bstr = ", ".join(f"{_bucket(lang, k)} {_pct(v)}" for k, v in top if v > 0)
        line = _t(lang,
                  f"Across the card the action skewed {bstr}.",
                  f"No card inteiro a ação pendeu para {bstr}.")
        tech = ep.get("top_techniques") or []
        if tech:
            listed = ", ".join(f"{t[0]} ({t[1]}×)" for t in tech[:4])
            line += _t(lang,
                       f" Most-logged techniques: {listed}.",
                       f" Técnicas mais registradas: {listed}.")
        sections.append((_t(lang, "How the card was won", "Como o card foi decidido"), [line]))

    # Headliners.
    hl = ep.get("headliners") or []
    if hl:
        joined = ", ".join(hl)
        sections.append((_t(lang, "Who showed up", "Quem estava lá"), [_t(
            lang,
            f"Top names on the card by Grappling ELO: {joined}.",
            f"Principais nomes do card por Grappling ELO: {joined}.")]))

    return sections


def render_markdown(sections: list[Section]) -> str:
    out: list[str] = []
    for heading, paras in sections:
        out.append(f"## {heading}")
        out.extend(paras)
    return "\n\n".join(out)
