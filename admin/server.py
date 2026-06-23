"""Admin FastAPI app — pro-athlete data entry and analytics dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from admin.auth import (
    _COOKIE_NAME,
    check_password,
    create_session,
    destroy_session,
    is_authenticated,
)
from analysis.athlete_elo import replay_matches
from cv.vocab_map import load_app_nodes
from db.base import db_session
from db.models import Athlete, Graph, GraphNode
from db.repository import (
    get_athlete_matches,
    publish_athlete,
    rank_elo_for_athlete,
    register_match,
    seed_athletes_from_leaderboard,
    upsert_athlete,
    upsert_graph_from_athlete_graph,
)
from export.athlete_graph_export import athlete_graph_to_app_json

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
            matches = get_athlete_matches(athlete_id, session)
            graph = session.execute(
                select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete_id)
            ).scalar_one_or_none()
            graph_json = athlete_graph_to_app_json(graph.id, session) if graph else None
            # Ranked opponents to impersonate against (everyone except this athlete).
            ranked = list(
                session.execute(
                    select(Athlete)
                    .where(Athlete.rank_elo.isnot(None), Athlete.id != athlete_id)
                    .order_by(Athlete.rank_elo.desc())
                ).scalars()
            )
            opponent_options = [
                {"id": a.id, "name": a.name, "rank_elo": a.rank_elo} for a in ranked
            ]
            # Convergence series (chronological), for the sparkline.
            series = [
                m.graph_elo_after
                for m in sorted(matches, key=lambda m: (m.year or 0, m.created_at))
                if m.graph_elo_after is not None
            ]
        return templates.TemplateResponse(
            request,
            "athlete_detail.html",
            context={
                "athlete": athlete,
                "matches": matches,
                "graph": graph_json,
                "node_options": app.state.node_options,
                "opponent_options": opponent_options,
                "graph_elo_series": series,
            },
        )

    # ── Register match ─────────────────────────────────────────────────────
    @app.post("/admin/athletes/{athlete_id}/matches", response_class=HTMLResponse)
    def add_match(
        request: Request,
        athlete_id: str,
        opponent_name: str = Form(""),
        opponent_athlete_id: str = Form(""),
        opponent_elo: str = Form(""),
        event: str = Form(""),
        year: str = Form(""),
        weight_class: str = Form(""),
        win_type: str = Form(""),
        stage: str = Form(""),
        submission: str = Form(""),
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

        try:
            manual_opp_elo = float(opponent_elo) if opponent_elo.strip() else None
        except ValueError:
            manual_opp_elo = None

        with db_session() as session:
            athlete = session.get(Athlete, athlete_id)
            if athlete is None:
                raise HTTPException(status_code=404)

            register_match(
                athlete_id=athlete_id,
                opponent_name=opponent_name or None,
                opponent_athlete_id=opponent_athlete_id or None,
                opponent_elo=manual_opp_elo,
                event=event or None,
                year=int(year) if year.isdigit() else None,
                weight_class=weight_class or None,
                win_type=win_type or None,
                stage=stage or None,
                submission=submission or None,
                won=won.lower() == "true",
                sequence=sequence,
                created_by=None,
                session=session,
            )

            # Replay the full match history chronologically to grow the graph ELO
            # from the belt floor toward the athlete's rank ELO target.
            target = rank_elo_for_athlete(athlete.name)
            if target is None:
                target = athlete.rank_elo if athlete.rank_elo is not None else 1000.0
            all_matches = sorted(
                get_athlete_matches(athlete_id, session),
                key=lambda m: (m.year or 0, m.created_at),
            )
            opp_elos = [
                _resolve_opp_elo(m, target, session) for m in all_matches
            ]
            graph, snapshots = replay_matches(
                athlete.name,
                all_matches,
                target,
                opp_elos,
                belt=athlete.belt or "black",
            )
            upsert_graph_from_athlete_graph(graph, athlete_id, session)
            if graph.user_elo is not None:
                athlete.elo = graph.user_elo
            for m, snap in zip(all_matches, snapshots):
                m.graph_elo_after = snap

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
        with db_session() as session:
            node_rows = list(session.execute(select(GraphNode)).scalars())
        type_counts: dict[str, int] = {}
        for n in node_rows:
            t = n.node_type or "unknown"
            type_counts[t] = type_counts.get(t, 0) + 1
        return templates.TemplateResponse(
            request,
            "analytics.html",
            context={"type_counts": type_counts, "total_nodes": len(node_rows)},
        )

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


def _resolve_opp_elo(match: Any, target: float, session: Any) -> float:
    """Opponent rating for a match: ranked opponent's rank_elo → manual → target."""
    if match.opponent_athlete_id:
        opp = session.get(Athlete, match.opponent_athlete_id)
        if opp is not None and opp.rank_elo is not None:
            return float(opp.rank_elo)
    if match.opponent_elo is not None:
        return float(match.opponent_elo)
    return target


app = create_admin_app()
