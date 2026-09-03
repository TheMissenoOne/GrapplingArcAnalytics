"""Name normalization helpers for BJJ technique names — extracted from export/tech_library.py."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# Known name aliases for cross-referencing (norm → canonical)
# NOTE: "guillotine" intentionally omitted — would change existing ADCC behavior
# (raw "guillotine" stays "guillotine", not resolved to "guillotine choke")
NAME_ALIASES: dict[str, str] = {
    "rnc": "rear naked choke",
    "d'arce choke": "darce choke",
    "d'arce": "darce choke",
    "inside heel hook": "heel hook",
    "outside heel hook": "heel hook",
    "mata leao": "rear naked choke",
    "hadaka jime": "rear naked choke",
    "chave de braco": "armbar",
    "chave de calcanhar": "heel hook",
    "triangulo": "triangle choke",
}


def _resolve_aliases(name: str) -> str:
    """Resolve a name to its canonical form via alias map."""
    return NAME_ALIASES.get(name, name)


def _normalize_name(name: str) -> str:
    """Normalize technique name for cross-referencing."""
    n = name.lower().strip()
    n = re.sub(r"[^a-z0-9 ]", "", n)
    n = re.sub(r"\s+", " ", n)
    return n


# ponytail: operator-confirmed synonym allowlist (human review of
# analysis.grappling_map's synonym_candidates output). Applied AFTER
# _normalize_name, ONLY in Analytics-internal derivation (aggregate grappling
# map + published athlete-graph replay) — never touches the node_key contract
# the App/Supabase sync relies on for user data. Extend by re-reviewing
# synonym_candidates as the corpus grows.
SYNONYMS: dict[str, str] = {
    "ankle pick takedown": "ankle pick",
    "trip takedown": "trip",
    "turtle escape": "escape to turtle",
    "pass the guard": "guard pass",
    "stand up escape": "standup escape",
    "arm lock": "armbar",
    "reverse arm lock": "armbar",
    "snatch single leg takedown": "single leg takedown",
    "single leg x guard entry": "single leg x",
    "half guard recovery": "half guard",
    # foot lock (occ=143) vs straight foot lock (occ=62, 2026-07-15 corpus check) — lower folds
    # into higher.
    "straight foot lock": "foot lock",
    "leg entry 5050": "5050 guard",  # DB node_key is "5050 guard" (en "50/50 Guard"), not "5050"
    "armbar choi bar": "choi bar",  # choi bar is a distinct technique, not an armbar variant
    "half guard control": "top control half guard",
    # 2026-08-20, from a returned frame reading that used BOTH spellings in one batch. Neither
    # errored, because both were already real nodes -- which is exactly how one action arrives
    # split in two with nothing to catch it. Corpus counts settle which way each folds:
    "pull guard": "guard pull",   # 4 raw events vs 170
    "pass": "guard pass",         # 23 raw events vs 120
    # REVIEWED, NOT MERGED (Q7, 2026-08-24): "katagatame darce" (1 occ) vs "darce choke"
    # (11 occ). The slash label "Katagatame / Darce" is the narrator refusing to decide
    # between two DIFFERENT submissions (arm triangle vs d'arce); folding it into either
    # would assert what the transcript declined to. Stays its own node until footage
    # settles the single bout that carries it.

    # N1 alias pass (2026-09-04, docs/taxonomy/04_ONTOLOGIA_CANONICA.md §5 row "N1 —
    # aliases + biblioteca"), all measured live against prod
    # (`uv run python -m scripts.audit_ontology`) — direction is always the higher
    # corpus count, and matches the curated `technique_library.json` grafia where one
    # exists. First five are `alias_candidates`-family hits (edit distance <= 2 / plural
    # / spacing); the rest are domain merges the distance heuristic can't see.
    "close guard": "closed guard",          # 1 event vs 145
    "take down": "takedown",                # 3 vs 131
    "snap down": "snapdown",                # 40 vs 47, and library canonical is "Snapdown"
    "shin on shin guard": "shin to shin guard",  # 1 vs 12, library canonical
    "nearfall": "near fall",                # 2 vs 5
    # north-south: "North-South Position" itself normalizes to "northsouth position"
    # (hyphen drops, no space fills the gap) -- the corpus's dominant spelling (104
    # events) and already the pre-existing target. "North South" (3, bare) and "North
    # South Control" (13, its own now-redundant library entry -- kept for display/type,
    # not for node identity) are the same position under a different narrator's words.
    "north south": "northsouth position",
    "north south control": "northsouth position",
    # forward-compat: only "North-South Pass" (-> "northsouth pass") exists in the
    # corpus today; this catches the spaced spelling if a future transcript uses it.
    "north south pass": "northsouth pass",
    # library already lists "leg lock entanglement" as a known variant string of "Leg
    # Entanglement" (35 events) -- this was never carried into SYNONYMS.
    "leg lock entanglement": "leg entanglement",
    # REVIEWED, NOT MERGED (N1, 2026-09-04): "kimura grip" (4 events, all `control`) vs
    # "kimura trap" (3 events, mostly `submission`) -- the alias_candidates family flags
    # this pair (edit distance 2, same length) but they're different techniques: a grip
    # vs a named finishing setup. The type split confirms it. Left alone on purpose.
    #
    # REVIEWED, NOT MERGED (N1, 2026-09-04): "Leg Entry (50/50)" (1 event) already
    # resolves to "5050 guard" via the "leg entry 5050" entry above (a pre-existing,
    # documented decision) rather than to "leg entry" or "backside 50/50 entry" -- both
    # plausible per the docs/taxonomy plan. Ambiguous at n=1; left as-is rather than
    # re-litigated without new evidence.
}


def canonicalize(key: str) -> str:
    """Collapse a normalized node_key to its synonym-merged canonical form (identity if none)."""
    return SYNONYMS.get(key, key)


# ponytail: curated display labels for synonym-collapsed nodes — a canonical key folds two+
# raw event labels (e.g. "Ankle Pick" / "Ankle Pick Takedown"), so pick ONE deterministic label
# per key instead of whichever raw variant happened to be first-seen. Matches the live
# `technique_nodes.label` rows (2026-07-14 check) so this never fights the DB-persisted graphs.
# Extend alongside SYNONYMS when a new pair is added.
CANONICAL_LABELS: dict[str, str] = {
    "guard pull": "Guard Pull",
    "ankle pick": "Ankle Pick",
    "trip": "Trip",
    "escape to turtle": "Escape to Turtle",
    "guard pass": "Guard Pass",
    "standup escape": "Stand‑up Escape",  # non-breaking hyphen, matches technique_nodes row
    "armbar": "Armbar",
    "single leg takedown": "Single Leg Takedown",
    "single leg x": "Single Leg X",
    "half guard": "Half Guard",
    "foot lock": "Foot Lock",
    "5050 guard": "50/50 Guard",
    "choi bar": "Choi Bar",
    # curated rename, not the raw technique_nodes label ("Top Control (Half Guard)") —
    # human-confirmed 2026-07-15.
    "top control half guard": "Chest to Chest Half Guard",
}


def canonical_label(key: str, fallback: str) -> str:
    """Curated display label for a canonicalized node key, else the caller's own label."""
    return CANONICAL_LABELS.get(key, fallback)


def _deaccent(s: str) -> str:
    """Strip combining accents (ã→a) so 'Galvão' and 'Galvao' match. Display keeps accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_PAREN_ANNOT_RE = re.compile(r"\s*\([^()]*\)")   # "(Opening Round)" / "(UFC BJJ 3)" / "(Rematch)"
_TRAIL_DIGITS_RE = re.compile(r"\s+\d+$")        # "Magomed Ankalaev 2" disambiguator suffix


def _sanitize_name(n: str) -> str:
    """Strip transcript/refiner scaffolding that leaks into a bout key so it doesn't become a
    junk ``Athlete.name`` (dump-validation F10). Handles the shapes seen across 11 dumps:
      * colon clause      "Marlon Vera: The bantamweight title defense…" → "Marlon Vera"
      * leaked stage prefix (unbalanced '(')  "Match 1 (Jon Blank" / "Grande Final (França"
      * balanced annotation "(Opening Round)" / "(Encore/Replay)" / "(UFC BJJ 3)"        → dropped
      * unbalanced trailing ')'  "Dan Strauss)"                                            → dropped
      * trailing digits    "Johnny Walker 2"                                               → dropped
    No real grappling name contains a ':' , '(' , ')' or a bare trailing number, so this is safe."""
    n = n.split(":", 1)[0]                       # colon clause → keep the name before it
    if n.count("(") > n.count(")"):              # leaked prefix like "Match 1 (Name"
        n = n[n.rfind("(") + 1:]
    n = _PAREN_ANNOT_RE.sub("", n)               # balanced "(…)" annotations
    n = n.replace(")", "")                       # leftover unbalanced trailing ')'
    n = _TRAIL_DIGITS_RE.sub("", n)              # trailing disambiguator digits
    return n


def clean_athlete_name(raw: str) -> str:
    """Clean a scraped athlete display name (KEEP accents/case).

    Strips transcript junk that split one human into many rows: ``[H:MM:SS]`` timestamps,
    space-delimited ``'nicknames'`` (but not the apostrophe in ``Sean O'Malley``), and
    leaked bout-label / round / annotation scaffolding (``_sanitize_name``). Collapses
    whitespace. Returns the display form; use ``athlete_key`` for merge/identity comparison.
    """
    n = re.sub(r"\[[0-9:]+\]", "", raw)               # [2:11:18] transcript timestamp
    n = re.sub(r"(?<=\s)'[^']+'(?=\s|$)", "", n)      # spaced 'Hulk' / 'Cyborg' nickname
    n = _sanitize_name(n)                             # leaked stage-label / annotation junk (F10)
    return re.sub(r"\s+", " ", n).strip()


# Athlete identity aliases (nickname-only / initial / misspelling forms that don't share a
# cleaned key) → canonical key. Shared by the importer and the dedupe script so a re-import
# can't re-split a merged human. NOT for distinct people (e.g. Andrew vs William Tackett).
ATHLETE_ALIASES: dict[str, str] = {
    "cyborg": "roberto abreu",          # Roberto 'Cyborg' Abreu
    "m galvao": "mica galvao",          # M. Galvão → Mica/Micael Galvão
    "micael galvao": "mica galvao",     # Micael "Mica" Galvão (same human)
    "d reis": "diogo reis",             # D. Reis → Diogo Reis
    "ffion davis": "ffion davies",      # "Davis" misspelling → Ffion Davies
    "a tackett": "andrew tackett",      # A. Tackett → Andrew (NOT William Tackett)
    "g ryan": "gordon ryan",            # G. Ryan → Gordon Ryan
    "g sousa": "gabriel sousa",         # G. Sousa → Gabriel Sousa
    "cyborg abreu": "roberto abreu",    # Cyborg Abreu → Roberto 'Cyborg' Abreu
    "joseph chen": "jozef chen",        # "Joseph" misspelling → Jozef Chen
    "jonathan alves": "johnatha alves", # "Jonathan" misspelling → Johnatha Alves
    "adele fornino": "adele fornarino", # "Fornino" misspelling → Adele Fornarino
    "gabby mccomb": "gabi mccomb",      # Gabby → Gabi McComb (same human)
    "heam rida": "haisam rida",         # "Heam" misspelling → Haisam Rida
    "heisen rita": "haisam rida",       # "Heisen Rita" misspelling → Haisam Rida
    "miki galva": "mica galvao",        # "Miki Galva" misspelling → Mica Galvão
    "daniel manosu": "dan manasoiu",    # "Manosu" misspelling → Dan Manasoiu
    # Spelling/typo + initial-form variants of the SAME human (dossier-dedup, F4). Confirmed
    # from bout data; brothers (Tye vs Kade Ruotolo) + ambiguous (Mica vs Mike Galvão,
    # D. vs Tex Johnson) deliberately EXCLUDED.
    "roosevelt sousa": "roosevelt souza",   # Sousa/Souza spelling
    "anthony salsbury": "anthony salisbury",  # typo
    "sam schwarzapfel": "sam schwartzapfel",  # dropped 't'
    "jozeph chen": "jozef chen",        # "Jozeph"/"Joseph" → Jozef Chen
    "nicky rodriguez": "nick rodriguez",  # nickname → Nick Rodriguez
    "eoghan oflannagan": "eoghan oflanagan",  # doubled 'n'
    "devhonte johnson": "devonte johnson",   # typo
    "ana carolina viera": "ana carolina vieira",  # Viera/Vieira
    "nicholas renier": "nicolas renier",  # spelling
    "nicollas renier": "nicolas renier",  # spelling
    "hanette staack": "hannette staack",  # doubled 'n'
    "jake straus": "jake strauss",      # dropped 's'
    "jaden groner": "jayden groner",    # Jaden/Jayden
    "jet thompson": "jett thompson",    # Jet/Jett
    "erico cocco": "enrico cocco",      # Erico/Enrico
    "josh barnet": "josh barnett",      # dropped 't'
    "eliot kelly": "eliott kelly",      # Eliot/Eliott
    "kamil uminski": "kamil huminski",  # dropped 'H'
    "akira shouji": "akira shoji",      # Shouji/Shoji romanization
    "ruan alvarena": "ruan alvarenga",  # dropped 'g'
    "c hellenberg": "casey hellenberg",  # initial → Casey
    "p donabedian": "patrick donabedian",  # initial → Patrick
    "p gaudio": "patrick gaudio",       # initial → Patrick
    "felipe pena sf": "felipe pena",    # leaked "SF" (semifinal) stage tag
    "ffion davies final": "ffion davies",  # leaked "Final" stage tag (ADCC 2022 women dup row)
    # Sula(-Mae) Loewenthal/Lowenthal — one human, four spellings across dumps. "Gita" is the
    # ADCC 2024 supercut transcript's name for the same athlete: the AO-Trials winner seeded #8
    # who met Mesquita in the -65 opening round IS Sula-Mae Loewenthal (bout-evidence rationale).
    "gita lowenthal": "sula mae lowenthal",
    "sula loewenthal": "sula mae lowenthal",
    "sula mae loewenthal": "sula mae lowenthal",
    "kyle bame": "kyle boehm",          # "Bame" transcription typo → Kyle Boehm
    # ADCC 2026 women's roster, confirmed 2026-08-20 from the bouts themselves rather than
    # from a string-similarity score. Each merges into the spelling the roster manifest uses.
    "helena cravar": "helena crevar",          # transposed letter; her one bout is WNO 31
                                               # 2025, and Crevar fought WNO 25 and 27
    "raphaela guedes": "rafaela guedes",       # ph/f spelling; both rows sit in ADCC 2022,
                                               # one W and one L, a normal bracket path
    "r guedes": "rafaela guedes",              # initial form; only one Guedes in the corpus
    "raphaela guedes final": "rafaela guedes", # stage tag leaked into the name, same class as
                                               # "felipe pena sf" above
    "mo black": "morgan black",                # nickname; ADCC Trials 2023/2024 then ADCC 2024
    "gabby garcia": "gabi garcia",             # her single bout is Craig Jones at CJI 2024 —
                                               # the publicised superfight, which is hers
    "gabrielle garcia": "gabi garcia",         # full name, no bouts; aliased so a future
                                               # import cannot open a third row
    # Confirmed by the project owner, 2026-08-20, which is corroboration from outside the
    # string -- the thing the rule asks for and the thing the data could not supply. The bouts
    # alone never settled it: three each, no shared event, and a double variation.
    "anna karolina vieira": "ana carolina vieira",
    # "Jocelyn" DB spelling vs "Joslyn" roster manifest spelling, same human (her one bout,
    # WNO 31 2025, is the same fight the manifest lists for "Joslyn Molina"). The scouting
    # records layer (data/scouting/adcc_2026_women.json) already carries this as an alias;
    # this entry is the same call for the rating-engine identity key, which reads a separate
    # DB name and had no way to see that file's decision.
    "jocelyn molina": "joslyn molina",
    # NOTE: Junny vs Edwin Ocasio, Maia vs Mayssa Bastos, George vs Jorge Santos are
    # DISTINCT people (real bouts) — do not alias.
}


# Pairs that LOOK like one person and are not, or that were examined and left unresolved.
#
# Until now this knowledge lived in comments, which no consumer can read: a report showing a
# near-identical pair had no way to say whether anyone had already looked at it, so a decided
# case and an unexamined one appeared identically on the page. `kind` separates the two answers
# that matter -- "these are two people" from "we looked and cannot tell" -- and `note` carries
# the evidence in Portuguese, because the only thing that renders it is a Portuguese report.
#
# ``distinct``   -- corroborated as different humans. Never alias these.
# ``unresolved`` -- examined, evidence does not settle it either way. Not a to-do; a finding.
REVIEWED_NOT_MERGED: tuple[tuple[str, str, str, str], ...] = (
    ("junny ocasio", "edwin ocasio", "distinct",
     "Lutas reais de ambos no corpus; são dois atletas."),
    ("maia bastos", "mayssa bastos", "distinct", "Duas atletas distintas."),
    ("george santos", "jorge santos", "distinct", "Dois atletas distintos."),
    ("andrew tackett", "william tackett", "distinct", "Irmãos."),
    ("tye ruotolo", "kade ruotolo", "distinct", "Irmãos gêmeos."),
    ("mica galvao", "mike galvao", "distinct", "Ambíguo o bastante para nunca fundir."),
    ("d johnson", "tex johnson", "distinct", "Forma de inicial ambígua; não fundir."),
)


def reviewed_verdict(key_a: str, key_b: str) -> tuple[str, str] | None:
    """``(kind, note)`` when this pair has already been judged, else None. Order-independent."""
    pair = {key_a, key_b}
    for a, b, kind, note in REVIEWED_NOT_MERGED:
        if {a, b} == pair:
            return kind, note
    return None


def roster_aliases(keys: Iterable[str]) -> dict[str, str]:
    """Alias entries whose canonical target is one of ``keys`` -- i.e. the splits already
    closed for that roster. Lets a report say how many it resolved, not just what is left."""
    wanted = set(keys)
    return {src: dst for src, dst in ATHLETE_ALIASES.items() if dst in wanted}


def raw_athlete_key(name: str) -> str:
    """Identity key BEFORE alias resolution (cleaned, de-accented, normalized)."""
    return _normalize_name(_deaccent(clean_athlete_name(name)))


def athlete_key(name: str) -> str:
    """Identity key for athlete dedup: cleaned, de-accented, normalized + alias-resolved."""
    k = raw_athlete_key(name)
    return ATHLETE_ALIASES.get(k, k)


def _normalize_adcc_sub(name: str) -> str:
    """Normalize + resolve aliases for ADCC submission names.

    Merges variants like "inside heel hook" / "outside heel hook" → "heel hook",
    "rnc" → "rear naked choke", etc.
    """
    return _resolve_aliases(_normalize_name(name))
