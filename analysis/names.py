"""Name normalization helpers for BJJ technique names — extracted from export/tech_library.py."""

from __future__ import annotations

import re
import unicodedata

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
    "kyle bame": "kyle boehm",          # "Bame" transcription typo → Kyle Boehm
    # NOTE: Junny vs Edwin Ocasio, Maia vs Mayssa Bastos, George vs Jorge Santos are
    # DISTINCT people (real bouts) — do not alias.
}


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
