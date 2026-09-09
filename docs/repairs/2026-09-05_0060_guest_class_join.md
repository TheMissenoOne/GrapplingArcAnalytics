# 0060 — drop-in de não-membro numa aula (guest class join)

**Estado:** migração escrita, **NÃO aplicada**. Aplicar é ação do dono/orquestrador (skill
`supabase-schema-migration` §6).

**Decisão vinculante (dono, 2026-09-03, item 5):** *"Não-membro em aula via QR ⇒ confirmação
explícita antes de entrar; professor vê só aquela aula/sessão + análise dela, nada do histórico."*

**Arquivos:**

- `alembic/versions/0060_attach_to_class_guest.py` (revision `0060`, down_revision `0059`)
- `db/models.py` — modelo `ClassGuest` (espelho da tabela nova)
- `tests/test_attach_to_class_guest.py` — source-scan (a suíte roda em SQLite, não executa
  migração Postgres — nota de escopo da 0019)

---

## 1. O contrato do App

### 1.1 `attach_to_class_guest(p_token text)` — NOVO

```
rpc('attach_to_class_guest', { p_token: '<token do QR>' })
  → [{ class_id: uuid, class_title: text }]
```

Mesma forma de retorno de `attach_to_class` (0026), então o handler do App é o mesmo caminho de
código: valida o token, grava o consentimento, devolve a aula. **Não escreve `user_sessions`** —
carimbar `class_session_id` na sessão do dia continua sendo escrita do próprio cliente sob a
policy `user_sessions_owner_all` (0023), exatamente como no fluxo de membro
(`QRScanScreen.tsx` → `updateSession({ classSessionId: cls.id })`).

Note o nome do parâmetro: **`p_token`**, não `token`. `attach_to_class` (0026) usa `token`; o
novo segue a convenção `p_*` de 0045/0054/0059. PostgREST casa por nome — mandar `token` aqui dá
`PGRST202` (function not found).

**Erros** (todos `errcode = 'P0001'`; o `message` é o código):

| código | quando | o que o App diz |
|---|---|---|
| `bad_token` | nenhum `class_sessions.join_token` igual a `p_token` | "código inválido" |
| `class_closed` | token existe mas `token_expires_at <= now()` | "essa aula já encerrou" |
| `already_member` | quem chamou **é** membro do grupo da aula | chamar `attach_to_class` (fluxo de membro) e seguir |

`bad_token` e `class_closed` são distinguíveis de propósito — 0026 funde os seus dois erros para
esconder *pertencimento a grupo*, pergunta que não existe aqui (quem chama já não é membro, por
definição, e já tem o token na mão). `already_member` é o sinal de fallback: o App tenta o
caminho de convidado, recebe isso, e refaz com `attach_to_class`. Ordem de checagem no servidor:
`bad_token` → `class_closed` → `already_member`.

Re-scan da mesma aula é idempotente (`on conflict do nothing`) e **mantém o primeiro
`consent_at`** — o consentimento que vale é o dado antes de entrar.

Consentimento explícito é obrigação do App **antes** da chamada (o card de confirmação que
`QRScanScreen.tsx` já usa para o fluxo de membro, com cópia própria de convidado: "o professor
desta aula vai ver a sessão de hoje, e só ela"). A RPC grava o timestamp; ela não pode provar que
a tela foi mostrada.

### 1.2 `class_session_guests(p_class_session_id uuid)` — NOVO (Web, aula ao vivo)

```
rpc('class_session_guests', { p_class_session_id: '<uuid da aula>' })
  → [{ profile_id: uuid, full_name: text, consent_at: timestamptz }]
```

Só o dono/professor do grupo da aula recebe linhas (`is_class_session_staff` por dentro). O
convidado **não** aparece em `group_member_names` (roster de membros) — é este RPC, e só ele, que
o mostra, e apenas dentro daquela aula. Sem faixa (0057), sem rating, sem vínculo de atleta.

### 1.3 `group_member_sessions()` — sem mudança de assinatura

Mesma assinatura, mesma projeção, mesmos grants (`create or replace`). O predicado passou a ser
`can_read_member_row(us.owner_id, us.class_session_id)`, cujo primeiro disjunto é literalmente
`shares_group_as_professor(p_profile)` — o caminho de membro não muda. O segundo disjunto só é
verdadeiro para linha cujo `class_session_id` é exatamente a aula que aquele convidado
consentiu, lida pela equipe daquela aula.

Efeito prático no Web, sem nenhuma mudança de cliente:

- `classes.ts:fetchAttachedCount` (`.eq('class_session_id', classId)`) passa a contar convidados.
- `students.ts:fetchRosterSessions` agrupa por `owner_id`; a chave de um convidado simplesmente
  não casa com nenhuma linha do roster e é ignorada. **Sem vazamento para a lista de alunos.**
- `students.ts:fetchStudentSessions(profileId)` com o `owner_id` de um convidado devolve no
  máximo a sessão daquela aula — o predicado é por linha.

### 1.4 O que NÃO mudou (e por quê)

`group_member_names`, `group_member_rating`, `group_member_graph_edges`,
`group_member_video_analysis`, `group_member_athlete` — todas começam em
`from public.group_members gm` e exigem linha para o perfil-alvo. Convidado não tem essa linha,
logo as cinco já devolvem zero linhas para ele. **A metade "nada do histórico" da decisão é
garantida pela forma que essas funções já têm**, não por algo que a 0060 acrescenta. Nenhuma foi
tocada — o teste `test_no_other_professor_rpc_is_redefined` trava isso.

Consequência a comunicar: **análise de vídeo de convidado não é exposta.**
`group_member_video_analysis` exige `gm.share_video_analysis`, opt-in por associação que o
convidado não tem como ligar. Mesma resposta que 0059 dá para um membro que nunca ligou o flag.
Caminho de upgrade, se o dono quiser: `class_guests.share_video_analysis` + um setter, no dia em
que o App tiver o toggle — não antes.

---

## 2. HANDOFF — comando de aplicação (só o dono/orquestrador roda)

```bash
cd /home/vetor/GrapplingArc/GrapplingArcAnalytics
uv run alembic history | head -3     # confirmar "0059 -> 0060 (head)"
DATABASE_URL=<DSN de prod, NUNCA colado em chat/log> uv run alembic upgrade head
```

Não rodei — e não devo — `alembic upgrade`, `apply_migration` por MCP, nem SQL manual contra o
projeto vivo (`cpkfcepvqxvfzquabfpj`).

**Lacuna conhecida:** não existe Postgres local neste repo (a suíte roda SQLite in-memory), então
o corpo SQL/RLS desta revisão **não foi executado em lugar nenhum**. Os testes são leitura de
fonte. A primeira execução real será a de produção.

---

## 3. Checagem de drift pós-aplicação (§7 da skill)

**A. Advisor** (`get_advisors` via MCP Supabase, ou Database → Advisors). Esperado: **0 ERROR**.
Comparar contagem de WARN/INFO antes/depois — WARN novo inesperado já é sinal de drift.

**B. Query direta, read-only** (padrão `db-prober`: `uv run python -` com `db.base.db_session`,
só SELECT):

```sql
-- Quais tabelas têm RLS ligado? class_guests tem de aparecer com relrowsecurity = true.
select relname, relrowsecurity
from pg_class
where relnamespace = 'public'::regnamespace and relkind = 'r'
order by relname;

-- Toda policy viva no schema public — diferenciar à mão contra
-- alembic/versions/*.py. Esperado exatamente UMA linha nova:
--   class_guests | class_guests_select_self_or_staff | SELECT | {authenticated}
select schemaname, tablename, policyname, cmd, roles
from pg_policies
where schemaname = 'public'
order by tablename, policyname;

-- Em que migração o banco vivo acha que está?
select version_num from alembic_version;   -- esperado: 0060
```

Checagens específicas desta revisão:

```sql
-- 1. Nenhum grant de escrita em class_guests. Esperado: só SELECT, só authenticated.
select grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public' and table_name = 'class_guests'
order by grantee, privilege_type;

-- 2. As quatro funções novas existem, são SECURITY DEFINER e têm search_path pinado.
select p.proname, p.prosecdef, p.proconfig
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('attach_to_class_guest','class_session_guests',
                    'can_read_member_row','is_class_session_staff')
order by p.proname;
-- esperado: prosecdef = true nas quatro, proconfig contendo search_path

-- 3. anon não executa nenhuma delas. Esperado: 4 linhas, todas false.
select p.proname, has_function_privilege('anon', p.oid, 'execute') as anon_can_execute
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname in ('attach_to_class_guest','class_session_guests',
                    'can_read_member_row','is_class_session_staff')
order by p.proname;

-- 4. group_member_sessions() manteve o caminho de membro E ganhou o de convidado.
select pg_get_functiondef(p.oid)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'group_member_sessions';
-- esperado no corpo: "can_read_member_row(us.owner_id, us.class_session_id)"
--                  e "us.deleted_at is null"

-- 5. attach_to_class (0026) NÃO foi alterada — o caminho de membro é o de ontem.
select pg_get_functiondef(p.oid)
from pg_proc p join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public' and p.proname = 'attach_to_class';
-- esperado: ainda 'invalid_or_not_member', ainda is_group_member
```

---

## 4. Lado App / Web (PRs pares — esta é só a de Analytics)

| lado | o que falta |
|---|---|
| App | `groupService.ts`: `attachToClassGuest(token)` chamando `rpc('attach_to_class_guest', { p_token })`, mapeando `bad_token`/`class_closed` para mensagens e `already_member` para o fallback `attachToClass`. `QRScanScreen.tsx`: card de confirmação com cópia de convidado. Hoje o erro do Supabase é engolido num `InvalidClassTokenError` único (`groupService.ts:50`) — o novo fluxo precisa ler `error.message`. |
| Web | `classes.ts`/ClassLive: `rpc('class_session_guests', { p_class_session_id })` para listar quem entrou como visitante naquela aula, separado do roster. |

Regra do root `CLAUDE.md`: dois PRs, um por repo, referenciados entre si, esperados para entrar
juntos. **Nada disso é pré-requisito para aplicar a 0060** — a migração é aditiva; sem o App, o
RPC apenas nunca é chamado.
