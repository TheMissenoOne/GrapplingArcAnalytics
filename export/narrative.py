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

from analysis.gendered_text import Gender, pick

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


# Short bilingual glosses for the Lamas action codes (analysis/lamas_chain.STATES), trimmed
# from analysis.lamas_chain.STATE_DEFS for a dossier sentence rather than a glossary entry.
_LAMAS_GLOSS: dict[str, tuple[str, str]] = {
    "CDP": ("a standing grip dispute", "uma disputa de pegada em pé"),
    "PGD": ("a guard pull", "uma puxada de guarda"),
    "SWPA": ("a sweep attempt", "uma tentativa de raspagem"),
    "SWP": ("a sweep", "uma raspagem"),
    "TKDA": ("a takedown attempt", "uma tentativa de queda"),
    "TKD": ("a takedown", "uma queda"),
    "GPSA": ("a guard-pass attempt", "uma tentativa de passagem de guarda"),
    "GPS": ("a guard pass", "uma passagem de guarda"),
    "BTKA": ("a back-take attempt", "uma tentativa de pegada de costas"),
    "BTK": ("a back take", "uma pegada de costas"),
    "SUBA": ("a submission attempt", "uma tentativa de finalização"),
    "SUB": ("a submission", "uma finalização"),
}


def _progression_section(p: dict[str, Any], lang: str = "en") -> Section | None:
    """RRB-progression prose (``analysis/rrb_progression.py``).

    DESCRIPTIVE ONLY, never a prediction and never a per-athlete significance claim: only
    17 of 441 athletes clear the corpus's gates, and of those, bootstrap CIs excluding zero
    sit at chance level (2/17) — see ``docs/research/rrb_progression.md`` §2.3. So this never
    says "improving", "getting better at" or attaches a confidence word to the direction; it
    describes where the sequences this corpus has moved him, nothing more.

    ``p["_progression"]`` is set by ``export/site_data.py`` from
    ``analysis.rrb_progression.athlete_progression``'s row for this athlete, present ONLY when
    that row is gated — an ungated athlete gets no section at all, not a filler sentence
    (root CLAUDE.md: honest absence over manufactured content).

    When the row's value table fell back to the reward-risk tier (``_mixed_source``), the
    prose drops the submission-anchored gloss and states the mechanism plainly instead, per
    the fallback chain's own rule that magnitudes/mechanism differ across tiers even though
    signs stay comparable.
    """
    prog = p.get("_progression")
    if not prog:
        return None
    name = p["fighter"]["name"]
    gender: Gender = p["fighter"].get("gender")
    per_action = prog.get("per_action")
    off_p = (prog.get("off_share") or {}).get("p")
    def_p = (prog.get("def_share") or {}).get("p")
    if per_action is None or (off_p is None and def_p is None):
        return None

    basis_en, basis_pt = (
        ("a blended read of how much initiative each action carries in this corpus",
         "uma leitura mista de quanta iniciativa cada ação carrega neste acervo")
        if prog.get("_mixed_source") else
        (pick(gender,
              m="a read of how close each action leaves him to eventually finishing",
              f="a read of how close each action leaves her to eventually finishing",
              neutral="a read of how close each action leaves them to eventually finishing"),
         pick(gender,
              m="uma leitura de quão perto cada ação o deixa de uma eventual finalização",
              f="uma leitura de quão perto cada ação a deixa de uma eventual finalização",
              neutral="uma leitura de quão perto cada ação está de uma eventual finalização"))
    )
    if per_action > 0.005:
        dir_en, dir_pt = "climbed, on balance", "subiu, no saldo"
    elif per_action < -0.005:
        dir_en, dir_pt = "slipped, on balance", "recuou, no saldo"
    else:
        dir_en, dir_pt = "held roughly flat", "ficou praticamente estável"
    possessive_en = pick(gender, m="his", f="her", neutral="their")
    possessive_pt = pick(gender, m="dele", f="dela", neutral=f"de {name}")
    sentences = [_t(
        lang,
        f"Across the sequences this corpus has for {name} — {basis_en} — {possessive_en} "
        f"standing {dir_en} over the course of {possessive_en} logged fights.",
        f"Nas sequências que este acervo tem de {name} — {basis_pt} — a posição "
        f"{possessive_pt} {dir_pt} ao longo das lutas registradas.")]

    dominant = "off" if (off_p or 0) >= (def_p or 0) else "def"
    mean_len = prog.get(f"mean_{dominant}_cycle_len")
    ground_en = "offensive" if dominant == "off" else "defensive"
    ground_pt = "ofensivo" if dominant == "off" else "defensivo"
    len_r = round(mean_len) if mean_len else None
    holds_en = pick(gender, m="He holds", f="She holds", neutral="They hold")
    holds_pt = pick(gender, m="Ele fica", f="Ela fica", neutral=f"{name} fica")
    sentences.append(_t(
        lang,
        f"{holds_en} {ground_en} ground more often than not"
        + (f", cycling back to it roughly every {len_r} action{'s' if len_r != 1 else ''}."
           if len_r else "."),
        f"{holds_pt} mais tempo em terreno {ground_pt}"
        + (f", voltando a ele a cada {len_r} {'ação' if len_r == 1 else 'ações'} em média."
           if len_r else ".")))

    example = prog.get("_example")
    if example:
        from_en, from_pt = _LAMAS_GLOSS.get(
            example["from_state"], (example["from_state"], example["from_state"]))
        to_en, to_pt = _LAMAS_GLOSS.get(
            example["to_state"], (example["to_state"], example["to_state"]))
        sentences.append(_t(
            lang,
            f"The sharpest single move in that reading runs from {from_en} into {to_en}.",
            f"A virada mais nítida dessa leitura vai de {from_pt} para {to_pt}."))

    return (_t(lang, "Progression", "Progressão"), [" ".join(sentences)])


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

    # Grappling Rating (Glicko-2) context — relative % move, never the raw rating.
    da, db = a.get("elo_delta_pct"), b.get("elo_delta_pct")
    if da is not None or db is not None:
        bits = []
        if da is not None:
            bits.append(f"{_name(a)} {da:+.1f}%")
        if db is not None:
            bits.append(f"{_name(b)} {db:+.1f}%")
        joined = "; ".join(bits)
        sections.append(("Grappling Rating (Glicko-2)", [_t(
            lang,
            f"This bout moved each fighter's Grappling Rating (Glicko-2): {joined}.",
            f"A luta mexeu no Grappling Rating (Glicko-2) de cada um: {joined}.")]))

    return sections


# ── "Grapple like X" dossier ─────────────────────────────────────────────────
def profile_narrative(p: dict[str, Any], lang: str = "en") -> list[Section]:
    f = p["fighter"]
    name = f["name"]
    gender: Gender = f.get("gender")
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
        mat_time_en = pick(gender, m="His mat time", f="Her mat time", neutral="Mat time")
        mat_time_pt = pick(gender, m="O tempo de tatame dele",
                            f="O tempo de tatame dela", neutral="O tempo de tatame")
        opener += _t(lang,
                     f" {mat_time_en} skews {bucket_str}.",
                     f" {mat_time_pt} pende para {bucket_str}.")
    rank, pctile = f.get("elo_rank"), f.get("elo_percentile")
    if rank:
        sits_en = pick(gender, m="He sits", f="She sits", neutral="They sit")
        division_en = pick(gender, m="his division", f="her division", neutral="the division")
        subject_pt = pick(gender, m="Ele", f="Ela", neutral=name)
        opener += _t(lang,
                     f" {sits_en} #{rank} by Grappling Rating (Glicko-2) in {division_en}",
                     f" {subject_pt} é o #{rank} em Grappling Rating (Glicko-2) na divisão")
        opener += (_t(lang, f" (top {pctile}% overall).", f" (top {pctile}% no geral).")
                   if pctile else ".")
    sections.append((_t(lang, "The system", "O sistema"), [opener]))

    # Signature game.
    sig = p.get("signature_techniques", [])[:3]
    trans = p.get("signature_transitions", [])[:2]
    if sig:
        # ponytail: names only, no per-entry percentage — a %-of-8-events "conversion" claim
        # from a thin sample reads as more precise than the data supports (owner distrust).
        entries = ", ".join(s["label"] for s in sig)
        entries_head_en = pick(gender, m="His most-traveled entries",
                                f="Her most-traveled entries", neutral="Most-traveled entries")
        entries_head_pt = pick(gender, m="As entradas mais percorridas dele",
                                f="As entradas mais percorridas dela",
                                neutral="As entradas mais percorridas")
        line = _t(lang,
                  f"{entries_head_en}: {entries}.",
                  f"{entries_head_pt}: {entries}.")
        if trans:
            spine = "; ".join(f"{t['from']} → {t['to']}" for t in trans)
            line += _t(lang,
                       f" The spine of the game runs {spine}.",
                       f" A espinha do jogo passa por {spine}.")
        sections.append((_t(lang, "Signature game", "Jogo de assinatura"), [line]))

    # Finishing.
    fin = p.get("finishing", {})
    fam = fin.get("submission_family", {})
    fin_bits = []
    if fin.get("finish_rate"):
        finishes_en = pick(gender, m="He finishes", f="She finishes", neutral="Finishes")
        wins_en = pick(gender, m="his wins", f="her wins", neutral="the wins")
        subject_pt = pick(gender, m="Ele", f="Ela", neutral=name)
        fin_bits.append(_t(lang,
                           f"{finishes_en} {_pct(fin['finish_rate'])} of {wins_en}",
                           f"{subject_pt} finaliza {_pct(fin['finish_rate'])} das vitórias"))
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

    # Progression — RRB-derived positional movement (rrb_progression.py), present only for
    # the athletes whose row cleared the corpus's gates; see _progression_section's docstring.
    prog_section = _progression_section(p, lang)
    if prog_section is not None:
        sections.append(prog_section)

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
            f"Top names on the card by Grappling Rating (Glicko-2): {joined}.",
            f"Principais nomes do card por Grappling Rating (Glicko-2): {joined}.")]))

    return sections


def render_markdown(sections: list[Section]) -> str:
    out: list[str] = []
    for heading, paras in sections:
        out.append(f"## {heading}")
        out.extend(paras)
    return "\n\n".join(out)
