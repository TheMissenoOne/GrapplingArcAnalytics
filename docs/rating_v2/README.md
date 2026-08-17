# Rating Engine V2 — Glicko-2 + constelações

**Estado atual: nada implementado. Isto é plano.** Nenhuma linha de engine V2 existe no repo,
nenhuma tabela foi criada, nenhuma migration escrita. A V1 (`analysis/athlete_elo.py`) continua
sendo a única engine em produção.

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
