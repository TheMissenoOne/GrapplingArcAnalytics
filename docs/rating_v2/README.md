# Rating Engine V2 — Glicko-2 + constelações

**Estado atual: NO AR.** O cutover foi feito (ADR-13, 2026-08-17) e a camada de nó saiu da sombra
em 2026-08-26 (ADR-16): `analysis/rating_v2/` é a engine do rating global E da nota por nó, e
`db.repository.replay_and_persist_athlete` projeta os dois em `computed_elo` / `graph_edges.elo` /
`graphs.user_elo` / `athletes.elo`. A V1 (`analysis/athlete_elo.py`) continua derivando o GRAFO
(nós, arestas, contagens, proveniência) e ainda rateia o que a V2 não cobre (MMA/wrestling, ADR-05).
Estado operacional e runbook: [`08_ESTADO_DO_CUTOVER.md`](08_ESTADO_DO_CUTOVER.md).

Origem: bundle de plano fornecido pelo usuário em
`/home/vetor/GrapplingArc/grapplingarc_glicko2_constellation_engine_plan_bundle/`, gerado contra o
commit `3d02020` — que é o HEAD atual deste repo. O plano foi escrito contra o código real.

## Índice

| Doc | Conteúdo |
|---|---|
| [`00_AUDITORIA_DO_PLANO.md`](00_AUDITORIA_DO_PLANO.md) | O bundle ponto-a-ponto contra o repo: o que confere, o que não existe, o que ele omite. Inclui o replay de sombra medido. |
| [`01_DECISOES.md`](01_DECISOES.md) | ADR das decisões de produto que o bundle deixou em aberto. |
| [`02_PLANO_DE_EXECUCAO.md`](02_PLANO_DE_EXECUCAO.md) | Waves adaptadas ao repo, com critério de aceitação mensurável por wave. |
| [`03_ANALISE_V2.md`](03_ANALISE_V2.md) | Como a camada de análise se reassenta: transições, constelações, e o que o relatório de categoria consome. |
| [`04_CONTRATOS_E_RISCOS.md`](04_CONTRATOS_E_RISCOS.md) | Contratos cross-module que quebram, ordem de migração, registro de riscos. |
| [`05_COMPARACAO_DETECTORES.md`](05_COMPARACAO_DETECTORES.md) | Constelações × `athlete_systems`, o critério do ADR-08 e as duas medições que o reabriram. |
| [`06_V1_VS_V2.md`](06_V1_VS_V2.md) | O que cada engine ainda faz, lado a lado. |
| [`07_PONDERACAO_POR_CONFIANCA.md`](07_PONDERACAO_POR_CONFIANCA.md) | Ponderação do corpus por confiança de atleta (`weight_fn` do `build_graph`). |
| [`08_ESTADO_DO_CUTOVER.md`](08_ESTADO_DO_CUTOVER.md) | Estado operacional, run fixado, runbook de replay. |
| [`09_SUCESSO_DERIVADO.md`](09_SUCESSO_DERIVADO.md) | **D7** — o score de uma ação derivado da própria cadeia, sem a flag `successful`. Score binário + cadeia de fontes, veredito sobre PtV, efeito medido na população de evidência e na escala. Design, ainda não implementado. |

## Invariante de arquitetura

**Rating, metagame e estrutura de jogo são três camadas separadas.** Rating responde *skill estimado
/ incerteza / força técnica*. Metagame responde *o que é usado, com que frequência, o que conecta
com o quê, como difere do corpus de elite*. Estrutura de jogo (constelações) responde *que
subconjuntos do grafo andam juntos*.

Consequência operacional: **membership de constelação nunca usa rating**, e o relatório de categoria
não consome nada de `rating_v2/` — nem depois da V2 no ar. Misturar as camadas produz conclusões do
tipo "Back Control tem rating alto, logo a categoria é caracterizada por Back Control", que é
precisamente o acoplamento que a V2 existe para eliminar.

## Camada compartilhada

Constelação tem **uma** definição no produto:

```
analysis/
  transitions/      build_graph.py, normalize.py
  constellations/   detect.py, stability.py, compare.py
  rating_v2/        glicko2.py, replay.py, config.py, models.py, periods.py
```

```text
Athlete Rating Engine  ──> constellations/detect.py
Category Trend Report  ──> constellations/detect.py
Baseline de elite      ──> transitions/build_graph.py
```

Nenhum consumidor implementa a sua própria versão. `analysis/athlete_systems.py` (582 linhas) e
`analysis/network_metrics.py` (397) migram para essa camada em vez de divergir dela.
