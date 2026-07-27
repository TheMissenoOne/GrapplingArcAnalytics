# GrapplingArcAnalytics — Estado atual (baseline GA-000)

Data: 2026-07-27. Levantamento read-only, nenhuma alteração funcional, nenhuma conexão com o banco
de produção. Relatório consolidado: `docs/ecosystem-current-state.md`.

---

## 1. Branch e árvore de trabalho

| Item | Valor |
|---|---|
| Branch default (`origin/HEAD`) | `main` ✅ |
| Branch atual | `main` |
| Working tree | **suja** — 4 caminhos untracked |

Untracked no momento do levantamento:

```
docs/PRO_ANALYTICS_LOCAL_PUBLISHER.md
docs/superpowers/plans/2026-07-17-video-engine-review-pipeline.md
systemd/
tests/test_pro_analytics_systemd.py
```

Consequência operacional: **nunca usar `git add -A` neste repo** enquanto esses arquivos estiverem
soltos — só caminhos explícitos.

---

## 2. CI — o que existe

`.github/workflows/` tem 2 arquivos, ambos introduzidos no mesmo commit
(`6ffd051 feat(pro): add batch analytics contract`).

### `ci.yml` (22 linhas)

- Triggers: `pull_request: [main]` + `push: [main]`. `permissions: contents: read`.
- Passos: checkout@v4 → `astral-sh/setup-uv@v5` (Python 3.12) → `uv sync --all-extras` → `uv run pytest`
  → `uv run ruff check` **em 7 caminhos hardcoded** (`ci.yml:22`):

```
analysis/pro_analytics.py jobs db/models.py alembic/versions/0021_pro_analytics.py
tests/test_pro_analytics.py tests/test_pro_analytics_models.py tests/test_publish_pro_analytics.py
```

- **Não roda `ruff check .`**. **Não roda `ruff format --check`**. **Não roda `mypy` em lugar nenhum.**
- Não testa migrations. Não verifica head único do alembic. Não faz smoke de import.

### `publish-pro-analytics.yml` (46 linhas)

- `workflow_dispatch` apenas, `environment: production`, consome `secrets.PROD_DATABASE_URL` (`:40`),
  roda `jobs.publish_pro_analytics`. Publicação Pro é 100% manual hoje (GA-034).

### Proteção de branch

Não verificável deste ambiente (`gh` não autenticado). Nada no repo declara checks obrigatórios.

---

## 3. Ferramentas configuradas × ferramentas executadas

`pyproject.toml` configura três linters e o CI usa um e meio.

| Ferramenta | Config | Executado no CI? | Resultado medido em 2026-07-27 |
|---|---|---|---|
| pytest | `testpaths = ["tests"]`, sem addopts/coverage | ✅ | **495 testes passando, 39,54 s** |
| ruff (lint) | `py312`, `line-length = 100`, `select = ["E","F","I","N","W","UP"]`, `extend-exclude = ["notebooks","transcripts"]` | 🟡 só 7 paths | `ruff check .` → **100 erros**, 50 auto-corrigíveis |
| ruff (format) | mesmo config | ❌ | `ruff format --check .` → **205 de 283 arquivos seriam reformatados** |
| mypy | **`strict = true`**, `warn_unused_ignores`, `ignore_missing_imports`, `exclude = ["notebooks/"]` | ❌ **nunca rodou** | ver abaixo |
| pre-commit | declarado em dev-deps | ❌ | **não existe `.pre-commit-config.yaml`** |

### Onde os 100 erros de ruff estão

```
99 em scripts/
 1 em export/
```

Ou seja: o código de produção (`analysis/`, `db/`, `admin/`, `harvest/`, `jobs/`, `realtime/`,
`schemas/`, `grapplemap/`, `tests/`) está **essencialmente limpo** — a sujeira toda mora em
`scripts/`, que é onde vivem os dumps e utilitários ad-hoc.

Distribuição por regra:

| Regra | Qtd | Auto-corrigível |
|---|---|---|
| I001 unsorted-imports | 41 | ✅ |
| E501 line-too-long | 37 | ❌ |
| E402 module-import-not-at-top | 5 | ❌ |
| F401 unused-import | 4 | ✅ |
| F841 unused-variable | 3 | ❌ |
| N806 non-lowercase-variable-in-function | 3 | ❌ |
| E401 multiple-imports-on-one-line | 2 | ✅ |
| F541 f-string-missing-placeholders | 2 | ✅ |
| E722 bare-except | 1 | ❌ |
| E741 ambiguous-variable-name | 1 | ❌ |
| W291 trailing-whitespace | 1 | ✅ |

### Custo real do `ruff format`

**205 de 283 arquivos** seriam reformatados. Formatar tudo de uma vez é um commit mecânico gigante
que colidiria com as ~7 branches/worktrees em voo. Decisão registrada: `ruff format --check .`
**não entra no gate agora**; entra escopado ao diff do PR ou depois que as branches em voo drenarem.

### mypy — primeiro erro bloqueia a contagem

`uv run mypy .` nem chega a contar:

```
scripts/insert_ufc_matches.py: error: Source file found twice under different module names:
"insert_ufc_matches" and "scripts.insert_ufc_matches"
Found 1 error in 1 file (errors prevented further checking)
```

Excluindo `scripts/`, o número real aparece:

```
$ uv run mypy . --exclude 'scripts/'
Found 322 errors in 49 files (checked 187 source files)
```

**138 dos 187 arquivos já passam em `strict`.** Onde estão os 49 sujos:

| Área | Arquivos com erro |
|---|---|
| `tests/` | **32** |
| `analysis/` | 14 (`archetype`, `athlete_elo`, `athlete_systems`, `defense_rate`, `fighter_similarity`, `gnn_predictor`, `graph_comparison`, `grappling_map`, `match_deviance`, `metric_evaluation`, `ocean`, `ufc_elo_engine`, `user_insights`, `user_profile`) |
| `export/` | 2 (`grapplemap_icons_export`, `site_data`) |
| `scripts/` | 1 (`dump_import`) |

Conclusão prática: o código de produção tem só **16 módulos sujos**. Um gate de mypy que exclua
`tests/` e liste esses 16 como dívida explícita já protege ~90% do código hoje.

### Divergência de versão do Python

`pyproject.toml:8` declara `requires-python = ">=3.12"`; o CI fixa **3.12**; a `.venv` local roda
**Python 3.14.6**. Local e CI testam interpretadores diferentes.

---

## 4. Segurança do admin — achados

App FastAPI: `admin/server.py` (952 linhas) + `admin/auth.py` (39 linhas).

| # | Problema | Evidência |
|---|---|---|
| 1 | **Senha padrão `changeme`** | `admin/auth.py:11` — `_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")` |
| 2 | **Senha em texto puro** | comparação por `secrets.compare_digest` (`auth.py:39`) é constant-time, mas a senha vem crua da env — sem hash |
| 3 | **Sessão sem expiração** | `admin/auth.py:10` — `_SESSIONS: dict[str, bool] = {}`; dict de processo, sem TTL, sem poda |
| 4 | **Cookie sem `Secure`, sem `Max-Age`** | `admin/server.py:211` — `resp.set_cookie(_COOKIE_NAME, token, httponly=True, samesite="lax")` |
| 5 | **Zero CSRF** | nenhuma ocorrência de `csrf`/`CSRF` no repo; POSTs são `Form(...)` puros, com `samesite=lax` como única mitigação |
| 6 | **Zero rate limiting** | nenhuma ocorrência de `rate_limit`/`ratelimit`/`slowapi` |
| 7 | **Zero trilha de auditoria** | nenhuma ocorrência de `audit` |
| 8 | **~30 checks de auth copiados inline** | `if not is_authenticated(request): return RedirectResponse(...)` em `server.py:225, 246, 264, 273, 331, 378, 432, 484, 498, 527, 558, 583, 593, 604, 618, 639, 650, 661, 680, …` |
| 9 | `require_auth` existe e não é usado | `admin/auth.py:15` — dependency morta |

Rotas de login: `server.py:196` (GET), `server.py:200-212` (POST); logout apaga o cookie em `:219`.
O app é criado com `docs_url=None, redoc_url=None` (`server.py:187`) — OpenAPI já está desligado.

O item 8 é a raiz do item 5: com a checagem copiada em cada rota, adicionar CSRF significaria editar
30 lugares e lembrar de fazê-lo em toda rota nova. Um middleware único resolve os dois.

Cobertura de teste atual: `tests/test_admin.py` — **4 casos** (`:41` renderiza login, `:53` senha
errada, `:65` redirect sem auth, `:76` página autenticada).

---

## 5. Segredos e ambiente

```
$ git ls-files | grep -i env
.env.example
alembic/env.py
web/src/vite-env.d.ts
```

**Nenhum `.env` rastreado.** `.gitignore:11-12` cobre `.env` e `.env.local`. Não há segredo commitado
a rotacionar.

### `.env.example` está defasado

| | Chaves |
|---|---|
| `.env.example` (13 linhas) | `KAGGLE_USERNAME`, `KAGGLE_KEY`, `ROBOFLOW_API_KEY`, `VICOS_DOWNLOAD_BASE`, `LOG_LEVEL` |
| `.env` real (local, untracked) | as acima **+ `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `SUPABASE_JWKS_URL`, `DATABASE_URL`, `ADMIN_PASSWORD`** |

Variáveis realmente lidas por código Python: `ADMIN_PASSWORD`, `BJJ_MODEL_ID`, `DATABASE_URL`,
`GRAPPLINGARC_HARVEST_DIR`, `QDRANT_PATH`, `ROBOFLOW_API_KEY`. As `SUPABASE_*` estão no `.env` mas
nenhum Python daqui as lê diretamente; as `KAGGLE_*` são consumidas pelo `kagglehub`.

Documentação: `README.md:11` só menciona `cp .env.example .env  # add Kaggle API keys`.
`DATABASE_URL` e `ADMIN_PASSWORD` só aparecem em `docs/PRO_ANALYTICS_HANDOFF.md` e
`docs/PRO_ANALYTICS_LOCAL_PUBLISHER.md`.

**Consequência:** quem clona o repo hoje não consegue subir o admin nem o publisher a partir da
documentação — falta metade das variáveis.

---

## 6. Migrations

- `alembic/versions/`: **21 revisões**, `0001` … `0021`.
- Cadeia estritamente linear — todo `down_revision` casa com a revisão anterior; `0001` tem
  `down_revision = None`.
- **Head único: `0021`** (`0021_pro_analytics.py`). Nenhuma head paralela.
- Últimas: `0018_user_sync_meta`, `0019_user_sessions_delete_guard`, `0020_guard_search_path`,
  `0021_pro_analytics`.
- **Nenhum teste de migration.** `alembic upgrade head` nunca roda no CI. O schema usa `pgvector(768)`,
  então SQLite não serve como substituto — um smoke test precisa de Postgres com pgvector.

---

## 7. Testes

- **63 arquivos** em `tests/`, ~465 funções `def test_`.
- Execução real: **495 testes passando em 39,54 s** (`uv run pytest -q`).
- **Nenhum `conftest.py`** no repo. Fixtures são arquivos de dados em `tests/fixtures/`:
  `fighter1.html`, `fighter2.html`, `style_parity.json`, `user_bundle_mini.json`,
  `vicos_annotations_mini.json`.
- Sem addopts, sem markers estritos, sem limiar de cobertura.
- 2 warnings: `StarletteDeprecationWarning` (httpx/testclient) e um `UserWarning` do xgboost.

---

## 8. Superfície de código

`export/` — 4.297 linhas:

| Linhas | Arquivo |
|---|---|
| 1500 | `export/site_data.py` |
| 633 | `export/tech_library.py` |
| 490 | `export/match_breakdown.py` |
| 430 | `export/ontology.py` |
| 337 | `export/narrative.py` |
| 249 | `export/grapplemap_icons_export.py` |
| 204 | `export/benchmark_results.py` |
| 174 | `export/grappling_map.py` |
| 121 | `export/athlete_graph_export.py` |
| 74 | `export/adcc_elo_table.py` |
| 63 | `export/incremental.py` |

Construtores de topo em `site_data.py`: `build_breakdowns` (`:264`), `build_fighters` (`:401`),
`build_elo` (`:545`), `build_events` (`:1167`).

Maiores arquivos fora de `scripts/dumps/` (que são dados, não lógica — o maior tem 2.415 linhas):
`export/site_data.py` (1500), `admin/server.py` (952), `db/repository.py` (805).

### `analysis/names.py` — 210 linhas, duas responsabilidades

| Símbolo | Linha | Papel |
|---|---|---|
| `NAME_ALIASES` | 11 | 12 aliases de técnica ("guillotine" propositalmente fora) |
| `_resolve_aliases` | 25 | |
| **`_normalize_name`** | **30** | **contrato com o App** (`normalizeLabel` em `graphSync.ts`). 5 linhas: lower/strip → remove não-`[a-z0-9 ]` → colapsa espaço |
| `SYNONYMS` | 44 | 14 entradas; comentário `ponytail:` em `:38-43` diz que só se aplica **depois** do `_normalize_name`, **apenas** em derivação interna do Analytics, nunca no `node_key` sincronizado |
| `canonicalize` | 64 | one-liner `SYNONYMS.get(key, key)` |
| `canonical_label` | 93 | |
| `clean_athlete_name` | 125 | limpeza de nome de atleta |
| `raw_athlete_key` / `athlete_key` | 193 / 198 | |

O arquivo mistura normalização de **técnica** (contrato cross-repo) com limpeza de **atleta**
(interno). Só a primeira é contrato.

### Contratos — não existe fonte única

- **Não existe diretório `contracts/`.**
- `schemas/` tem `__init__.py` (78) e `app_types.py` (177) — dataclasses espelhando os tipos TS do App
  à mão: `UserAuth` (`:15`), `RoundEntry` (`:26`), `RoundSnapshot` (`:36`), `Session` (`:50`),
  `GraphNode` (`:64`), `GraphEdge` (`:75`), `Graph` (`:84`), `Signature` (`:93`), `System` (`:103`),
  `UserBundle` (`:115`, com `from_json()`).
- Nenhum JSON Schema, nenhuma fixture compartilhada, nenhum teste de paridade automático com o App.
  A correspondência é mantida por disciplina humana.

---

## 9. Qualidade de dados — o que já existe

Não há conceito de quarentena nem módulo `data_quality`. O que existe são scripts de artefato de
revisão, avulsos:

| Arquivo | Papel |
|---|---|
| `scripts/detect_anomalies.py` | detecta nomes, duplicatas e resultados faltando em dados gerados em lote |
| `scripts/verify_dumps.py` | confere dumps contra transcrições (bouts, contagens, timestamps, nomes) |
| `scripts/recheck_generics.py` | gera prompts DeepSeek para labels genéricos |
| `analysis/match_deviance.py` | divergência Jensen–Shannon por (match, atleta) vs o histórico do atleta — sinal de QA |
| `analysis/date_reconcile.py` | cruza anos de luta com bjjheroes; read-only por default, `--apply` corrige |

Funções `validate*` no repo inteiro: **duas** — `export/ontology.py:367 validate_seed()` e
`realtime/export.py:123 validate_session_payload()`.

Documentos relacionados: `docs/qa_implementation_plan.md` (12 achados P0–P3, propõe uma "Lane F"),
`docs/dump_validation_report.md` (auditoria de 2026-07-07 sobre 58 dumps, nomes de atleta malformados),
`docs/canonicalization_report.{md,json}`, `docs/date_reconcile_report.json`.

**Não há portão** entre dado bruto e export público: nada impede um registro não resolvido de virar
página no site.

---

## 10. Higiene do repositório

- `admin/__pycache__/*.cpython-314.pyc` e `analysis/__pycache__` existem em disco (pycache é gitignored).
- `db.sqlite` — arquivo de 0 byte na raiz, gitignored.
- `web/node_modules/` e `web/dist/` em disco, com `web/.gitignore` presente.
- `pre-commit` é dependência declarada sem nenhuma configuração.

---

## 11. Gates — status

| Gate | Estado |
|---|---|
| CI roda em PR | 🟢 sim |
| pytest gate | 🟢 495 testes verdes |
| ruff cobre o repo | 🔴 só 7 caminhos hardcoded (100 erros escondidos, 99 em `scripts/`) |
| ruff format | 🔴 não roda (205/283 arquivos fora do padrão) |
| mypy strict gate | 🔴 configurado, **nunca executado** (322 erros / 49 arquivos; 16 fora de `tests/`) |
| Migrations testadas | 🔴 nenhuma |
| Head único do alembic verificado | 🔴 não (mas hoje está correto: head `0021`) |
| Segredo commitado | 🟢 nenhum |
| `.env.example` completo | 🔴 faltam 6 chaves |
| Admin seguro | 🔴 senha padrão `changeme`, sessão eterna, cookie fraco, sem CSRF/rate-limit/audit |
| Portão de qualidade de dados | 🔴 inexistente |
| Publicação Pro | 🔴 100% manual (`workflow_dispatch`) |
