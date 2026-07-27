# GrapplingArc — Estado atual do ecossistema (GA-000)

Data: **2026-07-27**. Baseline técnico consolidado dos três repositórios, levantado antes de
qualquer alteração de código. Tudo aqui foi **medido**, não estimado; os comandos estão citados.

Relatórios por repositório:

- `GrapplingArcApp/audit/2026-07-27-current-state.md`
- `GrapplingArcAnalytics/audit/2026-07-27-current-state.md`
- `GrapplingArc/audit/2026-07-27-current-state.md`

---

## 1. Placar dos gates

| Gate | App | Analytics | Site |
|---|---|---|---|
| CI existe | 🔴 nenhum | 🟢 sim | 🔴 só deploy |
| CI dispara em PR | 🔴 | 🟢 | 🔴 |
| Testes verdes | 🟢 134 suítes / **975 testes** / 24 s | 🟢 **495 testes** / 40 s | ⚪ não há |
| Lint no gate | 🔴 sem script (65 erros) | 🟡 só 7 caminhos (100 erros ocultos) | 🔴 |
| Typecheck no gate | 🔴 sem script (mas **verde**) | 🔴 mypy strict nunca rodou (322 erros) | ⚪ |
| Formatação no gate | ⚪ | 🔴 205/283 arquivos fora do padrão | ⚪ |
| Migrations testadas | ⚪ | 🔴 nenhuma | ⚪ |
| Validação antes de publicar | ⚪ | 🔴 nenhum portão de dados | 🔴 nenhuma |
| Branch principal única | 🔴 default 309 commits atrás | 🟢 | 🟢 |
| `.env` fora do versionamento | 🔴 **`.env` rastreado**, `.gitignore` só cobre `.env*.local` | 🟢 nada rastreado, ignore correto | 🟢 |
| Admin seguro | ⚪ | 🔴 senha padrão `changeme` | ⚪ |
| Paridade de contrato testada | 🔴 | 🔴 | 🔴 |

---

## 2. Os seis achados que mais importam

### 2.1 O App não tem CI nenhum

`.github/` não existe no `GrapplingArcApp`. Nada impede merge com teste vermelho, tipo quebrado ou
lint sujo. É o maior repositório dos três em superfície de código e o único sem portão.

A boa notícia é que a base está **melhor do que a documentação sugere**: 975 testes verdes,
`tsc --noEmit` sai 0, e os 65 erros de ESLint são todos da mesma família trivial
(`no-unused-vars`). O CI pode nascer verde.

### 2.2 A branch default do App aponta para código de 309 commits atrás

`origin/HEAD` → `master`; o trabalho vive em `development`.

```
$ git log --left-right --cherry-pick --oneline origin/master...origin/development
5 <   |   309 >
$ git diff --stat origin/master...origin/development
760 files changed, 124432 insertions(+), 33634 deletions(-)
```

Os 5 commits só-do-`master` já foram superados em `development` (detalhe em
`GrapplingArcApp/audit/2026-07-27-branch-reconciliation.md`). Além disso, **`development` local está
14 commits à frente de `origin/development`** — trabalho não pushado.

Qualquer contribuidor que clone o repo hoje recebe, por padrão, uma versão obsoleta do produto.

### 2.3 O admin do Analytics aceita senha padrão

`admin/auth.py:11` — `_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")`.

Somado a: sessão em dict de processo sem TTL (`auth.py:10`), cookie sem `Secure` e sem `Max-Age`
(`server.py:211`), zero CSRF, zero rate limiting, zero auditoria. E a checagem de autenticação está
**copiada inline em ~30 rotas** (`server.py:225, 246, 264, …`) em vez de centralizada — o que
significa que toda rota nova precisa lembrar de repetir o padrão, e adicionar CSRF exigiria editar
30 lugares.

É o painel que publica grafos de atletas em produção.

### 2.4 97% das páginas do site não geram preview de compartilhamento

Medido sobre as 806 páginas de `site/`:

| Situação | Páginas |
|---|---|
| `og:image` aponta para arquivo inexistente | **733** |
| `og:image` aponta para SVG (unfurlers ignoram) | 47 |
| **`og:image` utilizável** | **26** |

São 390 arquivos `site/assets/fighters/<slug>.jpg` faltando, sem nenhum fallback. Para um produto
cujo canal de aquisição é compartilhamento social, essa é a falha de maior alcance do ecossistema.

### 2.5 O App versiona o `.env` e o `.gitignore` não o protege

`GrapplingArcApp` rastreia `.env` em `master` **e** em `development` (5 commits o tocaram, desde o
commit inicial). O `.gitignore:34` cobre `.env*.local` — **não cobre `.env`**.

As 6 chaves de hoje têm prefixo `EXPO_PUBLIC_*`, que por definição do Expo são embutidas no bundle do
cliente e portanto públicas (client IDs OAuth, URL do Supabase, chave anon protegida por RLS). Não é
vazamento que exija rotação imediata.

O risco é o guarda-corpo: no dia em que uma chave de verdade entrar nesse arquivo, ela será commitada
**em silêncio**, e não há secret scanning para pegar depois. O Analytics já faz certo — nenhum `.env`
rastreado e o ignore cobre o caso.

### 2.6 Não existe fonte única de contratos

Os módulos conversam por seis contratos mantidos **por disciplina humana**, sem verificação
automática nenhuma:

| Contrato | App | Analytics |
|---|---|---|
| Bundle do usuário | `src/data/mockData/mock_user_bundle.json` | `schemas/app_types.py` (177 l., dataclasses escritas à mão) |
| Biblioteca de técnicas | `@grapplingarch:nodes_library` | `export/tech_library.py` |
| Tabela ELO | `@grapplingarch:elo_stats` | `export/adcc_elo_table.py` |
| Matemática do ELO | `eloService.ts` + `sequenceScorer.ts` | `analysis/elo_calibration.py` |
| **Node key** | `normalizeLabel()` em `graphSync.ts` (**sem teste direto**) | `analysis/names.py:_normalize_name` |
| Arestas dirigidas | `services/directedEdges.ts` | `analysis/network_metrics.py` |
| Bundle do site | `site/*-data.js` + `site/graph.js` | `export/site_data.py` |

O par mais crítico — `normalizeLabel` × `_normalize_name`, que precisa casar **caractere a
caractere** ou o grafo sincronizado quebra — não tem teste em nenhum dos dois lados. `graphSync.ts`
tem 5 importadores de produção e zero cobertura direta.

Não existe diretório `contracts/`, nenhum JSON Schema, nenhuma fixture compartilhada.

---

## 3. Números medidos, por repositório

### GrapplingArcApp

```
$ npx jest --ci --runInBand      →  134 suítes, 975 testes, todos passando, 24,02 s
$ npx tsc --noEmit               →  exit 0, zero erros
$ npx tsc --noEmit --strict      →  52 erros
$ npx eslint .                   →  65 erros, 28 warnings (todos no-unused-vars)
```

- `tsconfig.json` tem 4 linhas e herda `expo/tsconfig.base` — `strict`, `noImplicitAny` e
  `noUncheckedIndexedAccess` estão **todos desligados**, apesar de `CLAUDE.md`/`AGENTS.md` afirmarem
  o contrário.
- 559 usos de `any` (249 `as any`); **zero** `@ts-ignore`.
- Ligar `strict` custa **52 erros**, não centenas — 5 deles somem só instalando `@types/react-dom`.
- 29 arquivos acima de 400 linhas; o maior é `src/screens/ShareScreen.tsx` (1428).
- ESLint e Playwright estão configurados e instalados, mas **nenhum script npm os invoca**.
- A compilação inclui 15 mockups de `design_handoff_grapplingarc_redesign/design-files/*.jsx`.

### GrapplingArcAnalytics

```
$ uv run pytest -q               →  495 testes passando, 39,54 s
$ uv run ruff check .            →  100 erros (99 em scripts/, 1 em export/), 50 auto-corrigíveis
$ uv run ruff format --check .   →  205 de 283 arquivos seriam reformatados
$ uv run mypy .                  →  bloqueia no 1º erro (módulo duplicado em scripts/)
$ uv run mypy . --exclude scripts/ →  322 erros em 49 arquivos (187 checados)
$ uv run alembic heads           →  head único: 0021
```

- O CI roda `ruff check` em **7 caminhos hardcoded** (`ci.yml:22`) e **nunca roda mypy**, apesar de
  `strict = true` no `pyproject.toml`.
- Dos 49 arquivos sujos no mypy, **32 são de `tests/`** — o código de produção tem só 16 módulos
  sujos (14 em `analysis/`, 2 em `export/`), então um gate útil é barato.
- Dos 100 erros de ruff, **99 estão em `scripts/`**. O código de produção está limpo.
- 21 migrations, cadeia linear, head único — mas **nenhuma é testada** e o schema usa `pgvector(768)`,
  logo um smoke test exige Postgres com pgvector (SQLite não serve).
- `.env.example` lista 5 chaves; o `.env` real tem 11. Faltam `SUPABASE_*`, `DATABASE_URL`,
  `ADMIN_PASSWORD`. Quem clona não consegue subir admin nem publisher pela documentação.
- Não existe `conftest.py`, nem `.pre-commit-config.yaml` (embora `pre-commit` seja dependência).
- `.venv` local roda Python 3.14.6; o CI fixa 3.12.

### GrapplingArc (site)

```
806 páginas .html   |   17.832 links internos verificados, 0 quebrados
canonical absoluta: 806/806        og:image utilizável: 26/806
```

- `deploy.yml` só faz `jekyll-build-pages`. **Nenhuma validação, nenhum job em PR.**
- Zero ferramental de qualidade no repo (sem `package.json`, `Makefile`, testes ou pre-commit).
- Existe um **sexto** global de dados não documentado: `GA_FEATURED` (em `breakdowns-data.js`).
- Contagens hardcoded em `index.html:132-134`. Duas conferem hoje (674, 85); a terceira (693
  "fighters tracked") **não é derivável de nenhum arquivo do repo**.
- Waitlist é `mailto:` para um endereço placeholder (`index.html:233`).
- `_config.yml` não tem `include:`, então **689 JSONs legados** de `assets/` (312 matches + 377
  fighters) e o `opencode.json` são publicados no Pages a cada deploy.

---

## 4. Dívida de documentação

Cada linha abaixo é uma afirmação de `CLAUDE.md`/`AGENTS.md` que o código contradiz. **Este PR não
corrige nada disso** — só registra. Cada uma vira um ticket próprio.

| Repo | Doc afirma | Realidade |
|---|---|---|
| App | "TS strict. No `any`, no `@ts-ignore`" | `strict` desligado; 559 `any` |
| App | "180+ violations" de `any` | 249 `as any` / 559 total |
| App | "31 test files / 216 tests" (`CLAUDE.md`) e "31 suites / 239 tests" (`AGENTS.md`) | **134 suítes / 975 testes** |
| App | "No tests on any supabase service" | 4 dos 9 já têm teste (`sessionSync`, `syncEngine`, `syncBootstrap`, `googleSupabaseBridge`); 5 seguem sem (`graphSync`, `proGraphs`, `supabaseAuth`, `supabaseClient`, `accountUpgrade`) |
| App | pro-graph reader "BUILT BUT UNWIRED" | `proAnalytics.ts` e `store/proAnalyticsThunksLocal.ts` já o consomem |
| App | lista de arquivos grandes termina em `ProjectsScreen` (1088) | `ShareScreen.tsx` tem 1428 |
| Site + raiz | 5 globais `*-data.js` | são 6 (falta `GA_FEATURED`) |
| Site | `assets/` legado é peso morto | continua **publicado** a cada deploy |
| Analytics | `README.md:11` só cita chaves do Kaggle | faltam 6 variáveis obrigatórias |

---

## 5. Riscos operacionais no momento do levantamento

| Risco | Detalhe |
|---|---|
| Trabalho não pushado | `GrapplingArcApp` `development` local: **14 commits à frente** do remoto |
| Working tree suja | `GrapplingArcAnalytics` tem 4 caminhos untracked (`systemd/`, `tests/test_pro_analytics_systemd.py`, 2 docs) — **nunca `git add -A`** neste repo |
| Muito trabalho em voo | 7 worktrees pré-existentes em `.worktrees/` — reformatação em massa (ex.: `ruff format .` em 205 arquivos) causaria conflito generalizado |
| `gh` não autenticado | proteção de branch, secret scanning e Dependabot não podem ser configurados deste ambiente |

---

## 6. Armadilhas encontradas ao medir (para quem for automatizar)

1. **Extrair links de HTML sem remover `<script>` produz falso positivo.** As páginas
   `grapple-*.html:163,182` montam `'breakdown-'+ref.slug+'.html'` em JS; um regex ingênuo reporta
   170 links quebrados para um alvo `breakdown-` que não existe. Com os blocos `<script>` removidos:
   17.832 links, **zero** quebrados.
2. **`mypy .` para no primeiro erro de resolução de módulo** e não conta nada. `scripts/` tem arquivo
   resolvido sob dois nomes de módulo; sem excluí-lo, a contagem real (322) fica invisível.
3. **A contagem de `any` não prevê o custo do `strict`.** 559 `any` sugerem catástrofe; o número real
   é 52 erros — justamente porque cada `any` silencia o strict onde está.

---

## 7. Ordem recomendada (Wave 1)

1. **GA-000** — este documento.
2. **GA-001** — reconciliar `master`/`development` no App. Barato: `development` é estritamente superior.
3. **GA-002** — CI do App. Pode nascer verde depois de limpar 65 `no-unused-vars`.
4. **GA-003** — completar o CI do Analytics: `ruff check .` no repo todo, mypy gateado no código de
   produção, head único do alembic, `upgrade head` contra Postgres+pgvector.
5. **GA-004** — validador do site em Python stdlib + job `validate` antes do deploy.
6. **GA-005** — hardening do admin, com middleware único de auth+CSRF substituindo os ~30 checks inline.

Fora da Wave 1, mas já dimensionado por este levantamento: ligar `strict` no App é um PR de 52
erros, e consertar as `og:image` (GA-027) é o item de maior retorno visível do site.
