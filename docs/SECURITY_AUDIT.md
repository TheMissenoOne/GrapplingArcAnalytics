# Auditoria público × privado — App, Web e a camada de acesso

**Data:** 2026-08-13/14 · **Escopo:** auditoria, sem remediação · **Alvo:** projeto Supabase de
produção + `GrapplingArcApp` + `GrapplingArcWeb`

Em 2026-08-13 foi gravada uma regra de governança na raiz do workspace, por questões éticas e de
LGPD:

> **DEVE SEMPRE HAVER DISTINÇÃO ENTRE DADOS PÚBLICOS E PRIVADOS. DADOS ALIMENTADOS DIRETAMENTE
> PELO APP COMO USUÁRIO SÃO PRIVADOS E ANONIMIZADOS PARA PROCESSAMENTO E NUNCA SÃO USADOS
> DIRETAMENTE PARA ANÁLISES COMPETITIVAS.**

Com o esclarecimento posterior: **vetorizar dado do app é permitido** quando o resultado volta pro
mesmo usuário (dossiê dele, grapple-like, arquétipo dele). O limite é a **finalidade**, não a
técnica.

O lado Analytics já havia sido auditado e corrigido no commit `3addd5e` (centroides de arquétipo e
`nearest_graphs` passaram a filtrar `owner_kind`, travados por `tests/test_private_data_boundary.py`).
Esta rodada cobriu os outros dois clientes do mesmo banco: **GrapplingArcApp** (quem produz o dado
privado) e **GrapplingArcWeb** (segundo cliente, nunca verificado ponta a ponta).

**Resultado de cabeçalho: nenhum caminho privado → competitivo foi encontrado em nenhum dos três
lados.** A regra, no sentido estrito em que foi escrita, está sendo cumprida. O que a auditoria
achou é outra classe de problema — vazamento **privado → terceiro** e lacunas de LGPD.

---

## 1. Camada de acesso (Supabase)

Auditada primeiro e direto no banco de produção (leitura apenas: `pg_policy`,
`information_schema`, linter do Supabase). É a camada que de fato protege os dois clientes.

### O que está correto

**RLS ligada em todas as 28 tabelas.** Cinco têm RLS ligada e zero políticas (`alembic_version`,
`bundle_imports`, `frame_annotations`, `map_edges`, `matches`) — isso nega tudo para
`anon`/`authenticated` e só passa por `service_role`. Seguro por padrão.

**A separação público/privado está codificada nas políticas, exatamente como a regra pede:**

| tabela | política | efeito |
|---|---|---|
| `graphs` | `graphs_user_select` | `owner_kind='user'` só o dono (`owner_id = auth.uid()`) |
| `graphs` | `graphs_athlete_read` | `owner_kind='athlete'` só se `athletes.is_published` |
| `graph_edges` | 2 políticas espelhadas | idem, via join em `graphs` |
| `user_sessions` | `user_sessions_owner_all` | só o dono, em todos os comandos |
| `profiles` | `profiles_select_own` | só o próprio (`id = auth.uid()`) |
| `athletes` | `athletes_published_read` | `anon` lê só `is_published` |

**Os RPCs de associação são apertados.** `join_group(invite_code)` grava `role` fixo em
`'student'` — não há caminho de auto-promoção a professor — e devolve o mesmo erro para código
inválido, expirado e revogado, para não revelar se um grupo existe. `attach_to_class(token)` exige
`is_group_member` e valida expiração. `group_members` não tem política de INSERT nem UPDATE, então
papel não se altera via API.

**As funções auxiliares de RLS devolvem só booleano.** `is_group_member` e
`is_group_owner_or_professor` são `SECURITY DEFINER`, mas o corpo só testa a associação do próprio
`auth.uid()`. Não vazam linha.

**Minimização de dado no fluxo do professor.** A view `group_member_sessions` remove
`data->'reflection'` e o campo `notes` de cada round antes de expor a sessão do aluno. O professor
vê a estrutura do treino, não o que o aluno escreveu. Limitação de finalidade bem feita.

### 🔴 ALTO — A · `group_member_sessions` contorna RLS e aceita escrita

Confirmado por duas fontes independentes: o catálogo e o linter do Supabase, que a marca em nível
**ERROR** (`security_definer_view`).

- `security_invoker=false` → a view roda como `postgres` e **ignora a RLS de `user_sessions`**. O
  único portão é o `WHERE shares_group_as_professor(owner_id)`.
- A view é **auto-updatable**: `is_updatable=YES`, `is_insertable_into=YES`. As colunas `id`,
  `owner_id`, `class_session_id` e `updated_at` são graváveis (`data` não é, por ser expressão).
- **Não tem `WITH CHECK OPTION`.** Num INSERT o Postgres não avalia o `WHERE` da view — a linha vai
  direto para `user_sessions`, como dono da view, sem RLS.
- `anon` e `authenticated` têm **INSERT, UPDATE, DELETE, TRUNCATE** na view.

Leitura está protegida na prática: para `anon`, `auth.uid()` é NULL, o `exists` dá falso e retornam
0 linhas. O problema é escrita:

- **INSERT** — quem tiver a chave anon pública poderia inserir linhas em `user_sessions` com
  `owner_id` arbitrário (fabricar treino na conta de outra pessoa). `data` sairia NULL; `owner_id`
  tem FK para `profiles`, então precisa de um UUID válido — que um professor conhece via
  `group_members`.
- **UPDATE** — sem `WITH CHECK OPTION`, um professor pode alterar `owner_id` das linhas que enxerga,
  reatribuindo a sessão de um aluno a outra pessoa.

> ⚠️ Derivado do catálogo + semântica padrão do Postgres. **Não foi demonstrado contra o endpoint
> real** — a verificação exige uma escrita com a chave anon, que é mutação e precisa de aprovação.
> Receita de reprodução local em §5.

### 🟡 MÉDIO — G · `source='user'` em `technique_nodes` é ambíguo

`technique_nodes` tem `technique_nodes_public_read USING true` — leitura mundial, inclusive `anon`
— e `technique_nodes_user_insert` permite a usuário autenticado inserir com `source='user'`. Hoje:
323 linhas `source='user'` contra 112 `source='library'`.

O problema é que `'user'` significa duas coisas. No servidor, `db/repository.py:261` grava
`source='user'` para **toda técnica vista em partida entrada por admin** — o docstring de
`export/tech_library.py:252` confirma ("every technique register_match has seen"). Mas a política de
RLS deixa o **usuário final do app** inserir com o mesmo marcador.

Consequência: **não existe forma de separar vocabulário entrado por admin de vocabulário digitado
por usuário do app**, e `export/tech_library.py` embarca todos em `@grapplingarch:nodes_library`,
distribuído a todos os usuários.

Atenuantes reais: a tabela **não tem coluna de autor** (nada liga um nó a uma pessoa — é
anonimização por desenho de schema), e a amostra de 323 rótulos é toda de nome de técnica ("Inside
Ashi", "Guarda X Simples", "Estrangulamento Cruzado"). Nenhum dado pessoal hoje.

Fica como **NEEDS-JUDGEMENT**, não violação: é texto livre do usuário numa superfície pública, sem
limite de conteúdo e sem como filtrar a origem.

### 🟡 MÉDIO — K · proteção de senha vazada desligada

O linter aponta `auth_leaked_password_protection` desativado (checagem contra HaveIBeenPwned). É
configuração de painel, não código. Relevante para LGPD por ser medida de segurança de conta.

### ℹ️ Observação funcional (não é falha de privacidade)

`profiles_select_own` é estrito e `group_members` só tem UUIDs — sem coluna de nome. **Um professor
não consegue ler o nome de um aluno pelo banco.** Do ponto de vista de privacidade isso é ótimo; do
ponto de vista de produto, a tela de aula não tem como exibir nomes. Se isso for corrigido depois,
tem que ser um alargamento mínimo e deliberado (ex.: política que deixe membro do mesmo grupo ler
só a coluna de nome de colega), **nunca** um bypass por service-role.

Também não há caminho para promover alguém a `professor` — só o dono do grupo passa em
`is_group_owner_or_professor`. Lacuna funcional, não de segurança.

---

## 2. GrapplingArcApp

### O que está correto

**Nenhum SDK de telemetria, analytics ou crash.** Sem Sentry, Firebase, Amplitude, Segment,
Bugsnag, Datadog, PostHog, Mixpanel. `src/utils/logger.ts` é `console.*` puro, zero rede. A IA no
aparelho (`expo-ai-kit`/Gemma, `src/services/aiKitSafe.ts:43`) roda local — texto de sessão não vai
para API de LLM nenhuma.

Hosts que o app alcança: Supabase, Drive (`appDataFolder`, espaço oculto por usuário), Google
Sign-In, `ibjjf.com`, e o manifesto de OTA da Expo. Só isso.

**Nenhuma tela mostra dado privado de outra pessoa.** Não há leaderboard nem ranking;
`groupService.ts:61` lê só a própria linha de `group_members`; `entitlement.ts:58` e
`proAnalytics.ts:220` leem só a própria linha. O papel `'professor'` existe no tipo mas **zero
consumidores** — a superfície de professor não está implementada no app.

**Backup e export são do usuário para o usuário.** Drive vai para `appDataFolder`
(`driveSyncService.ts:99+`), inacessível a outro app ou pessoa. O export
(`utils/userDataBundle.ts:248`) abre a folha de compartilhamento do sistema, destino escolhido pelo
usuário.

**O compartilhamento não vaza reflexão.** `ShareScreen` renderiza local e captura PNG; o tipo
`ShareableSession` carrega `reflection` (`types/shareTemplates.ts:17`) mas **nenhum componente lê
esse campo** — a prosa exibida é gerada. Vale remover o campo, é uma armadilha.

**O bucket de vídeo está correto.** `session-videos` é `public = false` em produção; escrita só no
próprio prefixo `auth.uid()`; leitura de professor pelo mesmo portão `shares_group_as_professor`
(alembic `0024_gym_groups.py:216-247`, confirmado em prod).

### ✅ A promessa de consentimento **é** cumprida

Uma primeira passagem levantou como violação latente o texto de consentimento em
`JoinGroupSheet.tsx:101` / `pt-BR.ts:1204-1206`:

> "O que seu professor vai ver: seus rounds, técnicas, ELO e os vídeos que você gravar. Não vê sua
> reflexão, nem as notas que você escreve no round."

...porque o app **envia** `reflection` e as notas mesmo assim, e a proteção no servidor não havia
sido localizada. Ela existe: a view `group_member_sessions` (§1) remove exatamente
`data->'reflection'` e o `notes` de cada round, e o bucket de vídeo tem a política de leitura de
professor. **Texto de consentimento, view e política de storage batem um a um.** Não é violação; é
uma das partes mais bem-feitas do sistema.

O ponto que permanece: essa garantia mora **inteiramente no servidor**. Um `select` direto em
`user_sessions` por qualquer caminho futuro que não passe pela view entrega a reflexão.

### 🔴 ALTO — B · rótulos digitados pelo usuário vão para tabela lida pelo mundo

Fecha o ciclo com o achado G. `src/services/graphSync.ts:65-100` monta linhas a partir dos nós do
grafo do usuário com `label: n.label` — **a string exata que a pessoa digitou** — e faz upsert em
`technique_nodes` com `source: 'user'`. Essa tabela é `USING true` para `anon`.

`normalizeLabel` (`graphSync.ts:16-22`) só minúsculiza e tira pontuação: é chave de junção, **não é
anonimizador**. Não há allow-list, moderação, limite de tamanho nem filtro de PII.

Quem digitar "passagem do prof. Carlos", o nome de um adversário, ou o nome de um drill da academia,
publica isso, verbatim, numa tabela legível por qualquer um sem login.

Atenua: a linha não tem coluna de autor — é inligável à pessoa. Não atenua: é texto livre de origem
privada numa superfície pública, e o achado G mostra que nem dá para separar do vocabulário entrado
por admin.

### 🔴 ALTO — C · apagar o perfil não apaga o grafo nem os vídeos

A exclusão de conta é por e-mail, documentada em `GrapplingArc/account-deletion.html` (25/06/2026) —
canal manual, que atende a exigência da Play Store. O problema é o que o `delete` cobre.

Cascatas a partir de `profiles` (verificado em prod): `user_sessions`, `user_sync_meta`,
`user_performance_snapshots`, `groups`, `group_members`, `group_invites`, `class_sessions`.

**Não cobertos:**

- `graphs` — `Graph.owner_id` é UUID **sem FK** (`db/models.py:103`), porque é polimórfico (aponta
  para `athletes.id` ou `profiles.id`). Nada cascateia.
- `graph_edges` — cascateia de `graphs`, que não é apagado.
- `storage.objects` do bucket `session-videos` — storage não tem FK.
- linhas de `technique_nodes` criadas pelo usuário — sem vínculo de autor, **inapagáveis por
  desenho**.

Hoje uma solicitação de exclusão cumprida como "delete do profile" deixa para trás o grafo, as
arestas e os vídeos. E não existe script de exclusão no repo. É eliminação incompleta sob o Art. 18
VI.

### 🟡 MÉDIO — E · sincronização na nuvem não tem consentimento nem interruptor

Não existe toggle de sync em lugar nenhum. Entrar com Google ou e-mail **é** o evento de
consentimento; a partir daí todo save dispara push automático (`sessionThunksLocal.ts:160`) mais uma
varredura em background (`useSyncManager.ts:48`). Compare com o backup do Drive, que **tem**
`toggleAutoBackup`.

O que sobe verbatim em `user_sessions.data` (`sessionSync.ts:180`): `reflection`, `goal`, `title`,
`topicInput`, `topics[]`, `rounds[].itemInput`, `rounds[].entries[].label` e `.setup`.
`stripMediaForSync` (`sessionSync.ts:91-104`) tira **só** referências de mídia — não é anonimizador,
e o docstring "Media is NEVER uploaded" (`sessionSync.ts:8`) é enganoso, já que
`videoUploadQueue.ts:136` sobe o vídeo bruto por outro caminho.

Convidados ficam de fora (`useSyncManager.ts:25`), e tudo é gated por `isSupabaseConfigured()`. Mas
gating de env não é consentimento.

### 🟡 MÉDIO — F · sem link de política de privacidade dentro do app

`privacy.html` e `account-deletion.html` existem no site público, mas grep em `src/`, `app.json` e
`app.config.js` não acha **nenhum** link para eles. Zero ocorrências de `lgpd` no repo inteiro. O
usuário não tem como chegar na política nem no canal de exclusão de dentro do app.

### 🟢 BAIXO — L · `.env` versionado

`GrapplingArcApp/.env` está rastreado no git e não ignorado, com `EXPO_PUBLIC_SUPABASE_URL` e
`EXPO_PUBLIC_SUPABASE_ANON_KEY` preenchidos (desde `2ef60f6`). `CLAUDE.md:232` afirma o contrário
("No env in repo") — documentação desatualizada.

Severidade baixa **isolada**: a chave é `sb_publishable_…`, projetada para ser pública e que já vai
embutida em todo binário instalado; quem protege é a RLS. Mas **compõe com o achado A**: a chave
anon é exatamente a credencial que aceita INSERT na view `group_member_sessions`, e ela é pública
por natureza — o ataque não depende do repo, basta ter o app instalado.

Menor: `API_BASE` morto em `SessionsFeed.tsx:21` e `graphThunksLocal.ts:19`, alimentado por
`EXPO_PUBLIC_API_URL` que ainda está no `.env`. Sem egress hoje; é arma carregada.

---

## 3. GrapplingArcWeb

**Está no ar.** `main @ bbe16d5` publicado em GitHub Pages, HTTP 200 público
(`https://themissenoone.github.io/GrapplingArcWeb/`). Repo é privado, site não é — o que é a forma
correta para uma SPA com login, mas convém não confundir as duas coisas.

Contrapeso: `groups`, `group_members`, `group_invites` e `class_sessions` têm **0 linhas** em
produção. Todo o fluxo de professor nunca rodou com dado real.

### O que está correto

**Nenhum caminho privado → competitivo. Nem por acidente.** Nenhuma query toca `matches`,
`athletes` ou `published_athlete_graphs`; a única menção a `owner_kind` no repo inteiro é
`graph.ts:157`, fixada em `'user'`. O repo não escreve em `graphs`, `graph_edges` nem
`user_sessions`.

**PKCE em vez de implicit flow** (`supabase.ts:31`) — o token nunca cai numa URL.

**Chave publicável, sem service-role.** Verificado no bundle vivo: `sb_publishable_…`, ligada à RLS.
Sem JWT de service-role no repo, no `dist/` ou no bundle. `README.md:79` e `deploy.yml:7` trazem
aviso escrito de nunca adicionar uma.

**Só `/login` é público**; tudo mais passa por `<Protected>`. Sem SSR/SSG, sem prerender — o build
não tem como embutir dado real, e `dist/` é gitignored.

**QR gerado localmente** (`QrCode.tsx:20`) — o token da aula não vai para nenhum serviço de imagem
de terceiro.

**O grafo vendorizado não envia nada.** `src/vendor/graph.js` é byte-idêntico ao do site público e
não tem uma única ocorrência de `fetch`/`WebSocket`/`localStorage`/`sendBeacon`. Recebe dado
privado, renderiza em canvas, fim.

**A cadeia de acesso do professor está bem desenhada.** O QR da aula **não** concede acesso — ele só
deixa o aluno anexar a própria sessão. O acesso vem de associação ao grupo, resolvido por
`shares_group_as_professor`, que não aceita grupo vindo do chamador. E `ClassLive` usa `head:true`
para contar sem trazer corpo de linha (`classes.ts:105`).

### 🔴 ALTO — D · sessão apagada continua visível para o professor

O achado que só aparece cruzando os dois repos. Os dois lados foram verificados diretamente.

1. O tombstone do App faz upsert **sem** o campo `data` (`sessionSync.ts:216-221`), então o `ON
   CONFLICT` **preserva o `data` no servidor**.
2. O trigger de proteção (`0019_user_sessions_delete_guard.py:58-72`) só compara `updated_at` — não
   limpa nada.
3. A view `group_member_sessions` **não filtra `deleted_at` e nem expõe a coluna** — confirmado por
   consulta direta: a string `deleted_at` não aparece na definição da view.

A leitura do próprio aluno esconde certo (`sessions.ts:81`, `.is('deleted_at', null)`). A do
professor (`students.ts:55`) **não tem como filtrar em camada nenhuma**. O aluno apaga a sessão no
app e o professor continua vendo título, objetivo, rounds, posições, resultados — e o vídeo assinado
do round.

É dado privado permanecendo visível a terceiro depois que o titular o retirou. Art. 18 da LGPD.

> **Latente, não ativo:** medido em produção — 0 tombstones hoje, e 0 relações de professor. A
> correção é uma linha (`and us.deleted_at is null` na view), e pertence ao Analytics: o Web não
> pode consertar isso, porque a coluna nem é exposta.

### 🟡 MÉDIO — I · `profiles.display_name` não existe

`students.ts:47` faz `profiles(display_name)`. As colunas de `profiles` são `id, full_name,
belt_rank, belt_degrees, is_guest, archetype_id, created_at, updated_at, is_pro`. **Não há
`display_name`** — a coluna real é `full_name`. O PostgREST devolve 400, e `/students` renderiza
erro.

E mesmo com a coluna certa não funcionaria: `profiles_select_own` é a **única** política de SELECT
em `profiles`, então o join embutido voltaria `null` para todo mundo menos o próprio professor.

Falha fechada — não é vazamento. Mas a lista de alunos não funciona, e a correção (política de
leitura de professor em `profiles`) é justamente uma leitura entre usuários que merece o mesmo
cuidado que `group_member_sessions` recebeu: escopo por `shares_group_as_professor` e **só a coluna
de nome**, nunca `select *`.

### 🟡 MÉDIO — H · Google Fonts

`index.html:7-9` carrega Archivo / Newsreader / Spline Sans Mono de `fonts.googleapis.com`. Nenhum
dado da aplicação sai — não viola a regra como escrita. Mas o **IP e o User-Agent** de todo aluno e
professor chegam ao Google a cada carregamento, sem consentimento, num contexto de treino/saúde.
Tribunais alemães já consideraram esse padrão ilícito sob o GDPR; sob a LGPD é, no mínimo, uma
transferência não informada. Hospedar os três WOFF2 localmente elimina o egress e ainda tira um
round-trip bloqueante. É o único host externo que sobra no produto.

### 🟡 MÉDIO — J · leituras de `user_sessions` sem filtro de dono no cliente

`sessions.ts:78-83` e `:90-95` selecionam sem predicado de `owner_id`. Protegidas **só** pela RLS
`user_sessions_owner_all` — confirmada ativa em produção, sem drift em relação à migração. A
dependência é deliberada e documentada em `sessions.ts:11`.

Não é violação. É camada única: se aquela política cair ou um grant derivar, `fetchMySessions` vira
"selecione todas as sessões do banco" sem rede de proteção no cliente. `graph.ts:154-159` mostra que
este repo sabe fazer defesa em profundidade — `sessions.ts` só não faz. Uma linha
(`.eq('owner_id', user.id)`) remove o ponto único de falha.

Mesma observação para `class_sessions` (`:86`), `group_invites` (`:75`, `:99`) e a contagem em
`classes.ts:105`.

### 🟢 BAIXO — M · dois defeitos funcionais que falham fechado + um teste tautológico

- **`created_by NOT NULL` sem default:** `class_sessions.created_by` (`0026:102`) e
  `group_invites.created_by` (`0024:82`) são obrigatórios sem default, e nem `createClass`
  (`classes.ts:58`) nem `createInvite` (`groups.ts:88`) preenchem. Os inserts devem falhar.
  Consistente com as tabelas estarem zeradas. (Inferido do schema, não executado.)
- **Porta de convite oferecida a professor, mas a política é só do dono:** `Group.tsx:151` mostra
  `InviteDoor` para `owner` ou `professor`; `group_invites_owner_all` (`0024:176`) exige
  `groups.owner_id = auth.uid()`. O professor vê o botão e toma erro de RLS. Decidir qual lado está
  certo — a UI ou a política.
- **O "teste de contrato de privacidade" não testa nada:** `sessions.ts:71` diz que
  `PRIVATE_SESSION_FIELDS` é "usado por um teste contra o payload do professor". O único consumidor
  é `src/test/sessions.test.ts:66-69`, que afirma `[...PRIVATE_SESSION_FIELDS]).toEqual(['reflection','notes'])`
  — uma tautologia que repete a constante declarada uma linha acima. **Nenhum teste verifica que o
  payload do professor não traz esses campos.** A remoção é real (a view faz), então não é vazamento
  vivo; mas o CI (`npm test`, gate do deploy) não pegaria a regressão.

---

## 4. Placar consolidado

| # | Achado | Onde | Sev. | Estado |
|---|---|---|---|---|
| A | View do professor aceita INSERT/UPDATE contornando RLS | DB | 🔴 | latente, não provado contra o endpoint |
| B | Rótulo digitado pelo usuário vai para tabela lida por `anon` | App + DB | 🔴 | **ativo** — 323 linhas |
| C | Apagar perfil não apaga grafo, arestas nem vídeos | App + DB | 🔴 | ativo |
| D | Sessão apagada continua visível ao professor | Web + App + DB | 🔴 | latente (0 tombstones, 0 professores) |
| E | Sem consentimento nem interruptor de sync na nuvem | App | 🟡 | ativo |
| F | Sem link de política de privacidade dentro do app | App | 🟡 | ativo |
| G | `source='user'` não separa admin de usuário final | DB | 🟡 | ativo |
| H | Google Fonts expõe IP/UA de aluno e professor | Web | 🟡 | ativo, site no ar |
| I | `profiles.display_name` não existe | Web | 🟡 | falha fechada |
| J | `user_sessions` sem filtro de dono no cliente | Web | 🟡 | camada única |
| K | Proteção de senha vazada desligada | Supabase | 🟡 | ativo |
| L | `.env` versionado (chave publicável) | App | 🟢 | compõe com A |
| M | `created_by NOT NULL`, convite só do dono, teste tautológico | Web | 🟢 | falham fechado |

**Três coisas que a auditoria confirmou estarem certas e que não devem ser "consertadas" por engano
depois:**

1. O texto de consentimento do JoinGroupSheet, a view `group_member_sessions` e a política
   `session_videos_professor_read` **batem um a um**. O professor vê rounds, técnicas e vídeo; não
   vê reflexão nem notas.
2. `security_invoker=false` em `group_member_sessions` é **deliberado e documentado**
   (`0027:16-35`). O linter do Supabase marca como ERROR; a exceção é consciente. O problema ali não
   é o definer — é a **ausência de `WITH CHECK OPTION` e os grants de escrita**.
3. `join_group` fixa `role='student'` e `group_members` não tem política de INSERT/UPDATE. Não há
   caminho de auto-promoção a professor. Não afrouxar isso sem pensar.

---

## 5. Verificação do achado A — Postgres local, zero toque em produção

Num Postgres efêmero (container ou `initdb` em tmp), recriar o mínimo que reproduz a semântica:

1. `profiles`, `user_sessions`, `group_members` com as mesmas colunas e FKs.
2. RLS ligada em `user_sessions` com `user_sessions_owner_all`.
3. `shares_group_as_professor` como `SECURITY DEFINER`.
4. A view `group_member_sessions` **exatamente** como está em produção — mesmo corpo,
   `security_invoker=false`, `security_barrier=true`, sem `WITH CHECK OPTION`.
5. Papéis `anon` e `authenticated` com os mesmos grants (incluindo INSERT/UPDATE/DELETE na view), e
   `auth.uid()` mockada como função que devolve NULL para simular `anon`.

Então, como `anon`:

```sql
INSERT INTO group_member_sessions (id, owner_id, updated_at)
VALUES ('probe-1', '<uuid de profile existente>', now());
SELECT count(*) FROM user_sessions WHERE id = 'probe-1';   -- como superusuário
```

- **1 linha** → o INSERT atravessa a view e ignora a RLS. Achado A confirmado; R1 vira urgente.
- **erro de permissão / 0 linhas** → a exposição é teórica. R1 continua valendo (revogar grant que
  ninguém usa é barato), com prioridade menor.

Vale rodar o mesmo INSERT com `WITH CHECK OPTION` aplicado, para confirmar que a correção
alternativa também fecha o buraco — assim a decisão entre "revogar grants" e "adicionar check
option" fica baseada em teste, não em leitura.

**Status: não executado.** O resultado entra aqui como linha de evidência quando rodar.

---

## 6. Remediações candidatas (não executadas nesta rodada)

Ordenadas por risco × custo. Toda mudança de policy/view é migração alembic e mutação de prod —
**humano/orquestrador**, nunca subagente, conforme `change-control`. Mudança que cruza App e
Analytics são **dois PRs** que se referenciam.

**Uma migração alembic resolve quatro achados de uma vez** (A, D, e metade de I):

| # | O quê | Achado | Repo |
|---|---|---|---|
| R1 | `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON group_member_sessions FROM anon, authenticated` (a view só precisa de SELECT) | A | Analytics |
| R2 | Adicionar `and us.deleted_at is null` à view `group_member_sessions` | D | Analytics |
| R3 | Política de leitura de professor em `profiles`, escopada por `shares_group_as_professor` e **só na coluna de nome** | I | Analytics |
| R4 | Corrigir `students.ts:47` para `full_name` | I | Web |

Depois, por ordem de urgência:

| # | O quê | Achado |
|---|---|---|
| R5 | Não subir `reflection` e notas de round, ou movê-las para tabela só-do-dono. Ponto único: `stripMediaForSync` (`sessionSync.ts:91`) — é o que o consentimento já promete | E |
| R6 | Rotina de exclusão de conta que cubra `graphs` + `graph_edges` + objetos do bucket, já que `Graph.owner_id` não tem FK | C |
| R7 | Barrar texto livre em `technique_nodes` a partir do app: vocabulário curado, ou publicar só a chave normalizada sem `label` | B |
| R8 | Separar origem em `technique_nodes` (`'match'` vs `'app_user'`) e decidir se app_user entra no export da biblioteca | G |
| R9 | Link para `privacy.html` e `account-deletion.html` dentro do app + consentimento/interruptor explícito de sync | E, F |
| R10 | Auto-hospedar os três WOFF2 | H |
| R11 | Ligar leaked-password protection no painel | K |
| R12 | `.eq('owner_id', …)` nas duas leituras de `user_sessions` (defesa em profundidade) | J |
| R13 | Teste real de fronteira: mock de `group_member_sessions` afirmando que `reflection` e `notes` não sobrevivem a `fetchStudentSessions` | M |
| R14 | Destrancar `.env`, corrigir `CLAUDE.md:232`, remover `API_BASE` morto e `reflection` de `ShareableSession` | L |

---

## 7. O que ficou por confirmar

Dito explicitamente, para ninguém tratar como verificado:

- **A** não foi demonstrado contra o endpoint real — é catálogo + semântica do Postgres (§5).
- **`created_by NOT NULL`** foi inferido do schema, não executado.
- A política do bucket `session-videos` foi lida e confirmada em produção; o restante do storage não
  foi auditado.
- O repo `GrapplingArc` (site público) não foi auditado nesta rodada — ele é gerado do corpus
  público e o `export/*` já filtra `owner_kind`, mas isso é inferência do lado Analytics, não
  leitura do bundle gerado.
