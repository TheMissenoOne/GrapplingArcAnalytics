#!/usr/bin/env python
"""Resolve the 204 web-researched decision results (ADR-06, docs/rating_v2/01_DECISOES.md).

Source spreadsheet: 204 rows of ``winner_id IS NULL AND win_type='DECISION'`` matches, each
web-researched by hand into a result. Reads the xlsx + the DB, proposes a fix per row, and
writes two reports. **Read-only by default** — ``--apply`` exists but writes are a separate,
deliberate decision; this script never mutates the DB unless that flag is passed.

    uv run python -m scripts.apply_decision_results                # dry-run (default)
    uv run python -m scripts.apply_decision_results --apply         # write to DB

Resolution per row, by ``status_resultado``:
  - Vitória   -> map ``vencedor`` to one of the match's two athletes: exact ``athlete_key``
                 match first, then string similarity (``difflib``) against BOTH names,
                 accepted only if the top score is >=0.82 AND beats the runner-up by >=0.10.
                 Otherwise unresolved -> human review.
  - Empate    -> ``winner_id`` stays NULL, ``win_type`` becomes 'DRAW' (the semantic fix
                 ADR-06 calls for -- these rows are wrongly 'DECISION' today).
  - Cancelada / Não realizada / Registro inválido / Ambígua -> untouched, human review.

``metodo`` maps to ``win_type`` only via the safe keyword rule (decis*/decision -> DECISION,
ponto/points -> POINTS, named finish -> SUBMISSION + ``matches.submission`` if empty);
anything outside that 4-value vocabulary (DQ, injury stoppage, bare "Overtime"...) is left
untouched and the row is added to the human-review report instead of guessed.
``ano_corrigido`` / ``evento_corrigido`` propose ``matches.year`` / ``matches.event`` when
they differ from the DB. A field that already holds a DIFFERENT non-empty value than what
would be written is a conflict -- reported, never silently overwritten.

xlsx read without openpyxl (not in the venv): unzip + parse the two XML parts we need
(shared strings + one worksheet) with stdlib ``zipfile``/``xml.etree`` -- ponytail: fine for
a one-shot script, add openpyxl only if this needs to become a repeatable ingestion path.
"""

from __future__ import annotations

import argparse
import csv
import logging
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

DEFAULT_XLSX = Path("/home/vetor/Downloads/decisoes_sem_vencedor_resultados_web.xlsx")
SHEET_NAME = "Resultados"
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rating_v2"
DRY_RUN_CSV = REPORT_DIR / "aplicacao_dry_run.csv"
REVIEW_CSV = REPORT_DIR / "revisao_humana.csv"

SIMILARITY_MIN = 0.82
SIMILARITY_GAP_MIN = 0.10

STATUS_WIN = "Vitória"
STATUS_DRAW = "Empate"
# Read as data, not inferred: these 4 statuses change nothing (ADR-06 spirit — failure closed).
STATUS_NO_ACTION = {"Cancelada", "Não realizada", "Registro inválido", "Ambígua"}

# Keyword rule for metodo -> win_type. Checked in order: decis*/decision -> DECISION;
# ponto/points (and no finish named) -> POINTS (matches.win_type vocabulary: DECISION 519 /
# SUBMISSION 350 / POINTS 11 / DRAW 3 — POINTS carries its own K multiplier in the V1 ELO
# engine, 1.0 vs DECISION's 0.85, so folding "Pontos N-0" into DECISION would understate K);
# named finish -> SUBMISSION. Grounded in the real 148 Vitória rows (2026-08-17 xlsx) — every
# value classified except DQ, an unnamed injury stoppage, and bare "Overtime" (3x): none of
# those three map into the 4-value win_type vocabulary, so they're left untouched and flagged
# for human review instead of invented.
_POINTS_KEYWORDS = ("ponto", "points")
_SUBMISSION_KEYWORDS = (
    "choke", "armbar", "arm lock", "triangle", "kimura", "americana", "omoplata", "plata",
    "guillotine", "kneebar", "ankle lock", "heel hook", "toehold", "toe hold", "wristlock",
    "wrist lock", "calf slicer", "banana split", "gogoplata", "peruvian necktie", "twister",
    "anaconda", "darce", "d'arce", "rnc", "rear naked", "rear-naked", "bulldog", "ezekiel",
    "lock", "hook", "submiss", "chave", "estrangulamento", "finaliz",
    "dead orchard",  # named finish (crank/choke), not an unknown method
)


@dataclass
class Resolution:
    match_id: str
    event_db: str | None
    event_proposed: str | None
    year_db: int | None
    year_proposed: int | None
    athlete_a: str
    athlete_b: str
    status: str
    vencedor_planilha: str
    winner_name: str | None
    winner_id: str | None
    method: str  # "exato" | "similaridade" | "nenhum" | "n/a"
    score: float | None
    win_type_current: str | None
    win_type_proposed: str | None
    submission_proposed: str | None
    confidence: str
    conflicts: list[str] = field(default_factory=list)
    source_1: str = ""
    source_2: str = ""
    needs_review: bool = False
    review_reason: str = ""


def _classify_method(status: str, metodo: str) -> tuple[str | None, str | None]:
    """(win_type, submission_text) proposed from status_resultado/metodo. Safe subset only."""
    if status == STATUS_DRAW:
        return "DRAW", None
    if status != STATUS_WIN:
        return None, None
    m = (metodo or "").lower()
    if "decis" in m or "decision" in m:
        return "DECISION", None
    if any(kw in m for kw in _POINTS_KEYWORDS):
        return "POINTS", None
    if any(kw in m for kw in _SUBMISSION_KEYWORDS):
        return "SUBMISSION", (metodo or "").strip() or None
    return None, None


def resolve_winner(
    vencedor: str, a_name: str, a_id: str, b_name: str, b_id: str,
) -> tuple[str | None, str | None, str, float | None]:
    """(winner_id, winner_name, method, score). method: exato/similaridade/nenhum."""
    from analysis.names import athlete_key

    v_key = athlete_key(vencedor)
    a_match = v_key == athlete_key(a_name)
    b_match = v_key == athlete_key(b_name)
    if a_match and not b_match:
        return a_id, a_name, "exato", None
    if b_match and not a_match:
        return b_id, b_name, "exato", None

    v_norm = vencedor.strip().lower()
    score_a = SequenceMatcher(None, v_norm, a_name.strip().lower()).ratio()
    score_b = SequenceMatcher(None, v_norm, b_name.strip().lower()).ratio()
    best_name, best_id, best = (a_name, a_id, score_a) if score_a >= score_b else (b_name, b_id, score_b)
    worst = score_b if best is score_a else score_a
    if best >= SIMILARITY_MIN and (best - worst) >= SIMILARITY_GAP_MIN:
        return best_id, best_name, "similaridade", best
    return None, None, "nenhum", max(score_a, score_b)


def resolve_row(row: dict[str, str], match: dict[str, object]) -> Resolution:
    """Pure: one xlsx row + one DB match state -> proposed Resolution. No I/O."""
    status = row["status_resultado"].strip()
    a_name, a_id = str(match["athlete_a_name"]), str(match["athlete_a_id"])
    b_name, b_id = str(match["athlete_b_name"]), str(match["athlete_b_id"])
    db_year = match.get("year")
    db_event = match.get("event")
    db_win_type = match.get("win_type")
    db_submission = match.get("submission")
    db_winner_id = match.get("winner_id")

    res = Resolution(
        match_id=row["match_id"], event_db=db_event, event_proposed=None,
        year_db=db_year, year_proposed=None, athlete_a=a_name, athlete_b=b_name,
        status=status, vencedor_planilha=row.get("vencedor", ""),
        winner_name=None, winner_id=None, method="n/a", score=None,
        win_type_current=db_win_type, win_type_proposed=None, submission_proposed=None,
        confidence=row.get("confianca", ""), source_1=row.get("fonte_1", ""),
        source_2=row.get("fonte_2", ""),
    )

    if status in STATUS_NO_ACTION:
        res.needs_review = True
        res.review_reason = f"status_resultado={status!r} — não altera nada, decisão humana"
        return res

    # Winner (Vitória only).
    if status == STATUS_WIN:
        vencedor = row.get("vencedor", "").strip()
        if vencedor:
            winner_id, winner_name, method, score = resolve_winner(vencedor, a_name, a_id, b_name, b_id)
            res.winner_id, res.winner_name, res.method, res.score = winner_id, winner_name, method, score
            if method == "nenhum":
                res.needs_review = True
                res.review_reason = (
                    f"vencedor {vencedor!r} não resolvido entre {a_name!r}/{b_name!r} "
                    f"(scores {score:.3f} / limiar {SIMILARITY_MIN})"
                )
        else:
            res.method = "nenhum"
            res.needs_review = True
            res.review_reason = "status_resultado=Vitória sem coluna vencedor preenchida"

        if db_winner_id not in (None, "") and res.winner_id is not None and db_winner_id != res.winner_id:
            res.conflicts.append("winner_id")

    # win_type / submission.
    metodo = row.get("metodo", "")
    win_type_raw, submission_text = _classify_method(status, metodo)
    method_unclassified = status == STATUS_WIN and win_type_raw is None
    win_type_proposed = win_type_raw
    if win_type_proposed == db_win_type:
        win_type_proposed = None  # no-op, nothing to propose
    if win_type_proposed is not None and db_win_type not in (None, "", "DECISION") and db_win_type != win_type_proposed:
        res.conflicts.append("win_type")
        win_type_proposed = None  # don't overwrite silently
    res.win_type_proposed = win_type_proposed

    if submission_text is not None:
        if db_submission in (None, ""):
            res.submission_proposed = submission_text
        elif str(db_submission).strip() != submission_text.strip():
            res.conflicts.append("submission")

    # Método sem representação no vocabulário de win_type (DQ, injury stoppage, bare
    # "Overtime"...): winner still applies normally, but flag for a human in one place
    # instead of leaving it silently unrepresented.
    if method_unclassified and res.winner_id is not None and not res.needs_review:
        res.needs_review = True
        res.review_reason = (
            f"método {metodo!r} sem representação em win_type "
            "(DECISION/SUBMISSION/POINTS/DRAW) — vencedor resolvido, win_type intocado"
        )

    # Year / event corrections — applied even without a resolved winner (ADR-06: the wrong
    # year/event misplaces the match in the replay period regardless of who won).
    ano_corrigido = (row.get("ano_corrigido") or "").strip()
    if ano_corrigido:
        try:
            year_val = int(float(ano_corrigido))
        except ValueError:
            year_val = None
        if year_val is not None and year_val != db_year:
            res.year_proposed = year_val

    evento_corrigido = (row.get("evento_corrigido") or "").strip()
    if evento_corrigido and evento_corrigido != (db_event or ""):
        res.event_proposed = evento_corrigido

    if res.conflicts:
        res.needs_review = True
        if not res.review_reason:
            res.review_reason = f"conflito em: {', '.join(res.conflicts)}"

    return res


# ── xlsx reader (stdlib only, no openpyxl) ──────────────────────────────────────────────────

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NSR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col_letters(cell_ref: str) -> str:
    return "".join(c for c in cell_ref if c.isalpha())


def read_xlsx_sheet(path: Path, sheet_name: str) -> list[dict[str, str]]:
    """Read one worksheet of a .xlsx into a list of header->value dicts. No dependency —
    unzips the OOXML parts and parses them with stdlib ``xml.etree``."""
    with zipfile.ZipFile(path) as zf:
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in sst.findall(f"{_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))

        rid_to_target = {
            r.get("Id"): r.get("Target") for r in rels.findall(".//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
        }
        sheet_el = next(
            s for s in workbook.findall(f"{_NS}sheets/{_NS}sheet") if s.get("name") == sheet_name
        )
        target = rid_to_target[sheet_el.get(f"{_NSR}id")].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        sheet = ET.fromstring(zf.read(target))

        def cell_value(c: ET.Element) -> str:
            t = c.get("t")
            if t == "s":
                v = c.find(f"{_NS}v")
                return shared[int(v.text)] if v is not None and v.text else ""
            if t == "inlineStr":
                parts = c.findall(f"{_NS}is/{_NS}t")
                return "".join(p.text or "" for p in parts)
            v = c.find(f"{_NS}v")
            return v.text if v is not None and v.text is not None else ""

        rows_xml = sheet.findall(f"{_NS}sheetData/{_NS}row")
        if not rows_xml:
            return []
        header_cells = rows_xml[0].findall(f"{_NS}c")
        header = {_col_letters(c.get("r")): cell_value(c) for c in header_cells}

        records: list[dict[str, str]] = []
        for row_el in rows_xml[1:]:
            rec: dict[str, str] = dict.fromkeys(header.values(), "")
            for c in row_el.findall(f"{_NS}c"):
                col = _col_letters(c.get("r"))
                name = header.get(col)
                if name is not None:
                    rec[name] = cell_value(c)
            records.append(rec)
        return records


# ── DB-facing run() — read-only unless --apply ──────────────────────────────────────────────

_RESOLUTION_FIELDS = [
    "match_id", "evento_banco", "evento_proposto", "ano_banco", "ano_proposto",
    "atleta_a", "atleta_b", "status_resultado", "vencedor_planilha",
    "vencedor_resolvido_nome", "vencedor_resolvido_id", "metodo_resolucao", "escore",
    "win_type_atual", "win_type_proposto", "confianca", "conflito", "fonte_1", "fonte_2",
]


def _to_csv_row(res: Resolution) -> dict[str, str]:
    return {
        "match_id": res.match_id,
        "evento_banco": res.event_db or "",
        "evento_proposto": res.event_proposed or "",
        "ano_banco": "" if res.year_db is None else str(res.year_db),
        "ano_proposto": "" if res.year_proposed is None else str(res.year_proposed),
        "atleta_a": res.athlete_a,
        "atleta_b": res.athlete_b,
        "status_resultado": res.status,
        "vencedor_planilha": res.vencedor_planilha,
        "vencedor_resolvido_nome": res.winner_name or "",
        "vencedor_resolvido_id": res.winner_id or "",
        "metodo_resolucao": res.method,
        "escore": "" if res.score is None else f"{res.score:.3f}",
        "win_type_atual": res.win_type_current or "",
        "win_type_proposto": res.win_type_proposed or "",
        "confianca": res.confidence,
        "conflito": "sim" if res.conflicts else "não",
        "fonte_1": res.source_1,
        "fonte_2": res.source_2,
    }


def _eligible(win_type: str | None, winner_id: str | None) -> bool:
    """ADR-06 eligibility: known winner OR explicit draw. Everything else (incl. unknown
    'DECISION' with no winner) is excluded from the replay."""
    return winner_id is not None or win_type == "DRAW"


def run(xlsx_path: Path, apply: bool) -> int:
    from sqlalchemy import select

    from db.base import db_session
    from db.models import Athlete, Match

    rows = read_xlsx_sheet(xlsx_path, SHEET_NAME)

    with db_session() as session:
        matches = {m.id: m for m in session.execute(
            select(Match).where(Match.id.in_([r["match_id"] for r in rows]))
        ).scalars()}
        athlete_names = dict(session.execute(select(Athlete.id, Athlete.name)).all())

        # "Invisible" today: status='final' match, and not one eligible match anywhere.
        final_matches = list(session.execute(select(Match).where(Match.status == "final")).scalars())
        eligible_by_athlete: dict[str, int] = {}
        for m in final_matches:
            if _eligible(m.win_type, m.winner_id):
                eligible_by_athlete[m.athlete_a_id] = eligible_by_athlete.get(m.athlete_a_id, 0) + 1
                eligible_by_athlete[m.athlete_b_id] = eligible_by_athlete.get(m.athlete_b_id, 0) + 1
        # Scoped to athletes who actually appear in THIS batch — matches the user's own
        # "175 invisíveis" figure (DB-wide zero-eligible-match count is a different, much
        # larger number and isn't what the batch can move).
        batch_athletes = {aid for m in matches.values() for aid in (m.athlete_a_id, m.athlete_b_id)}
        invisible_before = {aid for aid in batch_athletes if eligible_by_athlete.get(aid, 0) == 0}

        resolutions: list[Resolution] = []
        newly_eligible_pairs: list[tuple[str, str]] = []
        for row in rows:
            match = matches.get(row["match_id"])
            if match is None:
                logger.warning("match_id %s from xlsx not found in DB — skipped", row["match_id"])
                continue
            db_state = {
                "match_id": match.id,
                "athlete_a_name": athlete_names.get(match.athlete_a_id, "?"),
                "athlete_a_id": match.athlete_a_id,
                "athlete_b_name": athlete_names.get(match.athlete_b_id, "?"),
                "athlete_b_id": match.athlete_b_id,
                "year": match.year, "event": match.event, "win_type": match.win_type,
                "submission": match.submission, "winner_id": match.winner_id,
            }
            res = resolve_row(row, db_state)
            resolutions.append(res)

            becomes_eligible = (res.winner_id is not None) or (res.win_type_proposed == "DRAW")
            if becomes_eligible and match.status == "final":
                newly_eligible_pairs.append((match.athlete_a_id, match.athlete_b_id))

            # A method with no win_type representation (needs_review for the CSV listing)
            # still applies winner/year/event normally — only unresolved winner, an
            # untouched status, or an actual conflict blocks a write.
            blocks_write = (
                res.status in STATUS_NO_ACTION
                or (res.status == STATUS_WIN and res.winner_id is None)
                or bool(res.conflicts)
            )
            if apply and not blocks_write:
                if res.winner_id is not None:
                    match.winner_id = res.winner_id
                if res.win_type_proposed is not None:
                    match.win_type = res.win_type_proposed
                if res.submission_proposed is not None:
                    match.submission = res.submission_proposed
                if res.year_proposed is not None:
                    match.year = res.year_proposed
                if res.event_proposed is not None:
                    match.event = res.event_proposed

        newly_visible = set()
        for a_id, b_id in newly_eligible_pairs:
            if a_id in invisible_before:
                newly_visible.add(a_id)
            if b_id in invisible_before:
                newly_visible.add(b_id)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with DRY_RUN_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_RESOLUTION_FIELDS)
        writer.writeheader()
        for res in resolutions:
            writer.writerow(_to_csv_row(res))

    review_fields = [*_RESOLUTION_FIELDS, "motivo"]
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=review_fields)
        writer.writeheader()
        for res in resolutions:
            if res.needs_review:
                writer.writerow({**_to_csv_row(res), "motivo": res.review_reason})

    n_win = sum(1 for r in resolutions if r.status == STATUS_WIN and r.winner_id is not None)
    n_draw = sum(1 for r in resolutions if r.status == STATUS_DRAW)
    n_year = sum(1 for r in resolutions if r.year_proposed is not None)
    n_event = sum(1 for r in resolutions if r.event_proposed is not None)
    n_review = sum(1 for r in resolutions if r.needs_review)
    logger.info("linhas processadas: %d", len(resolutions))
    logger.info("lutas que ganham vencedor: %d", n_win)
    logger.info("lutas que viram DRAW: %d", n_draw)
    logger.info("lutas que mudam de ano: %d", n_year)
    logger.info("lutas que mudam de evento: %d", n_event)
    logger.info("linhas para revisão humana: %d", n_review)
    logger.info("atletas invisíveis hoje: %d", len(invisible_before))
    logger.info("desses, passam a ter luta elegível com este lote: %d", len(newly_visible))
    logger.info("%s -> %s, %s", "APLICADO" if apply else "DRY-RUN", DRY_RUN_CSV, REVIEW_CSV)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Resolve web-researched decision results (ADR-06)")
    ap.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    ap.add_argument("--apply", action="store_true", help="write to DB (default: dry-run only)")
    args = ap.parse_args()
    return run(args.xlsx, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
