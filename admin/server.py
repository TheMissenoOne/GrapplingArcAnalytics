"""Admin FastAPI app — pro-athlete data entry and analytics dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from admin.auth import (
    _COOKIE_NAME,
    check_password,
    create_session,
    destroy_session,
    is_authenticated,
)
from cv.vocab_map import load_app_nodes
from db.base import db_session
from db.models import (
    Archetype,
    Athlete,
    Dilemma,
    Graph,
    Match,
    Milestone,
    Principle,
    System,
    SystemDilemma,
    SystemImplementation,
    SystemPrinciple,
    TechniqueNode,
)
from db.ontology_repository import (
    add_milestone,
    delete_archetype,
    delete_dilemma,
    delete_implementation,
    delete_milestone,
    delete_principle,
    delete_system,
    set_position_decision_space,
    set_system_dilemmas,
    set_system_principles,
    slugify,
    upsert_dilemma,
    upsert_implementation,
    upsert_principle,
    upsert_system,
    upsert_target_archetype,
)
from db.repository import (
    approve_match,
    delete_match,
    get_match,
    get_matches_for_athlete,
    publish_athlete,
    register_match,
    replay_and_persist_athlete,
    seed_athletes_from_leaderboard,
    update_match,
    upsert_athlete,
)
from db.scraped_import import import_scraped_dir
from export.athlete_graph_export import athlete_graph_to_app_json
from harvest.harvester import (
    HARVEST_INBOX,
    HARVEST_PROCESSED,
    harvest_playlist,
    harvest_urls,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _lines(raw: str) -> list[str]:
    """Split a textarea/comma field into a clean list (newlines or commas, trimmed)."""
    parts = raw.replace(",", "\n").splitlines()
    return [p.strip() for p in parts if p.strip()]


# ── Global-match perspective helpers ────────────────────────────────────────
def _other_id(match: Match, athlete_id: str) -> str:
    return match.athlete_b_id if match.athlete_a_id == athlete_id else match.athlete_a_id


def _result_for(match: Match, athlete_id: str) -> str:
    if (match.win_type or "").upper() == "DRAW" or match.winner_id is None:
        return "D"
    return "W" if match.winner_id == athlete_id else "L"


def _match_view(
    match: Match, athlete_id: str, others: dict[str, Athlete]
) -> dict[str, Any]:
    """A global match rendered from ``athlete_id``'s side for the match table.

    ``others`` is a prefetched ``{athlete_id: Athlete}`` map of every opponent (built in
    one query by the caller) — avoids an N+1 ``session.get`` per match."""
    other = others.get(_other_id(match, athlete_id))
    return {
        "id": match.id,
        "year": match.year,
        "event": match.event,
        "opponent_name": other.name if other else "—",
        "win_type": match.win_type,
        "stage": match.stage,
        "submission": match.submission,
        "result": _result_for(match, athlete_id),
        "seq_len": len(match.sequence or []),
        "status": match.status,
    }


def _seq_with_actor_ids(
    sequence: list[dict[str, Any]], you_id: str, opp_id: str
) -> list[dict[str, Any]]:
    """Map a you/opponent chip sequence → actor_id events for storage."""
    out: list[dict[str, Any]] = []
    for e in sequence:
        if not isinstance(e, dict):
            continue
        item = {k: v for k, v in e.items() if k != "actor"}
        item["actor_id"] = you_id if e.get("actor") == "you" else opp_id
        out.append(item)
    return out


def _seq_to_perspective(
    sequence: list[dict[str, Any]] | None, you_id: str
) -> list[dict[str, Any]]:
    """Map a stored actor_id sequence → you/opponent for the chip builder prefill."""
    out: list[dict[str, Any]] = []
    for e in sequence or []:
        if not isinstance(e, dict):
            continue
        item = {k: v for k, v in e.items() if k != "actor_id"}
        item["actor"] = "you" if e.get("actor_id") == you_id else "opponent"
        out.append(item)
    return out


def _winner_id(won: bool, win_type: str | None, you_id: str, opp_id: str) -> str | None:
    if (win_type or "").upper() == "DRAW":
        return None
    return you_id if won else opp_id


def _resolve_opponent(
    opponent_athlete_id: str, opponent_name: str, session: Any
) -> Athlete | None:
    """Existing athlete by id, else get-or-create by name (both sides are athletes)."""
    from analysis.names import _normalize_name

    if opponent_athlete_id:
        existing: Athlete | None = session.get(Athlete, opponent_athlete_id)
        return existing
    name = (opponent_name or "").strip()
    if not name:
        return None
    norm = _normalize_name(name)
    for a in session.execute(select(Athlete)).scalars():
        if _normalize_name(a.name) == norm:
            matched: Athlete = a
            return matched
    aid = upsert_athlete(name=name, belt="black", source="opponent", session=session)
    created: Athlete | None = session.get(Athlete, aid)
    return created


def _opponent_options(athlete_id: str, session: Any) -> list[dict[str, Any]]:
    """All athletes except this one, ranked first, for the opponent picker."""
    rows = session.execute(
        select(Athlete)
        .where(Athlete.id != athlete_id)
        .order_by(Athlete.rank_elo.desc().nullslast(), Athlete.name)
    ).scalars()
    return [{"id": a.id, "name": a.name, "rank_elo": a.rank_elo} for a in rows]


def create_admin_app() -> FastAPI:
    app = FastAPI(title="GrapplingArc Admin", docs_url=None, redoc_url=None)

    if STATIC_DIR.is_dir():
        app.mount("/admin/static", StaticFiles(directory=str(STATIC_DIR)), name="admin-static")

    # Load node vocab once at startup for the match-entry picker
    app.state.node_options = _build_node_options()

    # ── Auth ──────────────────────────────────────────────────────────────
    @app.get("/admin/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "login.html", context={"error": None})

    @app.post("/admin/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        password: str = Form(...),
    ) -> Any:
        if not check_password(password):
            return templates.TemplateResponse(
                request, "login.html", context={"error": "Invalid password"}
            )
        token = create_session()
        resp = RedirectResponse("/admin/athletes", status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie(_COOKIE_NAME, token, httponly=True, samesite="lax")
        return resp

    @app.get("/admin/logout")
    def logout(request: Request) -> RedirectResponse:
        token = request.cookies.get(_COOKIE_NAME, "")
        destroy_session(token)
        resp = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        resp.delete_cookie(_COOKIE_NAME)
        return resp

    # ── Athletes list ──────────────────────────────────────────────────────
    @app.get("/admin/athletes", response_class=HTMLResponse)
    def athletes_list(request: Request) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            athletes = list(
                session.execute(
                    select(Athlete).order_by(Athlete.rank_elo.desc().nullslast())
                ).scalars()
            )
        return templates.TemplateResponse(
            request, "athletes.html", context={"athletes": athletes}
        )

    @app.post("/admin/athletes", response_class=HTMLResponse)
    def create_athlete(
        request: Request,
        name: str = Form(...),
        nickname: str = Form(""),
        team: str = Form(""),
        weight_class: str = Form(""),
        belt: str = Form(""),
    ) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            athlete_id = upsert_athlete(
                name=name,
                nickname=nickname or None,
                team=team or None,
                weight_class=weight_class or None,
                belt=belt or None,
                session=session,
            )
        return RedirectResponse(
            f"/admin/athletes/{athlete_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    # ── Seed athletes from the ADCC leaderboard ─────────────────────────────
    @app.post("/admin/athletes/seed")
    def seed_athletes(request: Request) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            seed_athletes_from_leaderboard(session)
        return RedirectResponse("/admin/athletes", status_code=status.HTTP_303_SEE_OTHER)

    # ── Athlete detail ─────────────────────────────────────────────────────
    @app.get("/admin/athletes/{athlete_id}", response_class=HTMLResponse)
    def athlete_detail(request: Request, athlete_id: str) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            athlete = session.get(Athlete, athlete_id)
            if athlete is None:
                raise HTTPException(status_code=404)
            all_matches = get_matches_for_athlete(athlete_id, session)
            # Prefetch every opponent in ONE query (not session.get per match → no N+1).
            other_ids = {_other_id(m, athlete_id) for m in all_matches}
            others = {
                a.id: a
                for a in session.execute(
                    select(Athlete).where(Athlete.id.in_(other_ids))
                ).scalars()
            } if other_ids else {}
            views = [_match_view(m, athlete_id, others) for m in all_matches]
            # Sort here (not in Jinja): year may be None and `|sort` would compare
            # None to int → TypeError. None sinks to the bottom (year or 0).
            views.sort(key=lambda v: v["year"] or 0, reverse=True)
            final_views = [v for v in views if v["status"] == "final"]
            draft_views = [v for v in views if v["status"] == "draft"]
            graph = session.execute(
                select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete_id)
            ).scalar_one_or_none()
            graph_json = athlete_graph_to_app_json(graph.id, session) if graph else None
            opponent_options = _opponent_options(athlete_id, session)
            series = athlete.elo_series or []
        return templates.TemplateResponse(
            request,
            "athlete_detail.html",
            context={
                "athlete": athlete,
                "matches": final_views,
                "drafts": draft_views,
                "graph": graph_json,
                "node_options": app.state.node_options,
                "opponent_options": opponent_options,
                "graph_elo_series": series,
            },
        )

    # ── Register match (global: opponent is an athlete) ─────────────────────
    @app.post("/admin/athletes/{athlete_id}/matches", response_class=HTMLResponse)
    def add_match(
        request: Request,
        athlete_id: str,
        opponent_name: str = Form(""),
        opponent_athlete_id: str = Form(""),
        event: str = Form(""),
        year: str = Form(""),
        weight_class: str = Form(""),
        win_type: str = Form(""),
        stage: str = Form(""),
        submission: str = Form(""),
        video_url: str = Form(""),
        won: str = Form("true"),
        sequence_json: str = Form("[]"),
    ) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        import json

        try:
            sequence = json.loads(sequence_json)
        except Exception:
            sequence = []

        with db_session() as session:
            athlete = session.get(Athlete, athlete_id)
            if athlete is None:
                raise HTTPException(status_code=404)
            opp = _resolve_opponent(opponent_athlete_id, opponent_name, session)
            if opp is None:
                raise HTTPException(status_code=400, detail="An opponent athlete is required")
            if opp.id == athlete_id:
                raise HTTPException(status_code=400, detail="An athlete cannot fight themselves")

            wt = win_type or None
            register_match(
                athlete_id,
                opp.id,
                winner_id=_winner_id(won.lower() == "true", wt, athlete_id, opp.id),
                win_type=wt,
                submission=submission or None,
                event=event or None,
                year=int(year) if year.isdigit() else None,
                weight_class=weight_class or None,
                stage=stage or None,
                video_url=video_url or None,
                sequence=_seq_with_actor_ids(sequence, athlete_id, opp.id),
                created_by=None,
                session=session,
            )
            # Double pass — rebuild both participants' graphs.
            replay_and_persist_athlete(athlete, session)
            replay_and_persist_athlete(opp, session)

        return RedirectResponse(
            f"/admin/athletes/{athlete_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    # ── Edit an existing match (then re-replay both sides) ──────────────────
    @app.get("/admin/athletes/{athlete_id}/matches/{match_id}/edit",
             response_class=HTMLResponse)
    def edit_match_form(request: Request, athlete_id: str, match_id: str) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            athlete = session.get(Athlete, athlete_id)
            match = get_match(match_id, session)
            if athlete is None or match is None or athlete_id not in (
                match.athlete_a_id, match.athlete_b_id
            ):
                raise HTTPException(status_code=404)
            opp = session.get(Athlete, _other_id(match, athlete_id))
            ctx_match = {
                "id": match.id,
                "event": match.event,
                "year": match.year,
                "weight_class": match.weight_class,
                "win_type": match.win_type,
                "stage": match.stage,
                "submission": match.submission,
                "video_url": match.video_url,
                "won": match.winner_id == athlete_id,
                "opponent_id": opp.id if opp else "",
                "opponent_name": opp.name if opp else "",
                "sequence": _seq_to_perspective(match.sequence, athlete_id),
            }
            opponent_options = _opponent_options(athlete_id, session)
        return templates.TemplateResponse(
            request,
            "edit_match.html",
            context={
                "athlete": athlete,
                "match": ctx_match,
                "node_options": app.state.node_options,
                "opponent_options": opponent_options,
            },
        )

    @app.post("/admin/athletes/{athlete_id}/matches/{match_id}",
              response_class=HTMLResponse)
    def update_match_route(
        request: Request,
        athlete_id: str,
        match_id: str,
        opponent_name: str = Form(""),
        opponent_athlete_id: str = Form(""),
        event: str = Form(""),
        year: str = Form(""),
        weight_class: str = Form(""),
        win_type: str = Form(""),
        stage: str = Form(""),
        submission: str = Form(""),
        video_url: str = Form(""),
        won: str = Form("true"),
        sequence_json: str = Form("[]"),
    ) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        import json

        try:
            sequence = json.loads(sequence_json)
        except Exception:
            sequence = []

        with db_session() as session:
            athlete = session.get(Athlete, athlete_id)
            match = get_match(match_id, session)
            if athlete is None or match is None or athlete_id not in (
                match.athlete_a_id, match.athlete_b_id
            ):
                raise HTTPException(status_code=404)
            prev_other_id = _other_id(match, athlete_id)
            opp = _resolve_opponent(opponent_athlete_id, opponent_name, session)
            if opp is None:
                raise HTTPException(status_code=400, detail="An opponent athlete is required")
            if opp.id == athlete_id:
                raise HTTPException(status_code=400, detail="An athlete cannot fight themselves")

            wt = win_type or None
            update_match(
                match_id,
                athlete_a_id=athlete_id,
                athlete_b_id=opp.id,
                winner_id=_winner_id(won.lower() == "true", wt, athlete_id, opp.id),
                win_type=wt,
                submission=submission or None,
                event=event or None,
                year=int(year) if year.isdigit() else None,
                weight_class=weight_class or None,
                stage=stage or None,
                video_url=video_url or None,
                sequence=_seq_with_actor_ids(sequence, athlete_id, opp.id),
                session=session,
            )
            # Rebuild this athlete, the (possibly new) opponent, and a dropped opponent.
            for aid in {athlete_id, opp.id, prev_other_id}:
                a = session.get(Athlete, aid)
                if a is not None:
                    replay_and_persist_athlete(a, session)

        return RedirectResponse(
            f"/admin/athletes/{athlete_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    # ── Draft queue: import scraped, approve, delete ────────────────────────
    @app.post("/admin/scraped/import")
    def scraped_import(request: Request) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            import_scraped_dir(session)  # drafts only — no replay (held out of graphs)
        return RedirectResponse("/admin/athletes", status_code=status.HTTP_303_SEE_OTHER)

    # ── Harvest: transcripts → prompt files → processed JSON → drafts ────────
    def _safe_in(name: str, folder: Path) -> Path | None:
        """Resolve ``name`` inside ``folder`` only — blocks path traversal."""
        p = (folder / name).resolve()
        return p if folder.resolve() in p.parents and p.is_file() else None

    @app.get("/admin/harvest", response_class=HTMLResponse)
    def harvest_page(request: Request) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        inbox = [
            {"name": p.name, "content": p.read_text(encoding="utf-8")}
            for p in sorted(HARVEST_INBOX.glob("*.harvest.md"), reverse=True)
        ] if HARVEST_INBOX.is_dir() else []
        processed = [
            p.name for p in sorted(HARVEST_PROCESSED.glob("*.json"), reverse=True)
        ] if HARVEST_PROCESSED.is_dir() else []
        with db_session() as session:
            draft_count = session.execute(
                select(func.count()).select_from(Match).where(Match.status == "draft")
            ).scalar()
        return templates.TemplateResponse(
            request, "harvest.html",
            context={"inbox": inbox, "processed": processed, "draft_count": draft_count,
                     "msg": request.query_params.get("msg")},
        )

    @app.post("/admin/harvest/run")
    def harvest_run(
        request: Request,
        urls: str = Form(""),
        playlist: str = Form(""),
        fighter: str = Form(""),
        opponent: str = Form(""),
        year: str = Form(""),
        lang: str = Form("en"),
    ) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        langs = (lang or "en",)
        # Split on newlines only — queries/URLs may contain spaces ("Name vs Name").
        url_list = [u.strip() for u in urls.replace("\r", "\n").split("\n") if u.strip()]
        results = []
        try:
            if playlist.strip():
                results += harvest_playlist(playlist.strip(), languages=langs)
            if len(url_list) == 1 and (fighter or opponent or year):
                from harvest.harvester import harvest_url
                results.append(harvest_url(
                    url_list[0], fighter=fighter or None, opponent=opponent or None,
                    year=int(year) if year.isdigit() else None, languages=langs))
            elif url_list:
                results += harvest_urls(url_list, languages=langs)
            ok = sum(1 for r in results if r.status == "ok")
            fails = [f"{r.status}: {r.input[:60]}" for r in results if r.status != "ok"]
            msg = f"Harvested {ok}/{len(results)} file(s)."
            if fails:
                msg += " Skipped — " + "; ".join(fails[:6])
        except Exception as exc:  # network/parse errors shouldn't 500 the page
            msg = f"Harvest error: {exc}"
        return RedirectResponse(f"/admin/harvest?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/harvest/processed")
    def harvest_save_processed(
        request: Request,
        processed_json: str = Form(""),
        filename: str = Form(""),
    ) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        import json
        import re
        from datetime import datetime

        text = processed_json.strip()
        try:
            obj = json.loads(text)
        except Exception:
            return RedirectResponse("/admin/harvest?msg=Invalid JSON",
                                    status_code=status.HTTP_303_SEE_OTHER)
        HARVEST_PROCESSED.mkdir(parents=True, exist_ok=True)
        base = filename.strip() or (
            f"{obj.get('fighter', 'x')}_vs_{obj.get('opponent', 'y')}_{obj.get('year', 'NA')}"
            if isinstance(obj, dict) else "processed")
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", base).strip("_")[:80] or "processed"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (HARVEST_PROCESSED / f"{ts}_{base}.json").write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return RedirectResponse("/admin/harvest?msg=Saved processed JSON",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/harvest/import")
    def harvest_import(request: Request) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            created = import_scraped_dir(session)
        return RedirectResponse(
            f"/admin/harvest?msg=Imported {len(created)} draft(s) — review on athlete pages",
            status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/harvest/delete")
    def harvest_delete(request: Request, name: str = Form(""), kind: str = Form("inbox")) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        folder = HARVEST_PROCESSED if kind == "processed" else HARVEST_INBOX
        p = _safe_in(name, folder)
        if p:
            p.unlink()
        return RedirectResponse("/admin/harvest?msg=Deleted " + name,
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/athletes/{athlete_id}/matches/{match_id}/approve")
    def approve_match_route(request: Request, athlete_id: str, match_id: str) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            match = approve_match(match_id, session)
            for aid in (match.athlete_a_id, match.athlete_b_id):
                a = session.get(Athlete, aid)
                if a is not None:
                    replay_and_persist_athlete(a, session)
        return RedirectResponse(
            f"/admin/athletes/{athlete_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    @app.post("/admin/athletes/{athlete_id}/matches/{match_id}/delete")
    def delete_match_route(request: Request, athlete_id: str, match_id: str) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            match = get_match(match_id, session)
            if match is None:
                raise HTTPException(status_code=404)
            participants = [match.athlete_a_id, match.athlete_b_id]
            was_final = match.status == "final"
            delete_match(match_id, session)
            if was_final:  # a draft never affected any graph
                for aid in participants:
                    a = session.get(Athlete, aid)
                    if a is not None:
                        replay_and_persist_athlete(a, session)
        return RedirectResponse(
            f"/admin/athletes/{athlete_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    # ── Publish athlete ────────────────────────────────────────────────────
    @app.post("/admin/athletes/{athlete_id}/publish")
    def publish(request: Request, athlete_id: str) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        with db_session() as session:
            publish_athlete(athlete_id, session)
        return RedirectResponse(
            f"/admin/athletes/{athlete_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    # ── Recompute archetypes ───────────────────────────────────────────────
    @app.post("/admin/archetypes/recompute")
    def recompute_archetypes(request: Request, k: int = Form(6)) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        from analysis.archetype import run_archetype_pipeline

        with db_session() as session:
            run_archetype_pipeline(session, k=k)
        return RedirectResponse("/admin/athletes", status_code=status.HTTP_303_SEE_OTHER)

    # ── Analytics overview ─────────────────────────────────────────────────
    @app.get("/admin/analytics", response_class=HTMLResponse)
    def analytics(request: Request) -> Any:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        # Type distribution over the shared technique library, aggregated in SQL
        # (the library grows with every synced technique — don't materialize it all).
        with db_session() as session:
            rows = session.execute(
                select(TechniqueNode.node_type, func.count())
                .group_by(TechniqueNode.node_type)
            ).all()
        type_counts = {(nt or "unknown"): int(cnt) for nt, cnt in rows}
        total_nodes = sum(type_counts.values())
        return templates.TemplateResponse(
            request,
            "analytics.html",
            context={"type_counts": type_counts, "total_nodes": total_nodes},
        )

    # ── Ontology authoring (RF04-06, RF20, DS-01/04) ────────────────────────
    def _guard(request: Request) -> RedirectResponse | None:
        if not is_authenticated(request):
            return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
        return None

    @app.get("/admin/ontology", response_class=HTMLResponse)
    def ontology_home(request: Request) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            systems = list(session.execute(select(System).order_by(System.name)).scalars())
            sys_rows = []
            for s in systems:
                n_ms = session.execute(
                    select(func.count()).select_from(Milestone).where(Milestone.system_id == s.id)
                ).scalar_one()
                n_impl = session.execute(
                    select(func.count()).select_from(SystemImplementation)
                    .where(SystemImplementation.system_id == s.id)
                ).scalar_one()
                sys_rows.append({"id": s.id, "key": s.key, "name": s.name,
                                 "objective": s.objective, "milestones": n_ms, "impls": n_impl})
            principles = list(session.execute(select(Principle).order_by(Principle.name)).scalars())
            dilemmas = list(session.execute(select(Dilemma).order_by(Dilemma.name)).scalars())
            ds_positions = list(session.execute(
                select(TechniqueNode.node_key, TechniqueNode.decision_space)
                .where(TechniqueNode.decision_space.isnot(None)).order_by(TechniqueNode.node_key)
            ).all())
            archetypes = list(session.execute(
                select(Archetype).order_by(Archetype.kind, Archetype.name)
            ).scalars())
            arch_rows = [{"id": a.id, "name": a.name, "kind": a.kind,
                          "description": a.description,
                          "signature_types": a.signature_types or []} for a in archetypes]
        return templates.TemplateResponse(request, "ontology.html", context={
            "systems": sys_rows, "principles": principles, "dilemmas": dilemmas,
            "ds_positions": [{"node_key": k, "ds": v} for k, v in ds_positions],
            "archetypes": arch_rows,
        })

    @app.post("/admin/ontology/archetypes")
    def create_target_archetype(request: Request, name: str = Form(...),
                                description: str = Form(""),
                                signature_types: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        sig = [t.strip() for t in signature_types.replace(",", " ").split() if t.strip()]
        with db_session() as session:
            upsert_target_archetype(name=name, description=description or None,
                                    signature_types=sig, session=session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/archetypes/{archetype_id}/delete")
    def remove_target_archetype(request: Request, archetype_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            delete_archetype(archetype_id, session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/principles")
    def create_principle(request: Request, name: str = Form(...), type: str = Form(""),
                         description: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            upsert_principle(key=slugify(name), name=name, type=type or None,
                             description=description or None, session=session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/principles/{principle_id}/delete")
    def remove_principle(request: Request, principle_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            delete_principle(principle_id, session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/dilemmas")
    def create_dilemma(request: Request, name: str = Form(...), situation: str = Form(""),
                      option_a: str = Form(""), option_b: str = Form(""),
                      principle_keys: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            upsert_dilemma(key=slugify(name), name=name, situation=situation or None,
                           option_a=option_a or None, option_b=option_b or None,
                           principle_keys=_lines(principle_keys), session=session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/dilemmas/{dilemma_id}/delete")
    def remove_dilemma(request: Request, dilemma_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            delete_dilemma(dilemma_id, session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/systems")
    def create_system(request: Request, name: str = Form(...), objective: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            sid = upsert_system(
                key=slugify(name), name=name, objective=objective or None,
                entry_positions=[], activation_conditions=[], expected_opponent_responses=[],
                alternative_paths=[], mastery_criteria=[], ds_mode="expert", session=session)
        return RedirectResponse(f"/admin/ontology/systems/{sid}",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/admin/ontology/systems/{system_id}", response_class=HTMLResponse)
    def edit_system(request: Request, system_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            system = session.get(System, system_id)
            if system is None:
                raise HTTPException(status_code=404, detail="System not found")
            milestones = list(session.execute(
                select(Milestone).where(Milestone.system_id == system_id)
                .order_by(Milestone.ordinal)).scalars())
            all_principles = list(
                session.execute(select(Principle).order_by(Principle.name)).scalars())
            all_dilemmas = list(
                session.execute(select(Dilemma).order_by(Dilemma.name)).scalars())
            attached_p = {row[0] for row in session.execute(
                select(SystemPrinciple.principle_id)
                .where(SystemPrinciple.system_id == system_id)).all()}
            attached_d = {row[0] for row in session.execute(
                select(SystemDilemma.dilemma_id)
                .where(SystemDilemma.system_id == system_id)).all()}
            impls = list(session.execute(
                select(SystemImplementation, Athlete.name)
                .join(Athlete, Athlete.id == SystemImplementation.athlete_id)
                .where(SystemImplementation.system_id == system_id)).all())
            impl_rows = [{"id": im.id, "name": im.name, "athlete": nm,
                          "notes": (im.overrides or {}).get("notes", "")} for im, nm in impls]
            athletes = list(session.execute(
                select(Athlete).order_by(Athlete.name)).scalars())
            ctx = {
                "system": system,
                "entry_positions": "\n".join(system.entry_positions or []),
                "activation_conditions": "\n".join(system.activation_conditions or []),
                "expected_opponent_responses": "\n".join(system.expected_opponent_responses or []),
                "alternative_paths": "\n".join(system.alternative_paths or []),
                "mastery_criteria": "\n".join(system.mastery_criteria or []),
                "milestones": milestones,
                "principles": [{"obj": p, "on": p.id in attached_p} for p in all_principles],
                "dilemmas": [{"obj": d, "on": d.id in attached_d} for d in all_dilemmas],
                "implementations": impl_rows,
                "athletes": athletes,
                "milestone_kinds": ["conceptual", "execution", "dilemma", "chaining",
                                    "resistance", "recovery"],
            }
        return templates.TemplateResponse(request, "edit_system.html", context=ctx)

    @app.post("/admin/ontology/systems/{system_id}")
    def update_system(
        request: Request, system_id: str, name: str = Form(...), objective: str = Form(""),
        entry_positions: str = Form(""), activation_conditions: str = Form(""),
        expected_opponent_responses: str = Form(""), alternative_paths: str = Form(""),
        mastery_criteria: str = Form(""), ds_mode: str = Form("expert"),
        principle_ids: list[str] = Form(default=[]),
        dilemma_ids: list[str] = Form(default=[]),
    ) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            system = session.get(System, system_id)
            if system is None:
                raise HTTPException(status_code=404, detail="System not found")
            system.name = name
            system.objective = objective or None
            system.entry_positions = _lines(entry_positions)
            system.activation_conditions = _lines(activation_conditions)
            system.expected_opponent_responses = _lines(expected_opponent_responses)
            system.alternative_paths = _lines(alternative_paths)
            system.mastery_criteria = _lines(mastery_criteria)
            system.ds_mode = ds_mode or "expert"
            set_system_principles(system_id, principle_ids, session)
            set_system_dilemmas(system_id, dilemma_ids, session)
        return RedirectResponse(f"/admin/ontology/systems/{system_id}",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/systems/{system_id}/delete")
    def remove_system(request: Request, system_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            delete_system(system_id, session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/systems/{system_id}/milestones")
    def create_milestone(request: Request, system_id: str, kind: str = Form(...),
                        ordinal: int = Form(0), name: str = Form(...),
                        description: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            add_milestone(system_id=system_id, ordinal=ordinal, kind=kind, name=name,
                          description=description or None, session=session)
        return RedirectResponse(f"/admin/ontology/systems/{system_id}",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/systems/{system_id}/milestones/{milestone_id}/delete")
    def remove_milestone(request: Request, system_id: str, milestone_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            delete_milestone(milestone_id, session)
        return RedirectResponse(f"/admin/ontology/systems/{system_id}",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/systems/{system_id}/implementations")
    def create_implementation(request: Request, system_id: str, athlete_id: str = Form(...),
                            name: str = Form(""), notes: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            upsert_implementation(system_id=system_id, athlete_id=athlete_id,
                                  name=name or None, notes=notes, session=session)
        return RedirectResponse(f"/admin/ontology/systems/{system_id}",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/systems/{system_id}/implementations/{impl_id}/delete")
    def remove_implementation(request: Request, system_id: str, impl_id: str) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            delete_implementation(impl_id, session)
        return RedirectResponse(f"/admin/ontology/systems/{system_id}",
                                status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/positions")
    def set_position_ds(request: Request, node_key: str = Form(...),
                      attacker_score: float = Form(...), defender_score: float = Form(...),
                      expected_reactions: str = Form(""), constraints: str = Form("")) -> Any:
        if (r := _guard(request)) is not None:
            return r
        with db_session() as session:
            set_position_decision_space(
                node_key=node_key.strip().lower(), attacker_score=attacker_score,
                defender_score=defender_score, expected_reactions=_lines(expected_reactions),
                constraints=_lines(constraints), session=session)
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/admin/ontology/export")
    def export_seed(request: Request) -> Any:
        if (r := _guard(request)) is not None:
            return r
        from export.ontology import export_ontology_seed

        export_ontology_seed()
        return RedirectResponse("/admin/ontology", status_code=status.HTTP_303_SEE_OTHER)

    return app


def _build_node_options() -> list[dict[str, str]]:
    try:
        raw = load_app_nodes()
        seen: set[str] = set()
        out = []
        for n in raw:
            name = str(n.get("name", "")).strip()
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "type": str(n.get("type", ""))})
        return out
    except Exception:
        return []


app = create_admin_app()
