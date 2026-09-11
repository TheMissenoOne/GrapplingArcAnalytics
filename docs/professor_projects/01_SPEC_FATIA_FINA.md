# Professor projects — spec da fatia fina (Fase B, lado Analytics)

**Alvo:** o §8 do plano do dono (2026-09-12): professor cria o projeto "Closed Guard" → anexa
uma aula com foco `move_set` → objetivo "cada aluno exposto tenta qualquer movimento do foco 3×
em 14 dias" → a página do projeto mostra **expostos / exploraram / repetiram / sem dados
suficientes** → a visão geral mostra a cobertura de exploração daquele projeto e uma lacuna com
evidência.

**Entrega:** `alembic/versions/0062_professor_projects.py` + espelho em `db/models.py` +
`tests/test_professor_projects.py`. Handoff de aplicação:
`docs/repairs/2026-09-12_0062_professor_projects.md`.

**Regra do §9.5 do plano:** tudo que o plano chamou de "default proposto" foi conferido contra o
código antes de virar schema. Cada um está abaixo como **ACEITO** ou **MUDADO**, com o motivo e o
arquivo que serviu de evidência.

---

## 1. Defaults validados

| # | Default proposto | Veredito | Motivo (evidência lida) |
|---|---|---|---|
| 1 | Baseline 14 d antes da aula, follow-up 14 d depois | **ACEITO** como *default*, não como constante | Viram colunas por objetivo (`baseline_days`/`followup_days`, `default 14`, CHECK 1..365). O Web hoje não tem janela em dias: `classFocus.focusApplicationWindow` usa `lookback = 5` **sessões**. São medidas diferentes; a janela em dias é a do plano e a que a evidência devolve. |
| 2 | `target_count` 3 | **ACEITO** como default de RPC, **MUDADO** quanto ao status | `class_objective_upsert(p_target_count default 3)`, e o CHECK só exige `>= 1`. Não existe calibração de "3" contra nada — é um default de UI, não um limiar medido. Mesma classe do `derive_difficulty` da 0058: transparente e re-ajustável, nunca apresentado como verdade. |
| 3 | Ciclo de vida: active / paused / completed | **ACEITO** (CHECK + `professor_project_set_status`) | `completed_at` é carimbado uma vez e **zerado em qualquer reabertura**; reabrir é explícito. |
| 4 | "Uma aula pertence a 0 ou 1 projeto **ativo**" | **MUDADO** para "0 ou 1 projeto, ponto" | Índice parcial não alcança `status`, que mora em *outra* tabela; denormalizar `status` para a tabela de ligação exigiria um trigger para mantê-lo verdadeiro. Então a PK de `professor_project_classes` **é** `class_session_id` — a regra vira impossível de violar, sem trigger. Mover a aula entre projetos é o mesmo upsert; projeto `completed` recusa os dois lados do movimento (`project_not_active`). |
| 5 | Exposto = aluno anexado via `class_session_id` | **ACEITO** na definição, **MUDADO** em duas bordas | (a) exige também linha em `group_members` do grupo do projeto — **convidado de drop-in (0060) fica fora**: `can_read_member_row` mostraria só a aula consentida dele, então ele apareceria como "exposto, zero tentativas", uma lacuna fabricada com dado que o professor não pode ter. (b) `user_sessions.class_session_id` passou a ser **write-once** — ver §5. |
| 6 | Tentativas contam `successful` true/false/omitido | **ACEITO** | `RoundEntry.successful?` — "Undefined is treated as successful" (`GrapplingArcApp/src/types/session.ts`). Logo `attempts` conta os três; `successful_attempts` conta true **e** omitido. Taxa de acerto é evidência, nunca manchete (constraint global do plano). |
| 7 | Nunca reflexões nem notas | **ACEITO**, e reforçado pela forma | A RPC de evidência devolve **contagens e datas**; a única coluna de texto é o `node_key` que o próprio professor escolheu. `reflection`/`notes`/`videoContext` não aparecem nem como filtro (teste `test_evidence_never_projects_private_session_text`). |
| 8 | Derivação pura no Web | **ACEITO** | A RPC devolve LINHAS; funil, KPIs, persistência e "lacuna" são derivação pura em `src/lib/*`. O Web continua sem tocar `user_sessions` em caminho de professor. |
| 9 | (implícito) casar foco com o que o aluno registrou por `normalizeLabel(label)` | **MUDADO** para `nodeKey` primeiro, label depois | `classFocus.focusOccurrences` casa só por `normalizeLabel(entry.label)`; mas o App resolve `entry.nodeKey` no momento da escolha (`nodeIdentity`), justamente para colapsar apelido em canônico ("Shoulder Crunch" = "Shoulder Clamp"). A regra da 0062 é `coalesce(nullif(nodeKey,''), normalize_node_key(label))` — superconjunto do que o Web faz hoje. Se o Web quiser números idênticos entre telas, adote o mesmo `coalesce` em `focusOccurrences`. |
| 10 | (implícito) janela ancorada em `starts_at` | **MUDADO** para o início do DIA (UTC) da aula | O QR carimba "a sessão do dia", e o `createdAt` dela pode ser **anterior** ao `starts_at` (treino de manhã, aula à noite). Ancorar no instante jogaria a própria sessão da aula na janela de *baseline*. Teto conhecido: `date_trunc('day', …)` é UTC, então uma sessão do fim da noite anterior (BRT) pode entrar na janela. Aceitável para contagem de exploração; não use isso como relógio de presença. |
| 11 | (implícito) data da tentativa | **MUDADO**: é a data da SESSÃO | `RoundEntry` não tem timestamp — nenhum. `first/last_attempt_at` são o `data->>'createdAt'` da sessão (fallback `user_sessions.updated_at` quando o texto não parseia). |

---

## 2. Modelo de dados

```
professor_projects(id, group_id→groups, created_by→profiles, name, description,
                   status active|paused|completed, starts_at, completed_at,
                   created_at, updated_at)
professor_project_classes(class_session_id PK →class_sessions, project_id→professor_projects,
                          position, created_at)
class_focus_specs(class_session_id PK →class_sessions, kind move_set|target_state|sequence,
                  node_keys text[], sequence_steps jsonb, created_at, updated_at)
class_objectives(id, class_session_id→class_sessions,
                 kind explore_move|reach_state|explore_sequence|increase_exploration,
                 node_keys text[], sequence_steps jsonb, target_count, target_percent,
                 baseline_days=14, followup_days=14, created_at, updated_at)
```

**RLS:** as quatro têm RLS ligada, `grant select` para `authenticated` e **policy só de SELECT**
(dono/professor do grupo, via `is_group_owner_or_professor` / `is_project_staff` /
`is_class_session_staff`). **Aluno não lê nenhuma das quatro.** Não há policy nem grant de
INSERT/UPDATE/DELETE: escrita é só pelas RPCs SECURITY DEFINER — mesma forma de
`session_video_jobs` (0058) e `class_guests` (0060).

**`class_sessions.focus_node_keys` (0050) continua, como PROJEÇÃO de compatibilidade.** O aluno
lê essa coluna pela policy `class_sessions_select_member`, e o Web já lê ela em `classFocus.ts`,
`curriculum.ts` e `ClassLive.tsx`. Quem escreve as duas metades é
**`class_focus_spec_upsert` (RPC), não um trigger** — a RPC já é a única escritora, então um
trigger seria um segundo mecanismo fazendo a mesma atribuição, e não cobriria o sentido inverso
(professor editando `focus_node_keys` direto pela policy de UPDATE da 0050).

> **Precedência, obrigação do lado Web:** existindo linha em `class_focus_specs`, ela é a
> verdade. Edite o foco por `class_focus_spec_upsert`, **não** por `updateClassPlan`
> (`classes.ts`), ou o `kind`/`sequence_steps` fica velho enquanto a coluna anda.

**`node_keys` é a coluna que TODA leitura de evidência consome, seja qual for o `kind`** — é isso
que deixa um novo tipo de foco aditivo: basta o novo tipo preencher `node_keys` com os itens cujas
tentativas contam. Para `kind='sequence'` a RPC deriva `node_keys` dos próprios
`sequence_steps` (`[{"node_key": "..."}, …]`, chaves extras ignoradas hoje).

---

## 3. Contrato RPC (o que o Web chama)

Convenção de erro idêntica à 0060: **`errcode = 'P0001'` e `message` == o código**. O Web lê
`error.message` e mapeia string por string.

### 3.1 Escrita

```ts
rpc('professor_project_upsert', {
  p_group_id: string, p_name: string,
  p_project_id?: string | null,      // null = criar
  p_description?: string | null,
  p_starts_at?: string | null,       // ISO
}) -> uuid                            // id do projeto
```
Erros: `empty_name` · `not_group_staff` · `project_not_found`.
⚠️ **UPDATE é substituição total** de `name`/`description`/`starts_at`: mande os valores atuais
dos campos que não está mudando, ou eles viram NULL.

```ts
rpc('professor_project_set_status', { p_project_id: string, p_status: 'active'|'paused'|'completed' }) -> void
```
Erros: `invalid_status` · `project_not_found` · `not_group_staff`.
`completed` carimba `completed_at` (mantém o primeiro); qualquer outro status zera.

```ts
rpc('professor_project_attach_class', {
  p_class_session_id: string,
  p_project_id?: string | null,   // null = DESANEXAR esta aula
  p_position?: number,            // default 0
}) -> void
```
Erros: `not_class_staff` · `project_not_found` · `not_group_staff` · `project_not_active`
(projeto de origem OU de destino `completed`/`paused`) · `group_mismatch` (aula e projeto em
grupos diferentes). Re-anexar a mesma aula a outro projeto ativo é o mesmo call (upsert na PK).

```ts
rpc('class_focus_spec_upsert', {
  p_class_session_id: string,
  p_kind: 'move_set'|'target_state'|'sequence',
  p_node_keys?: string[],          // default []
  p_sequence_steps?: unknown[],    // default []; [{ node_key: string, ... }]
}) -> void
```
Erros: `invalid_kind` · `not_class_staff` · `empty_focus` (nenhuma chave sobrou depois de
normalizar). Escreve a spec **e** `class_sessions.focus_node_keys`.

```ts
rpc('class_objective_upsert', {
  p_class_session_id: string,
  p_kind: 'explore_move'|'reach_state'|'explore_sequence'|'increase_exploration',
  p_objective_id?: string | null,  // null = criar
  p_node_keys?: string[],          // default [] = "o foco desta aula"
  p_sequence_steps?: unknown[],
  p_target_count?: number,         // default 3
  p_target_percent?: number | null,
  p_baseline_days?: number,        // default 14
  p_followup_days?: number,        // default 14
}) -> uuid
```
Erros: `invalid_kind` · `not_class_staff` · `invalid_target` (`increase_exploration` sem
`target_percent > 0`, ou os outros três sem `target_count >= 1`) · `invalid_window`
(`baseline_days`/`followup_days` fora de 1..365) · `objective_not_found` (id não é desta aula).

### 3.2 Leitura

```ts
rpc('professor_project_list', { p_group_id: string }) -> Array<{
  id: string; name: string; description: string | null;
  status: 'active'|'paused'|'completed';
  starts_at: string | null; completed_at: string | null;
  created_at: string; updated_at: string;
  class_count: number; classes_delivered: number;   // delivered = starts_at <= now()
  first_class_at: string | null; last_class_at: string | null;
}>
```
Zero linhas se quem chama não for dono/professor do grupo (sem erro — a RPC não conta se o grupo
existe). A lista de aulas de um projeto **não** vem daqui: leia
`professor_project_classes` direto (policy de SELECT) e junte com `class_sessions`, que o Web já
busca.

```ts
rpc('professor_project_evidence', { p_project_id: string }) -> Array<{
  class_session_id: string;
  class_starts_at: string;
  objective_id: string | null;        // objetivo cuja janela foi usada (mais recente da aula)
  profile_id: string;                 // aluno exposto; nome vem do roster que o Web já tem
  node_key: string;                   // item do foco (ou do objetivo, se ele listar os seus)
  window_start: string;               // início do DIA da aula (UTC)
  window_end: string;                 // window_start + followup_days
  attempts: number;                   // successful true|false|omitido
  successful_attempts: number;        // true + omitido
  sessions_with_attempt: number;      // sessões DISTINTAS com >= 1 tentativa na janela
  first_attempt_at: string | null;
  last_attempt_at: string | null;
  baseline_attempts: number;                 // janela [window_start - baseline_days, window_start)
  baseline_sessions_with_attempt: number;
  sessions_in_window: number;         // sessões que o aluno registrou na janela, com ou sem foco
}>
```

**Uma linha por (aula × aluno exposto × node_key do foco), inclusive com zeros.** O produto
cartesiano é de propósito: "exposto e não explorou" é a informação que o funil precisa e não
existiria se a RPC só devolvesse acertos.

Acesso: `is_group_owner_or_professor(projeto.group_id)` na entrada; cada sessão lida passa por
`can_read_member_row(us.owner_id, us.class_session_id)` — o **mesmo** predicado de
`group_member_sessions()` (0060) — e `us.deleted_at is null` (0044).

---

## 4. Fronteira: o que a RPC devolve × o que o Web deriva

| A RPC devolve | O Web deriva (puro, `src/lib/`) |
|---|---|
| linhas por (aula, aluno, node_key) com contagens e datas | **expostos** = `profile_id` distintos da aula |
| `attempts` | **exploraram** = alunos com `attempts >= objetivo.target_count` (ou `>= 1` para "tocou no assunto" — decisão de apresentação) |
| `sessions_with_attempt` | **repetiram / persistência** = `sessions_with_attempt >= 2` (repetir em sessão POSTERIOR, não dois registros na mesma) |
| `sessions_in_window` | **sem dados suficientes** = `sessions_in_window = 0` (não treinou/não sincronizou) — distinto de "treinou e não explorou" |
| `successful_attempts` | `reach_state` (estado alcançado = tentativa bem-sucedida); taxa de acerto como evidência secundária |
| `baseline_attempts` | `increase_exploration`: `attempts` vs `baseline_attempts` contra `target_percent` |
| `class_starts_at`, `window_*` | linha do tempo da aula, rótulo da janela, "exposição adequada?" |
| nada | **nomes** (`fetchRoster`), **ELO relativo** (`group_member_rating`), texto de qualquer espécie |

Quatro tipos de objetivo, **três** deles são derivação pura sobre as MESMAS linhas —
`explore_move` lê `attempts`, `reach_state` lê `successful_attempts`, `increase_exploration` lê
`baseline_attempts`. É isso que "aditivo depois" quer dizer aqui.

**Teto conhecido, declarado:** `explore_sequence` **não** é respondível por contagem por nó — ele
precisa da ORDEM das entradas dentro do round. Quando entrar, acrescente uma coluna
`sequence_matches integer` nesta mesma RPC (mesma varredura, mais um lateral sobre
`data->'rounds'->'entries'` particionado por `sequenceId`), **não** uma segunda RPC. Mudar o
`returns table` obriga `drop`+`create` da função: é justamente por isso que as colunas de
baseline já entram agora, mesmo sem a primeira fatia usá-las.

---

## 5. O denominador de exposição (achado da Fase A)

`user_sessions.class_session_id` é o denominador de tudo aqui, e a policy `user_sessions_owner_all`
(0023) é `FOR ALL` do dono — ou seja, **a parte medida podia mover ou apagar a própria presença**.
E já apagava por acidente: `getUserSessionsSince` seleciona `id,data,updated_at,deleted_at`
(nunca puxa a coluna) enquanto `pushSessionsBatch` manda `class_session_id: s.classSessionId ??
null` em **todo** push (`sessionSync.ts:257`) — qualquer device cuja cópia local perdeu o campo
zerava a presença registrada.

A trava foi **dobrada dentro de `guard_user_sessions_stale_write()` (0019)**, não adicionada como
um segundo trigger: dois triggers disparam em ordem de NOME, e `trg_user_sessions_class_lock`
viria **antes** de `trg_user_sessions_stale_write` — um racer atrasado teria o `class_session_id`
julgado antes do skip da 0019. Uma função, um trigger, ordem determinística.

| Caso | Comportamento | Motivo |
|---|---|---|
| `OLD` NULL | passa qualquer valor | é o carimbo do QR |
| `NEW` NULL, `OLD` não, aula ainda existe | **ignora o NULL, mantém `OLD`** | é o push benigno de um device que nunca puxou a coluna |
| `NEW` NULL, `OLD` não, aula **apagada** | passa | é a ação `on delete set null` do FK (0026); bloquear faria o DELETE falhar |
| `NEW` ≠ `OLD`, ambos não-nulos | **mantém `OLD`, em silêncio** | ver abaixo |
| `NEW` = `OLD` | passa | re-push idempotente |

**Por que não `raise 'class_session_locked'`** (era a forma pedida): `syncEngine` empurra o
conjunto inteiro num `upsert` só e só avança o cursor **depois** que o push resolve
(`syncEngine.ts:211`/`:302`). Uma linha recusada derruba o lote, o cursor não anda, e o próximo
passe re-empurra a mesma linha ofensora — **para sempre**. Um aluno que escaneasse o QR de uma
segunda aula em cima da mesma sessão travaria toda a sincronização de sessões do device dele até
sair uma release do App. A imunidade que a métrica precisa é "vale o primeiro carimbo", e manter
`OLD` entrega exatamente isso sem armadilha fail-closed. Comentário `ponytail:` no código nomeia o
caminho de upgrade: se o produto quiser avisar o aluno, o `raise` volta **na mesma release** que o
handler do App, nunca antes.

Efeito colateral bom, de graça: a erosão acidental de presença descrita acima para de acontecer.

---

## 6. `normalize_node_key` — quarta porta do mesmo contrato

O plano proíbe o Web de consultar `user_sessions` em caminho de professor, então o casamento
label→chave acontece em SQL. Isso cria uma **quarta** implementação de `normalizeLabel`:

| lado | arquivo |
|---|---|
| App | `src/services/graphSync.ts:normalizeLabel` |
| Web | `src/lib/derive/normalizeLabel.ts` |
| Analytics (Python) | `analysis/names.py:_normalize_name` |
| Analytics (SQL, **novo**) | `public.normalize_node_key(text)` (0062) |

`lower → strip [^a-z0-9 ] → colapsa espaços → trim`. Duas armadilhas resolvidas no corpo:

1. **`collate "C"` é carga**, não enfeite: sob `en_US.UTF-8` a faixa `[a-z]` é ordenada por
   locale e **manteria** letras acentuadas que os outros três lados removem. `Galvão` tem que
   virar `galvo` (remove, não deacentua) nas quatro portas.
2. O `btrim` vai no **fim**: `btrim` só come espaço, enquanto `.trim()` do JS come todo
   whitespace. Rodando por último, sobre uma string já limpa, os dois dão o mesmo resultado.

A linha "Node key" do `CLAUDE.md` raiz precisa ganhar esta quarta porta quando a 0062 entrar.

---

## 7. Não construído, de propósito

- **`professor_project_detach_class` separado** — `attach_class(p_project_id => null)` é o
  desanexar. A identidade da ligação é a AULA (é a PK), então não há nada que uma segunda função
  expresse que essa não expresse.
- **Rota de escrita do aluno** em qualquer uma das quatro tabelas — nem policy, nem grant.
- **Agregado de turma sobre dado privado** (média, ranking, benchmark do roster). A 0050 já
  recusou isso explicitamente; a evidência aqui é **por aluno**, e o Web agrega para a MESMA
  equipe que já pode ver aquele aluno. Nada aqui sai do escopo do grupo, e nada aqui vira corpus,
  centroide, dataset de CV ou `site/`.
- **Arquétipo** na evidência — é a Fase D/E do plano, com portão de pesquisa antes.
- **Convidado de drop-in no denominador** — §1 item 5.
- **Índice em `class_focus_specs`/`professor_project_classes` além da PK** — `ponytail:` a PK
  serve toda leitura desta fatia; `idx_professor_project_classes_project` existe só porque a
  leitura "aulas deste projeto" vai pelo lado oposto da PK.

---

## 8. Pares (regra dos dois PRs)

| lado | o que falta |
|---|---|
| **Web** (`GrapplingArcWeb`) | `src/lib/professorProjects.ts` (as 4 RPCs de escrita + `professor_project_list`), `classObjectives.ts`, `projectEvidence.ts` (derivação pura sobre as linhas do §3.2), rotas `Projects`/`ProjectDetail`, item "Projects" no Shell. Cache por `scopedKey('project-evidence', projectId)`. **Editar foco pela RPC, não por `updateClassPlan`.** |
| **App** (`GrapplingArcApp`) | Nada é obrigatório. A 0062 é aditiva e o App não chama nenhuma RPC nova. Só observe o §5: a partir da aplicação, um segundo QR na mesma sessão não muda mais a presença no servidor — o estado local do device continua mostrando a última aula escaneada. Se isso incomodar, o conserto é no App (não re-carimbar sessão já carimbada), não no banco. |

Nada disso é pré-requisito para aplicar a 0062.
