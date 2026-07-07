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
    # NOTE: Junny vs Edwin Ocasio, Maia vs Mayssa Bastos, George vs Jorge Santos are
    # DISTINCT people (real bouts) — do not alias.
}


def athlete_key(name: str) -> str:
    """Identity key for athlete dedup: cleaned, de-accented, normalized + alias-resolved."""
    k = _normalize_name(_deaccent(clean_athlete_name(name)))
    return ATHLETE_ALIASES.get(k, k)


def _normalize_adcc_sub(name: str) -> str:
    """Normalize + resolve aliases for ADCC submission names.

    Merges variants like "inside heel hook" / "outside heel hook" → "heel hook",
    "rnc" → "rear naked choke", etc.
    """
    return _resolve_aliases(_normalize_name(name))
