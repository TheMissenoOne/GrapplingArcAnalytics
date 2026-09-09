"""``class_guests`` / ``attach_to_class_guest`` (alembic 0060) — source-scan.

Same reason as ``test_session_video_analysis.py``: this suite runs against SQLite in-memory
and never executes a Postgres migration, so RLS/RPC/grant shape is checked by reading the
migration text, not by running it.

What these tests are actually guarding, in one line each:
  - the guest write path is the RPC and nothing else (no INSERT policy, no INSERT grant);
  - the guest READ scope is one class session and nothing else;
  - the member path through ``group_member_sessions()`` did not drift;
  - no other professor projection was widened;
  - ``downgrade()`` undoes everything ``upgrade()`` did.
"""

from __future__ import annotations

from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "0060_attach_to_class_guest.py"

NEW_FUNCTIONS = (
    "public.is_class_session_staff(uuid)",
    "public.can_read_member_row(uuid, uuid)",
    "public.attach_to_class_guest(text)",
    "public.class_session_guests(uuid)",
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code() -> str:
    """Source with the module docstring stripped — so prose naming a function or a policy
    doesn't false-positive a check for that name in the actual SQL/DDL."""
    src = _source()
    first = src.index('"""')
    second = src.index('"""', first + 3)
    return src[second + 3 :]


def _upgrade() -> str:
    code = _code()
    return code[code.index("def upgrade()") : code.index("def downgrade()")]


def _downgrade() -> str:
    code = _code()
    return code[code.index("def downgrade()") :]


def test_revision_chain() -> None:
    src = _source()
    assert 'revision = "0060"' in src
    assert 'down_revision = "0059"' in src


# ── the write path is the RPC, and only the RPC ───────────────────────────────────────────


def test_every_create_policy_has_a_drop_if_exists_first() -> None:
    src = _source()
    policy = "class_guests_select_self_or_staff"
    assert f"drop policy if exists {policy} on public.class_guests" in src
    assert f"create policy {policy} on public.class_guests" in src


def test_no_insert_update_or_delete_policy_on_class_guests() -> None:
    """The only writer is attach_to_class_guest (SECURITY DEFINER). A `for insert` policy
    would open a client write path that bypasses the consent timestamp entirely."""
    code = _code().lower()
    for bad in ("for insert", "for update", "for delete", "for all"):
        assert bad not in code, f"class_guests must have no `{bad}` policy"


def test_class_guests_grants_are_select_only() -> None:
    code = _code()
    assert "revoke all on public.class_guests from anon, authenticated;" in code
    assert "grant select on public.class_guests to authenticated;" in code
    for verb in ("grant insert", "grant update", "grant delete", "grant all"):
        assert verb not in code.lower()


def test_rls_enabled_on_class_guests() -> None:
    assert "alter table public.class_guests enable row level security;" in _code()


def test_class_guests_read_policy_is_self_or_class_staff() -> None:
    code = _code()
    start = code.index("create policy class_guests_select_self_or_staff")
    body = code[start : start + 300]
    assert "for select" in body
    assert "profile_id = auth.uid()" in body
    assert "public.is_class_session_staff(class_session_id)" in body
    # 0025's rule: a policy never queries an RLS-protected table itself.
    assert "group_members" not in body
    assert "class_sessions" not in body


# ── the RPC itself ────────────────────────────────────────────────────────────────────────


def test_attach_to_class_guest_shape() -> None:
    code = _code()
    assert "create or replace function public.attach_to_class_guest(p_token text)" in code
    start = code.index("create or replace function public.attach_to_class_guest")
    body = code[start : code.index('"""', start)]
    # Same return shape as attach_to_class (0026) — the App reuses its handler.
    assert "returns table (class_id uuid, class_title text)" in body
    assert "security definer" in body
    assert "set search_path = public" in body


def test_attach_to_class_guest_error_codes() -> None:
    """The three codes the App maps verbatim. Order matters: a bad token must never be
    reported as `already_member` (that would leak group membership for a guessed token)."""
    code = _code()
    start = code.index("create or replace function public.attach_to_class_guest")
    body = code[start : code.index('"""', start)]
    for err in ("bad_token", "class_closed", "already_member"):
        assert f"raise exception '{err}' using errcode = 'P0001';" in body
    assert body.index("'bad_token'") < body.index("'class_closed'") < body.index("'already_member'")


def test_attach_to_class_guest_refuses_members_and_expired_tokens() -> None:
    code = _code()
    start = code.index("create or replace function public.attach_to_class_guest")
    body = code[start : code.index('"""', start)]
    assert "public.is_group_member(target_class.group_id)" in body
    assert "target_class.token_expires_at <= now()" in body


def test_attach_to_class_guest_records_consent_and_is_idempotent() -> None:
    code = _code()
    start = code.index("create or replace function public.attach_to_class_guest")
    body = code[start : code.index('"""', start)]
    assert "insert into public.class_guests (class_session_id, profile_id, consent_at)" in body
    assert "values (target_class.id, auth.uid(), now())" in body
    # Re-scan keeps the FIRST consent — `do update` would silently rewrite it.
    assert "on conflict (class_session_id, profile_id) do nothing" in body


def test_attach_to_class_0026_is_not_touched() -> None:
    """The member path must be byte-identical to yesterday: this revision does not redefine
    attach_to_class, so its single `invalid_or_not_member` error can't regress."""
    code = _code()
    assert "create or replace function public.attach_to_class(" not in code
    assert "drop function if exists public.attach_to_class(text)" not in code


# ── the read scope: one class session, nothing else ───────────────────────────────────────


def test_can_read_member_row_keeps_the_member_predicate_first() -> None:
    """First disjunct is exactly 0044's predicate applied to the same column — if this drifts,
    every professor silently loses (or gains) sight of their own students."""
    code = _code()
    start = code.index("create or replace function public.can_read_member_row")
    body = code[start : code.index('"""', start)]
    assert "select shares_group_as_professor(p_profile)" in body


def test_can_read_member_row_guest_branch_needs_staff_and_that_exact_class() -> None:
    code = _code()
    start = code.index("create or replace function public.can_read_member_row")
    body = code[start : code.index('"""', start)]
    assert "is_class_session_staff(p_class_session)" in body
    assert "from public.class_guests cg" in body
    assert "where cg.class_session_id = p_class_session" in body
    assert "and cg.profile_id = p_profile" in body


def test_is_class_session_staff_is_group_scoped_to_that_class() -> None:
    code = _code()
    start = code.index("create or replace function public.is_class_session_staff")
    body = code[start : code.index('"""', start)]
    assert "from public.class_sessions cs" in body
    assert "where cs.id = p_class_session" in body
    assert "is_group_owner_or_professor(cs.group_id)" in body


def test_group_member_sessions_member_path_and_projection_unchanged() -> None:
    """Same signature, same projection, same 0044 deleted_at filter — only the predicate
    moved behind the helper. Compared against 0044's own source so a drift in either file
    fails here."""
    code = _code()
    up_start = code.index("_SESSIONS_FN_UP")
    up = code[up_start : code.index("# 0044's body", up_start)]

    assert "create or replace function public.group_member_sessions()" in up
    assert "can_read_member_row(us.owner_id, us.class_session_id)" in up
    assert "and us.deleted_at is null" in up
    # The notes/reflection stripping and the column list are lifted verbatim from 0044.
    for fragment in (
        "id text,",
        "owner_id uuid,",
        "class_session_id uuid,",
        "updated_at timestamptz,",
        "data jsonb",
        "us.data - 'reflection'",
        "jsonb_agg(r.value - 'notes')",
    ):
        assert fragment in up, fragment


def test_downgrade_restores_0044_body_verbatim() -> None:
    """The restored body must match 0044's `_FN_UP` character for character (modulo
    whitespace), or a rollback leaves a function nobody wrote."""
    mine = _code()
    start = mine.index("_SESSIONS_FN_DOWN = ")
    restored = mine[mine.index('"""', start) + 3 : mine.index('"""', mine.index('"""', start) + 3)]

    prior = (VERSIONS / "0044_group_member_sessions_hides_deleted.py").read_text(encoding="utf-8")
    p_start = prior.index("_FN_UP = ")
    original = prior[
        prior.index('"""', p_start) + 3 : prior.index('"""', prior.index('"""', p_start) + 3)
    ]

    assert restored.split() == original.split()


def test_no_other_professor_rpc_is_redefined() -> None:
    """"nada do histórico": the other five projections are group_members-shaped and already
    return nothing for a guest. Redefining any of them here would be a widening nobody asked
    for — this test is the tripwire."""
    code = _code()
    for fn in (
        "group_member_names",
        "group_member_rating",
        "group_member_graph_edges",
        "group_member_video_analysis",
        "group_member_athlete",
    ):
        assert f"create or replace function public.{fn}" not in code
        assert f"drop function if exists public.{fn}" not in code


def test_class_session_guests_is_staff_gated_and_narrow() -> None:
    code = _code()
    start = code.index("create or replace function public.class_session_guests")
    body = code[start : code.index('"""', start)]
    assert "is_class_session_staff(p_class_session_id)" in body
    assert "where cg.class_session_id = p_class_session_id" in body
    # A guest opted into one class, not into a profile projection: name + consent only.
    for absent in ("belt_rank", "belt_degrees", "user_elo", "athlete_id", "is_pro"):
        assert absent not in body


# ── grants, definer discipline, downgrade completeness ────────────────────────────────────


def test_every_new_function_is_definer_with_pinned_search_path() -> None:
    code = _code()
    for fn in (
        "is_class_session_staff",
        "can_read_member_row",
        "attach_to_class_guest",
        "class_session_guests",
    ):
        start = code.index(f"create or replace function public.{fn}")
        body = code[start : code.index('"""', start)]
        assert "security definer" in body, fn
        assert "search_path" in body, fn


def test_every_new_function_revokes_anon_and_grants_authenticated() -> None:
    """0028's lesson: revoking from PUBLIC alone does not remove Supabase's default anon
    grant. The loop in upgrade() covers all four — assert the tuple it loops over."""
    code = _code()
    for fn in NEW_FUNCTIONS:
        assert f'"{fn}"' in code
    up = _upgrade()
    assert "revoke all on function {fn} from public;" in up
    assert "revoke all on function {fn} from anon;" in up
    assert "grant execute on function {fn} to authenticated;" in up


def test_downgrade_drops_everything_upgrade_created() -> None:
    down = _downgrade()
    for fn in NEW_FUNCTIONS:
        assert f"drop function if exists {fn};" in down
    assert "drop policy if exists class_guests_select_self_or_staff on public.class_guests;" in down
    assert 'op.drop_table("class_guests")' in down
    assert "_SESSIONS_FN_DOWN" in down


def test_upgrade_creates_the_table_the_downgrade_drops() -> None:
    up = _upgrade()
    assert 'op.create_table(\n        "class_guests"' in up
    # consent_at is the whole point of the row — a nullable one would mean "guest with no
    # recorded consent", which the owner's decision does not allow to exist.
    assert '"consent_at", sa.DateTime(timezone=True), nullable=False' in up
    assert 'sa.ForeignKey("class_sessions.id", ondelete="CASCADE")' in up
    assert 'sa.ForeignKey("profiles.id", ondelete="CASCADE")' in up


def test_models_mirror_has_class_guest() -> None:
    """§2 of the supabase-schema-migration skill: models.py and the migration land together."""
    models = (Path(__file__).resolve().parents[1] / "db" / "models.py").read_text(encoding="utf-8")
    assert "class ClassGuest(Base):" in models
    assert '__tablename__ = "class_guests"' in models
