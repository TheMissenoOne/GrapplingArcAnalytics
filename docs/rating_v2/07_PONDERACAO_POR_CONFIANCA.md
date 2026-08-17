# Ponderação por confiança no mapa público — medição (wave 9)

**Status: medido, 2026-08-17. Recomendação abaixo. Nada foi publicado — esta é uma medição
read-only, nenhum export/site rodou, nenhuma escrita em produção.**

## Pergunta

Hoje `analysis/transitions/build_graph.py:network_from_sequences` e todos os consumidores
(`analysis/network_metrics.py`, `analysis/grappling_map.py`, `analysis/ocean.py`,
`analysis/constellations/detect.py`) contam cada luta/transição igualmente, independente
de quão bem observado é o atleta que a produziu. Ponderar por confiança (RD do Rating
Engine V2, run `210a5ba7-7f88-4b54-b5a5-1dbadfdab4b2`) muda o mapa público o suficiente
para justificar acoplar os dois sistemas?

## Metodologia

**Corpus.** Todas as 894 `matches` do banco, filtradas por `status='final'` e disciplina
`submission_grappling` via o mesmo mapa `data/rating_v2/disciplines.json` que o replay usa
(`analysis/rating_v2/replay.py:load_discipline_map`/`_discipline_of`) — mesma definição de
corpus que a V2, para que RD e grafo falem da mesma população. Resultado: **576 lutas
elegíveis, 568 atletas, 213 nós** no grafo agregado.

**Confiança.** RD por atleta lido de `athlete_rating_states_v2` para o `run_id` fixo acima
(ADR-02: sempre com `run_id` explícito). 558/568 atletas (98%) têm estado nesse run; os 10
restantes caem no piso `RD=250` (`EngineConfig.initial_rd`) — nunca peso zero.

**Esquemas** (`analysis/confidence_weight.py`, testado em `tests/test_confidence_weight.py`):

| Esquema | Fórmula | Faixa |
|---|---|---|
| `uniform` | `w = 1` | controle — comportamento de hoje |
| `precision` | `w = 1/RD²`, renormalizado p/ média 1 | ilimitado acima da média |
| `bounded` | `w = 1/(1 + (RD/200)²)`, `RD_ref=200` (corte editorial da wave 8) | `(0, 1]` |

Um 4º esquema (shrinkage por `bouts_observed`) foi considerado e **descartado por YAGNI**:
resolveria a mesma pergunta que `bounded` já resolve (temperar atletas de alta incerteza)
por outro caminho, sem dar ao relatório um ponto de comparação distinto — não valeu a
quarta coluna. Registrado no docstring do módulo, não implementado.

**Aplicação do peso.** `network_from_sequences` ganhou um parâmetro opcional `weight_fn`
(``None`` = comportamento idêntico a antes, byte-a-byte — testado em
`tests/test_transitions.py`). Toda contagem que a função produz (`occ`, `ok_count`,
`denom`, `reward`, `risk` por nó; `weight`/`ok` por aresta) é atribuída ao ator dono do
evento sendo contado — a mesma convenção de `actor_id` que já rege ELO e edges
direcionadas (`CLAUDE.md` raiz, "Directed-edge rules"). Nenhum consumidor (site, ocean,
constellations) foi tocado; eles continuam chamando `network_from_sequences(seqs)` sem o
parâmetro, então nada do que o site publica mudou.

**Script de medição** (read-only, não escreve no banco, não chama `export.site_data`):
`scripts/measure_confidence_weighting.py` → `uv run python -m
scripts.measure_confidence_weighting` → `reports/rating_v2/confidence_weighting.json`
(gitignored).

## Resultado

| Métrica | `uniform` (controle) | `precision` (1/RD²) | `bounded` (1/(1+(RD/200)²)) |
|---|---|---|---|
| Spearman PageRank vs. controle | 1.0 | **0.9617** | **0.9946** |
| Spearman Weighted PageRank | 1.0 | 0.9891 | 0.9989 |
| Spearman betweenness | 1.0 | 0.9460 | 0.9763 |
| Spearman frequência (occ) | 1.0 | 0.9261 | 0.9808 |
| Spearman reward-risk | 1.0 | 0.9631 | 0.9858 |
| Top-20 PageRank: entram/saem | — | 1 entra / 1 sai (Rear Naked Choke ↔ Escape to Standing) | **0 / 0** |
| Top-20: deslocamento médio de posição | — | 1.53 | 0.40 |
| Top-20: deslocamento máximo | — | 8 | 2 |
| Concentração top-1 atleta (share do peso total) | **2.46%** | **18.14%** | 4.10% |
| Concentração top-5 atletas | 8.33% | 28.27% | 11.54% |
| Nós que mudam de região (comunidade) | — | 27.23% (58/213) | 20.19% (43/213) |
| Jaccard médio melhor-match de comunidade | — | 0.689 | 0.711 |

Top-1 contribuidor do corpus (por volume bruto de eventos): **Gordon Ryan**, em todos os
esquemas — a pergunta não é se ele domina o corpus (domina, por volume real de lutas), é
quanto cada esquema de peso **amplifica** esse domínio além do que o volume já garante.

## Leitura

**A armadilha do enunciado se confirmou, com número.** `precision` faz a participação do
maior contribuidor saltar de 2,46% para **18,14%** do peso total do grafo — um fator
~7,4×. Não é um efeito hipotético de RD 58 vs. 250: é o que aconteceu no corpus real. Ao
mesmo tempo, a correlação de ranking continua alta (Spearman ≥ 0,93 em todas as métricas)
porque PageRank e frequência já são dominados pelas mesmas técnicas centrais
independentemente do peso — mas top-20 já sente 1 entrada/saída e até 8 posições de
deslocamento, e **27% dos nós mudam de região no mapa**. `precision` é rejeitado como
esquema de produção pelo motivo que o enunciado já antecipava.

**`bounded` é quase indistinguível do controle nas métricas que o site publica.**
PageRank (0,9946), Weighted PageRank (0,9989), reward-risk (0,9858) e betweenness (0,9763)
— todas ≥0,976 exceto frequência bruta (0,9808, ainda alta). **Zero** movimento no top-20:
nenhum nó entra, nenhum sai, deslocamento médio de 0,4 posição. A concentração sobe, mas
moderadamente (2,46%→4,10% top-1; 8,33%→11,54% top-5) — o RD_ref=200 está fazendo o que
foi desenhado para fazer: dar mais peso a quem tem menos incerteza, sem reproduzir a
amplificação de `precision`.

**A mudança de comunidade (20-27%) é o único número que não é pequeno em nenhum dos dois
esquemas, e é a métrica que merece mais ceticismo, não menos.** ADR-07/ADR-08 já
documentaram que o Louvain deste corpus isola nós de baixo grau como singletons com
facilidade (até 40% em alguns atletas) e que o Jaccard médio de bootstrap entre
detecções **do mesmo grafo** já fica na faixa 0,58–0,85 dependendo do par comparado — ou
seja, o detector tem ruído de partição inerente nessa faixa mesmo sem qualquer mudança de
peso. Um Jaccard médio de 0,69–0,71 aqui está dentro (não abaixo) da faixa de ruído já
medida para este detector neste corpus. **Não dá para atribuir a reorganização de
comunidade ao esquema de peso com confiança — é indistinguível da instabilidade que o
Louvain já tem neste corpus**, sem rodar o mesmo bootstrap sob peso (não feito aqui; ver
"O que ficou de fora").

## Recomendação — **nenhum, por ora**

**Não ponderar o mapa público por confiança agora.** O esquema seguro (`bounded`,
`RD_ref=200`) muda as métricas que o site efetivamente publica em menos de meio ponto de
correlação (Spearman ≥ 0,976 em PageRank/Weighted PageRank/betweenness/reward-risk) e
zero posições de churn no top-20 — abaixo do que justifica acoplar o mapa público a um
motor de rating que **ainda está em shadow** (ADR-02: V2 nunca é lido sem `run_id`
explícito, não fez cutover). O esquema que teria um efeito real (`precision`) é
exatamente o esquema que reproduz a armadilha que motivou a medição — descartado pelo
próprio enunciado do problema, confirmado pelo número (top-1 share 7,4× o controle).

**Se/quando isso for revisitado:** `bounded` é a escolha certa entre os dois testados —
nunca `precision`. Mas o gatilho para revisitar não é "o RD ficou mais preciso" — é o
Rating V2 fazer cutover (deixar de ser shadow) E alguém rodar o bootstrap de
`analysis/constellations/stability.py` sob os pesos `bounded` para separar "mudança real
de comunidade por confiança" de "ruído normal do Louvain neste corpus" antes de tratar os
20% como um efeito real.

## O que ficou de fora (dívida declarada, não escondida)

- **Bootstrap de estabilidade sob peso**: `stability.py` já existe e faz exatamente esse
  teste para o detector sem peso; rodá-lo com `weight_fn=bounded` responderia se os 20% de
  mudança de comunidade são sinal ou ruído. Não rodado aqui — está fora do escopo de
  "medir o efeito", é a próxima pergunta se este relatório for revisitado.
- **Efeito no `grappling_map.py`/Ocean além de reward-risk e betweenness** (ex.: as
  arestas sugeridas por similaridade semântica em `attach_neighbors`) não foi medido —
  esse pipeline não depende de `occ`/`weight` do jeito que PageRank depende, e o
  enunciado não pediu.
