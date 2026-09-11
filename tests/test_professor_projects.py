"""Professor projects / focus specs / objectives (alembic 0062) — source-scan.

Same reason as ``test_attach_to_class_guest.py``: this suite runs against SQLite in-memory and
never executes a Postgres migration, so RLS/RPC/grant/trigger shape is checked by reading the
migration text, not by running it.

What these tests guard, one line each:
  - the write path is the RPCs and nothing else (no INSERT/UPDATE/DELETE policy, no such grant);
  - every RPC answers "am I staff here" inside its own body, before it writes or reads;
  - the evidence RPC never projects a student's free text, and reads through the same predicate
    ``group_member_sessions()`` does;
  - ``user_sessions.class_session_id`` became write-once without breaking 0019's stale-write skip;
  - ``downgrade()`` undoes everything ``upgrade()`` did.
"""

from __future__ import annotations

from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "0062_professor_projects.py"

TABLES = (
    "professor_projects",
    "professor_project_classes",
    "class_focus_specs",
    "class_objectives",
)

NEW_FUNCTIONS = (
    "public.normalize_node_key(text)",
    "public.is_project_staff(uuid)",
    "public.professor_project_upsert(uuid, text, uuid, text, timestamptz)",
    "public.professor_project_set_status(uuid, text)",
    "public.professor_project_attach_class(uuid, uuid, integer)",
    "public.class_focus_spec_upsert(uuid, text, text[], jsonb)",
    "public.professor_project_list(uuid)",
    "public.professor_project_evidence(uuid)",
)

# Every RPC a client calls, and the access question its body must ask before doing anything.
RPC_GUARDS = {
    "professor_project_upsert": ("is_group_owner_or_professor",),
    "professor_project_set_status": ("is_project_staff",),
    "professor_project_attach_class": ("is_class_session_staff", "is_group_owner_or_professor"),
    "class_focus_spec_upsert": ("is_class_session_staff",),
    "class_objective_upsert": ("is_class_session_staff",),
    "professor_project_list": ("is_group_owner_or_professor",),
    "professor_project_evidence": ("is_group_owner_or_professor", "can_read_member_row"),
}

# Free text a student wrote. None of it may appear anywhere in the SQL this revision adds —
# not in a projection, not in a filter. Same tripwire shape as 0060's narrowness test.
PRIVATE_KEYS = ("reflection", "notes", "videoContext", "archetype_report", "rating_note")


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


def _fn_body(name: str) -> str:
    code = _code()
    start = code.index(f"create or replace function public.{name}")
    return code[start : code.index('"""', start)]


def test_revision_chain() -> None:
    src = _source()
    assert 'revision = "0062"' in src
    assert 'down_revision = "0061"' in src


# ── the write path is the RPCs, and only the RPCs ─────────────────────────────────────────


def test_no_insert_update_or_delete_policy_on_any_new_table() -> None:
    """A client write policy would be a second door onto rows whose invariants (one project per
    class, the focus_node_keys projection, kind/target agreement) live inside the RPCs."""
    code = _code().lower()
    for stmt in code.split("create policy")[1:]:
        head = stmt[: stmt.index("using")]
        assert "for select" in head, head
    for bad in ("for insert", "for update", "for delete", "for all to"):
        assert f"create policy {bad}" not in code
        assert f"_select_staff on public.{{table}} {bad}" not in code


def test_grants_are_select_only() -> None:
    code = _code()
    assert "revoke all on public.{table} from anon, authenticated;" in code
    assert "grant select on public.{table} to authenticated;" in code
    for verb in ("grant insert", "grant update", "grant delete", "grant all"):
        assert verb not in code.lower()


def test_rls_enabled_on_every_new_table() -> None:
    code = _code()
    assert "alter table public.{table} enable row level security;" in code
    for table in TABLES:
        assert f'"{table}"' in code, table


def test_every_create_policy_has_a_drop_if_exists_first() -> None:
    code = _code()
    drop = code.index("drop policy if exists {table}_select_staff on public.{table};")
    create = code.index("create policy {table}_select_staff on public.{table} for select")
    assert drop < create


def test_select_policies_go_through_definer_helpers_only() -> None:
    """0025's rule: a policy never queries an RLS-protected table itself. The predicate tuple
    is the whole policy body, so asserting it is asserting every policy."""
    code = _code()
    start = code.index("_TABLES = (")
    block = code[start : code.index(")\n", code.index("class_objectives", start))]
    assert "public.is_group_owner_or_professor(group_id)" in block
    assert "public.is_project_staff(project_id)" in block
    assert "public.is_class_session_staff(class_session_id)" in block
    for protected in ("select 1", "from public.group_members", "from public.class_sessions"):
        assert protected not in block, protected


# ── every RPC validates membership inside its own body ────────────────────────────────────


def test_every_rpc_asks_the_access_question_in_its_own_body() -> None:
    for fn, guards in RPC_GUARDS.items():
        body = _fn_body(fn)
        for guard in guards:
            assert guard in body, f"{fn} must gate on {guard}"


def test_every_new_function_is_definer_with_pinned_search_path() -> None:
    """normalize_node_key is the one exception and must stay the exception: it touches no table,
    so it is a plain immutable function — SECURITY DEFINER on it would be privilege for nothing."""
    for fn in RPC_GUARDS:
        body = _fn_body(fn)
        assert "security definer" in body, fn
        assert "search_path" in body, fn

    helper = _fn_body("is_project_staff")
    assert "security definer" in helper
    assert "set search_path to 'public'" in helper

    pure = _fn_body("normalize_node_key")
    assert "security definer" not in pure
    assert "immutable" in pure and "strict" in pure


def test_every_new_function_revokes_anon_and_grants_authenticated() -> None:
    """0028's lesson: revoking from PUBLIC alone does not remove Supabase's default anon
    grant. The loop in upgrade() covers all of them — assert the tuple it loops over."""
    code = _code()
    for fn in NEW_FUNCTIONS:
        assert f'"{fn}"' in code, fn
    assert "class_objective_upsert(" in code
    up = _upgrade()
    assert "revoke all on function {fn} from public;" in up
    assert "revoke all on function {fn} from anon;" in up
    assert "grant execute on function {fn} to authenticated;" in up


def test_error_codes_are_the_contract() -> None:
    """message == code (0060's convention) — the Web maps these strings verbatim."""
    code = _code()
    for err in (
        "empty_name",
        "project_not_found",
        "not_group_staff",
        "invalid_status",
        "not_class_staff",
        "project_not_active",
        "group_mismatch",
        "invalid_kind",
        "empty_focus",
        "invalid_target",
        "invalid_window",
        "objective_not_found",
    ):
        assert f"raise exception '{err}' using errcode = 'P0001';" in code, err


# ── the focus projection ──────────────────────────────────────────────────────────────────


def test_focus_spec_upsert_writes_the_compatibility_projection() -> None:
    """class_sessions.focus_node_keys (0050) is still what the students' own policy and every
    existing Web surface read. The RPC writes both halves or they drift."""
    body = _fn_body("class_focus_spec_upsert")
    assert "insert into public.class_focus_specs" in body
    assert "update public.class_sessions" in body
    assert "set focus_node_keys = v_keys" in body
    assert "on conflict (class_session_id) do update" in body


def test_focus_and_objective_keys_are_normalised_through_one_function() -> None:
    for fn in ("class_focus_spec_upsert", "class_objective_upsert"):
        assert "public.normalize_node_key(" in _fn_body(fn), fn


def test_normalize_node_key_matches_normalizelabel_char_for_char() -> None:
    """Fourth port of the App's normalizeLabel (lower → strip [^a-z0-9 ] → collapse → trim).
    `collate "C"` is load-bearing: under en_US.UTF-8 the range [a-z] is locale-ordered and
    would KEEP accented letters the JS/Python sides strip."""
    body = _fn_body("normalize_node_key")
    assert 'lower(p_label) collate "C"' in body
    assert "'[^a-z0-9 ]', '', 'g'" in body
    assert "' +', ' ', 'g'" in body
    assert "btrim(" in body


# ── the evidence read: counts and dates, nothing a student wrote ──────────────────────────


def test_evidence_never_projects_private_session_text() -> None:
    """The professor gets numbers. Reflections, round notes and video context are the student's
    own words and have no professor-facing path — here or anywhere (root CLAUDE.md)."""
    code = _code().lower()
    for key in PRIVATE_KEYS:
        assert key.lower() not in code, f"0062 must never mention `{key}`"


def test_evidence_returns_only_ids_dates_and_counts() -> None:
    body = _fn_body("professor_project_evidence")
    returns = body[body.index("returns table (") : body.index("language sql")]
    # node_key is the only text column, and it is the professor's OWN focus key echoed back.
    assert returns.count(" text,") + returns.count(" text\n") == 1
    assert "node_key text," in returns
    for column in (
        "attempts integer,",
        "successful_attempts integer,",
        "sessions_with_attempt integer,",
        "first_attempt_at timestamptz,",
        "last_attempt_at timestamptz,",
        "baseline_attempts integer,",
        "baseline_sessions_with_attempt integer,",
        "sessions_in_window integer",
    ):
        assert column in returns, column


def test_evidence_reads_sessions_through_the_same_predicate_as_group_member_sessions() -> None:
    body = _fn_body("professor_project_evidence")
    assert "can_read_member_row(us.owner_id, us.class_session_id)" in body
    # 0044's lesson: a withdrawn session must not come back through a new read.
    assert "us.deleted_at is null" in body


def test_exposed_set_is_members_of_the_projects_own_group() -> None:
    """A drop-in guest (0060) is deliberately out of BOTH numerator and denominator: their
    other sessions are invisible by construction, so counting them as exposed would fabricate
    a gap out of data the professor is not allowed to have."""
    body = _fn_body("professor_project_evidence")
    exposed = body[body.index("exposed as (") : body.index("sess as (")]
    assert "join public.group_members gm" in exposed
    assert "gm.group_id = cls.group_id and gm.profile_id = us.owner_id" in exposed


def test_attempts_count_successful_true_false_and_omitted() -> None:
    """The plan's rule: exploration includes unsuccessful attempts, and the App treats an
    omitted `successful` as landed. `attempts` counts all three; `successful_attempts` is the
    evidence column, never the headline."""
    body = _fn_body("professor_project_evidence")
    assert "coalesce((entry ->> 'successful')::boolean, true) as successful" in body
    assert "count(h.session_id) filter (" in body


def test_evidence_falls_back_from_objective_keys_to_the_class_focus() -> None:
    body = _fn_body("professor_project_evidence")
    assert "nullif(obj.node_keys, '{}'::text[]), sp.node_keys, cs.focus_node_keys" in body


def test_no_other_professor_rpc_is_redefined() -> None:
    """0060's tripwire, kept: this revision widens no existing projection."""
    code = _code()
    for fn in (
        "group_member_sessions",
        "group_member_names",
        "group_member_rating",
        "group_member_graph_edges",
        "group_member_video_analysis",
        "group_member_athlete",
        "can_read_member_row",
        "is_class_session_staff",
        "attach_to_class",
    ):
        assert f"create or replace function public.{fn}(" not in code, fn
        assert f"drop function if exists public.{fn}(" not in code, fn


# ── the exposure denominator stops being mutable ──────────────────────────────────────────


def test_class_stamp_lock_lives_in_0019s_trigger_function() -> None:
    """Folded into the existing BEFORE UPDATE function, not added as a second trigger: two
    triggers fire in NAME order, and a lock named before `trg_user_sessions_stale_write` would
    judge a stale racer's class id before 0019's skip ever ran."""
    code = _code()
    assert "create trigger" not in code
    up = code[code.index("_GUARD_FN_UP = ") : code.index("# 0019's body")]
    # 0019's stale-write skip stays FIRST.
    assert up.index("if NEW.updated_at < OLD.updated_at then") < up.index("class_session_id")
    assert "if OLD.class_session_id is not null" in up
    assert "NEW.class_session_id is distinct from OLD.class_session_id" in up


def test_class_stamp_lock_keeps_the_first_stamp_and_never_raises() -> None:
    """A raise would fail the whole `upsert` batch (`syncEngine` pushes the changed set in one
    call and only advances its cursor afterwards), so one re-scan would wedge a device's entire
    session sync until an App release shipped. Keeping OLD gives the same immutability."""
    code = _code()
    up = code[code.index("_GUARD_FN_UP = ") : code.index("# 0019's body")]
    assert "NEW.class_session_id := OLD.class_session_id;" in up
    assert "raise exception" not in up


def test_class_stamp_lock_lets_the_fk_set_null_through() -> None:
    """`class_sessions` FK is `on delete set null` (0026). If the referenced class is gone the
    NULL is the referential action, not a client push — blocking it would fail the delete."""
    code = _code()
    up = code[code.index("_GUARD_FN_UP = ") : code.index("# 0019's body")]
    assert "select 1 from public.class_sessions cs where cs.id = OLD.class_session_id" in up
    assert "return NEW;" in up


def test_downgrade_restores_0019_guard_body_verbatim() -> None:
    """The restored body must match 0019's `_GUARD_FN_UP` character for character (modulo
    whitespace), or a rollback leaves a function nobody wrote."""
    mine = _code()
    start = mine.index("_GUARD_FN_DOWN = ")
    restored = mine[mine.index('"""', start) + 3 : mine.index('"""', mine.index('"""', start) + 3)]

    prior = (VERSIONS / "0019_user_sessions_delete_guard.py").read_text(encoding="utf-8")
    p_start = prior.index("_GUARD_FN_UP = ")
    original = prior[
        prior.index('"""', p_start) + 3 : prior.index('"""', prior.index('"""', p_start) + 3)
    ]

    assert restored.split() == original.split()


# ── downgrade completeness ────────────────────────────────────────────────────────────────


def test_downgrade_drops_everything_upgrade_created() -> None:
    up, down = " ".join(_upgrade().split()), " ".join(_downgrade().split())
    for table in TABLES:
        assert f'op.create_table( "{table}"' in up, table
        assert f'op.drop_table("{table}")' in down, table
    for index in (
        "idx_professor_projects_group",
        "idx_professor_project_classes_project",
        "idx_class_objectives_class",
    ):
        assert f'op.create_index( "{index}"' in up or f'op.create_index("{index}"' in up, index
        assert f'op.drop_index( "{index}"' in down or f'op.drop_index("{index}"' in down, index
    assert "drop policy if exists {table}_select_staff on public.{table};" in down
    assert "drop function if exists {fn};" in down
    assert "_GUARD_FN_DOWN" in down


def test_downgrade_does_not_blank_the_0050_focus_column() -> None:
    """`class_sessions.focus_node_keys` predates this revision; 0062 only ever COPIES into it."""
    down = _downgrade()
    assert "op.drop_column" not in down
    # The only mention allowed is the comment explaining why it is left alone.
    assert down.index("focus_node_keys") > down.index("NOT reverted")


def test_models_mirror_has_every_new_table() -> None:
    """§2 of the supabase-schema-migration skill: models.py and the migration land together."""
    models = (Path(__file__).resolve().parents[1] / "db" / "models.py").read_text(encoding="utf-8")
    for cls, table in (
        ("ProfessorProject", "professor_projects"),
        ("ProfessorProjectClass", "professor_project_classes"),
        ("ClassFocusSpec", "class_focus_specs"),
        ("ClassObjective", "class_objectives"),
    ):
        assert f"class {cls}(Base):" in models, cls
        assert f'__tablename__ = "{table}"' in models, table
