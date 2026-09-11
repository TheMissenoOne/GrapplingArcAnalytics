# 0062 — professor projects, focos de aula, objetivos (fatia fina da Fase B)

**Estado:** migração escrita, **NÃO aplicada**. Aplicar é ação do dono/orquestrador (skill
`supabase-schema-migration` §6). Head anterior: `0061` (composite identity de `user_sessions`).

**Spec completa (defaults ACEITO/MUDADO + contrato do Web):**
`docs/professor_projects/01_SPEC_FATIA_FINA.md`.

**Arquivos:**

- `alembic/versions/0062_professor_projects.py` (revision `0062`, down_revision `0061`)
- `db/models.py` — `ProfessorProject`, `ProfessorProjectClass`, `ClassFocusSpec`, `ClassObjective`
- `tests/test_professor_projects.py` — source-scan (a suíte roda em SQLite, não executa migração
  Postgres — nota de escopo da 0019)

**O que entra:** 4 tabelas (RLS, SELECT só para dono/professor, **nenhuma policy nem grant de
escrita**), 9 funções novas, e uma alteração no corpo de `guard_user_sessions_stale_write()`
(0019) que torna `user_sessions.class_session_id` write-once.

---

## 1. HANDOFF — comando de aplicação (só o dono/orquestrador roda)

```bash
cd /home/vetor/GrapplingArc/GrapplingArcAnalytics
uv run alembic history | head -3     # confirmar "0061 -> 0062 (head)"
DATABASE_URL=<DSN de prod, NUNCA colado em chat/log> uv run alembic upgrade head
```

Não rodei — e não devo — `alembic upgrade`, `apply_migration` por MCP, nem SQL manual contra o
projeto vivo.

**Pré-requisito:** a `0061` precisa estar aplicada antes (em 2026-09-09 o head vivo era `0060`).
Conferir `select version_num from alembic_version;` antes de rodar.

**Lacuna conhecida (a mesma de sempre):** não existe Postgres local neste repo — a suíte roda
SQLite in-memory. O corpo SQL/RLS/trigger desta revisão **não foi executado em lugar nenhum**. Os
testes são leitura de fonte. A primeira execução real será a de produção. As duas partes que mais
merecem olho na primeira execução: (a) o corpo do
`professor_project_evidence` (CTEs + `filter (where …)` + `make_interval`), (b) o
`collate "C"` dentro de `normalize_node_key`.

**Gates locais rodados** (regra OOM: um por vez):

```
uv run pytest tests/test_professor_projects.py -q   # 27 passed
uv run pytest tests/test_db.py -q                   # 28 passed (create_all com as 4 tabelas novas)
uv run ruff check alembic/versions/0062_professor_projects.py db/models.py \
                  tests/test_professor_projects.py  # All checks passed
```

---

## 2. Checagem de drift pós-aplicação (§7 da skill)

**A. Advisor** (`get_advisors` por MCP Supabase, ou Database → Advisors). Esperado: **0 ERROR**.
Comparar WARN/INFO antes/depois — WARN novo inesperado já é sinal de drift.

**B. Query direta, read-only** (padrão `db-prober`: `uv run python -` com `db.base.db_session`,
só SELECT):

```sql
-- Em que migração o banco vivo acha que está?
select version_num from alembic_version;   -- esperado: 0062

-- RLS ligada nas quatro tabelas novas.
select relname, relrowsecurity
from pg_class
where relnamespace = 'public'::regnamespace and relkind = 'r'
  and relname in ('professor_projects','professor_project_classes',
                  'class_focus_specs','class_objectives')
order by relname;                          -- esperado: 4 linhas, todas true

-- Policies novas: esperado EXATAMENTE quatro, todas cmd = SELECT, roles = {authenticated}.
select tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public'
  and tablename in ('professor_projects','professor_project_classes',
                    'class_focus_specs','class_objectives')
order by tablename, policyname;

-- Nenhum grant de escrita nas quatro. Esperado: só SELECT, só authenticated.
select table_name, grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in ('professor_projects','professor_project_classes',
                     'class_focus_specs','class_objectives')
order by table_name, grantee, privilege_type;

-- As nove funções novas existem; as oito de acesso são SECURITY DEFINER com search_path pinado,
-- e normalize_node_key NÃO é definer (é pura).
select p.proname, p.prosecdef, p.provolatile, p.proconfig
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('normalize_node_key','is_project_staff','professor_project_upsert',
                    'professor_project_set_status','professor_project_attach_class',
                    'class_focus_spec_upsert','class_objective_upsert',
                    'professor_project_list','professor_project_evidence')
order by p.proname;
-- esperado: prosecdef = true em todas MENOS normalize_node_key (false, provolatile = 'i')

-- anon não executa nenhuma delas. Esperado: 9 linhas, todas false.
select p.proname, has_function_privilege('anon', p.oid, 'execute') as anon_can_execute
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('normalize_node_key','is_project_staff','professor_project_upsert',
                    'professor_project_set_status','professor_project_attach_class',
                    'class_focus_spec_upsert','class_objective_upsert',
                    'professor_project_list','professor_project_evidence')
order by p.proname;

-- O trigger da 0019 continua UM só, e o corpo ganhou a trava de classe SEM perder o skip.
select tgname from pg_trigger
where tgrelid = 'public.user_sessions'::regclass and not tgisinternal;
-- esperado: exatamente trg_user_sessions_stale_write

select pg_get_functiondef(p.oid)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'guard_user_sessions_stale_write';
-- esperado no corpo, NESTA ORDEM: "if NEW.updated_at < OLD.updated_at then"
--                                 "if OLD.class_session_id is not null"
--                                 "NEW.class_session_id := OLD.class_session_id;"
-- e NENHUM "raise exception"

-- group_member_sessions() NÃO foi tocada (a 0062 não redefine projeção nenhuma da 0060).
select pg_get_functiondef(p.oid)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'group_member_sessions';
-- esperado: ainda "can_read_member_row(us.owner_id, us.class_session_id)" e "deleted_at is null"
```

**C. Duas checagens específicas desta revisão** (SELECT, seguras):

```sql
-- 1. normalizeLabel em SQL == normalizeLabel em JS/Python. A terceira linha é a que prova o
--    collate "C": sem ele, sob en_US.UTF-8, o "ã" sobreviveria.
select public.normalize_node_key('  Closed   Guard ')  = 'closed guard'   as t1,
       public.normalize_node_key('De La Riva!')        = 'de la riva'     as t2,
       public.normalize_node_key('Galvão')             = 'galvo'          as t3,
       public.normalize_node_key(e'Arm\tBar')          = 'armbar'         as t4;
-- esperado: t1..t4 todos true

-- 2. A projeção de compatibilidade fica coerente depois do primeiro uso da RPC de foco.
select cs.id, cs.focus_node_keys, sp.kind, sp.node_keys
from public.class_sessions cs
join public.class_focus_specs sp on sp.class_session_id = cs.id
where cs.focus_node_keys is distinct from sp.node_keys;
-- esperado: zero linhas (qualquer linha aqui = alguém editou a coluna por fora da RPC)
```

---

## 3. Contrato para o builder do Web (cópia da spec §3)

Erros: **`errcode = 'P0001'`, `message` == o código** (convenção da 0060). O Web lê
`error.message` e mapeia string por string.

### Escrita

| RPC | args | retorno | erros |
|---|---|---|---|
| `professor_project_upsert` | `p_group_id`, `p_name`, `p_project_id?` (null = criar), `p_description?`, `p_starts_at?` | `uuid` | `empty_name`, `not_group_staff`, `project_not_found` |
| `professor_project_set_status` | `p_project_id`, `p_status` ∈ active/paused/completed | void | `invalid_status`, `project_not_found`, `not_group_staff` |
| `professor_project_attach_class` | `p_class_session_id`, `p_project_id?` (null = desanexar), `p_position?` | void | `not_class_staff`, `project_not_found`, `not_group_staff`, `project_not_active`, `group_mismatch` |
| `class_focus_spec_upsert` | `p_class_session_id`, `p_kind` ∈ move_set/target_state/sequence, `p_node_keys?`, `p_sequence_steps?` | void | `invalid_kind`, `not_class_staff`, `empty_focus` |
| `class_objective_upsert` | `p_class_session_id`, `p_kind` ∈ explore_move/reach_state/explore_sequence/increase_exploration, `p_objective_id?`, `p_node_keys?`, `p_sequence_steps?`, `p_target_count?`=3, `p_target_percent?`, `p_baseline_days?`=14, `p_followup_days?`=14 | `uuid` | `invalid_kind`, `not_class_staff`, `invalid_target`, `invalid_window`, `objective_not_found` |

⚠️ `professor_project_upsert` em modo UPDATE **substitui** `name`/`description`/`starts_at`:
mande os valores atuais dos campos que não está mudando.
⚠️ Nome dos parâmetros é `p_*`. PostgREST casa por nome — mandar `project_id` em vez de
`p_project_id` dá `PGRST202` (function not found). Mesma pegadinha documentada na 0060.

### Leitura

```ts
rpc('professor_project_list', { p_group_id }) -> Array<{
  id, name, description, status, starts_at, completed_at, created_at, updated_at,
  class_count, classes_delivered, first_class_at, last_class_at }>

rpc('professor_project_evidence', { p_project_id }) -> Array<{
  class_session_id, class_starts_at, objective_id, profile_id, node_key,
  window_start, window_end,
  attempts, successful_attempts, sessions_with_attempt,
  first_attempt_at, last_attempt_at,
  baseline_attempts, baseline_sessions_with_attempt, sessions_in_window }>
```

Uma linha por **(aula × aluno exposto × node_key do foco)**, zeros inclusos — "exposto e não
explorou" é informação, não ausência. Derivação no Web (pura):

- **expostos** = `profile_id` distintos por aula
- **exploraram** = `attempts >= objetivo.target_count`
- **repetiram / persistência** = `sessions_with_attempt >= 2`
- **sem dados suficientes** = `sessions_in_window = 0`
- **`reach_state`** lê `successful_attempts`; **`increase_exploration`** compara `attempts` com
  `baseline_attempts` contra `target_percent`
- nomes vêm do roster (`fetchRoster`), nunca desta RPC

Nunca há texto do aluno na resposta: sem `reflection`, sem `notes` de round, sem `videoContext`,
sem título/goal de sessão. A única coluna de texto é o `node_key` que o próprio professor escolheu.

**Obrigação:** existindo `class_focus_specs` para a aula, edite o foco por
`class_focus_spec_upsert`, **não** por `classes.updateClassPlan` — senão `kind`/`sequence_steps`
ficam velhos enquanto a coluna anda.

---

## 4. Risco novo que a aplicação introduz (leia antes de aplicar)

`user_sessions.class_session_id` vira write-once dentro do trigger da 0019. A partir da
aplicação:

- um device que empurra `class_session_id: null` numa sessão já carimbada **para de apagar** a
  presença (bug silencioso que existia — `getUserSessionsSince` nunca puxa a coluna);
- um aluno que escaneia o QR de uma **segunda** aula em cima da mesma sessão continua vendo a
  segunda aula no próprio device, mas o servidor mantém a primeira. Não dá erro, não trava sync.
  Se o produto quiser avisar o aluno, o `raise 'class_session_locked'` volta **junto** com o
  handler do App, nunca antes (o `raise` sozinho derruba o lote do `upsert` e trava o cursor de
  sync — `syncEngine.ts:211`/`:302`).

Rollback: `downgrade()` restaura o corpo de 0019 **verbatim** (teste
`test_downgrade_restores_0019_guard_body_verbatim`) e derruba as 4 tabelas, as 4 policies e as 9
funções. O que **não** volta atrás: os valores que `class_focus_spec_upsert` projetou em
`class_sessions.focus_node_keys` — essa coluna é da 0050, os valores são o foco real do professor,
e apagá-los seria destruir dado que a 0062 só copiou.

---

## 5. Doc que muda junto (regra do mesmo push)

- `CLAUDE.md` raiz, linha "Node key": ganha a **quarta** porta do `normalizeLabel`,
  `public.normalize_node_key(text)` (0062) — e a nota de que `collate "C"` é o que mantém a
  paridade com o strip-não-deacentua dos outros três lados.
- `CLAUDE.md` raiz, tabela de contratos: uma linha nova para professor projects (App: nada;
  Analytics: 0062 + RPCs; Web: `professorProjects.ts`/`classObjectives.ts`/`projectEvidence.ts`),
  no mesmo formato da linha de "Drop-in class join".
- `.wikis/GrapplingArcAnalytics/Contratos.md` — mesma entrada.
- `dashboard/` — item de atenção "0062 pendente de aplicação em prod" via o agente `dashboard`.
