"""Admin FastAPI app — pro-athlete data entry and analytics dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from admin.auth import (
    _COOKIE_NAME,
    check_password,
    create_session,
    destroy_session,
    is_authenticated,
)
from analysis.athlete_graph import build_athlete_graph
from analysis.elo_calibration import compute_adcc_elo
from cv.vocab_map import load_app_nodes
from db.base import db_session
from db.models import Athlete, AthleteMatch, Graph, GraphNode
from db.repository import (
    get_athlete_matches,
    publish_athlete,
    register_match,
    upsert_athlete,
    upsert_graph_from_athlete_graph,
)
from export.athlete_graph_export import athlete_graph_to_app_json

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_admin_app() -> FastAPI:
    app = FastAPI(title="GrapplingArc Admin", docs_url=None, redoc_url=None)

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
            athletes = list(session.execute(select(Athlete)).scalars())
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
        return templates.TemplateResponse(
            request,
            "athlete_detail.html",
            context={
                "athlete": athlete,
                "matches": matches,
                "graph": graph_json,
                "node_options": app.state.node_options,
            },
        )

    # ── Register match ─────────────────────────────────────────────────────
    @app.post("/admin/athletes/{athlete_id}/matches", response_class=HTMLResponse)
    def add_match(
        request: Request,
        athlete_id: str,
        opponent_name: str = Form(""),
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

        with db_session() as session:
            athlete = session.get(Athlete, athlete_id)
            if athlete is None:
                raise HTTPException(status_code=404)

            register_match(
                athlete_id=athlete_id,
                opponent_name=opponent_name or None,
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

            # Rebuild athlete graph from all matches
            all_matches = get_athlete_matches(athlete_id, session)
            sessions_payload = [
                {"topics": [], "rounds": [{"entries": m.sequence or []}]}
                for m in all_matches
                if m.won  # only winning moves build the graph
            ]
            graph = build_athlete_graph(athlete.name, sessions_payload)
            upsert_graph_from_athlete_graph(graph, athlete_id, session)

            # Recompute athlete ELO from match history
            _recompute_athlete_elo(athlete_id, session)

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


def _recompute_athlete_elo(athlete_id: str, session: Any) -> None:
    """Recompute and store ELO for an athlete from their match history."""

    matches = list(
        session.execute(select(AthleteMatch).where(AthleteMatch.athlete_id == athlete_id)).scalars()
    )
    if not matches:
        return
    rows = [
        {
            "winner": "athlete" if m.won else "opponent",
            "loser": "opponent" if m.won else "athlete",
            "win_type": m.win_type or "POINTS",
            "stage": m.stage or "R1",
        }
        for m in matches
    ]
    df = pd.DataFrame(rows)
    elo_df = compute_adcc_elo(df)
    athlete_elo = elo_df[elo_df["fighter"] == "athlete"]["elo"].values
    if len(athlete_elo) > 0:
        athlete = session.get(Athlete, athlete_id)
        if athlete:
            athlete.elo = float(athlete_elo[0])


app = create_admin_app()
