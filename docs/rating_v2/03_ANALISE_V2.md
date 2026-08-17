# A camada de análise sobre a V2

O usuário pediu retrabalho da engine **e** refinamento do sistema de análise. Este doc trata do
segundo: como `network_metrics`, `athlete_systems`, `archetype`, `insights`, dossiês e o relatório de
categoria se reassentam — e, principalmente, o que **não** deve encostar no rating.

## As três camadas

```text
RATING            skill estimado, incerteza, força técnica     → rating_v2/
METAGAME          o que é usado, com que frequência,           → category_profile, insights
                  o que difere do corpus de elite
ESTRUTURA         que nós andam juntos no jogo                 → transitions/, constellations/
```

`ESTRUTURA` é insumo dos outros dois. `RATING` **nunca** é insumo de `METAGAME`. A seta que não pode
existir é `rating → metagame`, porque ela produz "Back Control tem rating alto, logo a categoria é
caracterizada por Back Control" — confundir importância estrutural com força estimada.

`analysis/network_metrics.py` já respeita isso hoje: PageRank, comunidades e reward−risk markoviano
(Lamas et al. 2024) são todos derivados de topologia e frequência, sem rating. A V2 não muda isso;
muda apenas de onde vem a função de detecção.

## Migração por módulo

| Módulo | Hoje | Depois | Quebra? |
|---|---|---|---|
| `network_metrics.py` (397 l.) | constrói a rede global e roda PageRank / comunidades / Markov | passa a construir via `transitions/build_graph.py` e detectar via `constellations/detect.py`; as métricas ficam | Não, se a construção do grafo for extraída sem mudar pesos |
| `athlete_systems.py` (582 l.) | detector próprio de "sistemas" do atleta | candidato a ser substituído pelo detector compartilhado — **só depois da comparação medida** (ADR-08) | Sim, potencialmente: tem consumidores em produto |
| `archetype.py` | KMeans agrupando **atletas** por vetor de grafo | inalterado — eixo diferente de constelação, que agrupa **nós** | Não |
| `insights.py` | relatório de pesquisa sobre o corpus | ganha as métricas de estabilidade da nova camada | Não |
| Dossiês | leem rating V1 | migram na wave 8, um consumidor por vez, atrás de `engine_version` | Sim, controlado |

O ponto que justifica a camada compartilhada: hoje "comunidade de técnicas" seria implementada três
vezes — em `network_metrics`, em `athlete_systems` e de novo no relatório de categoria. Três
definições divergem em silêncio; a primeira divergência aparece como duas telas do produto
discordando sobre o mesmo atleta.

## O relatório de categoria: o que depende da V2 e o que não depende

Spec: `docs/superpowers/specs/2026-08-16-relatorio-categoria-tendencias-design.md`.

**Não depende da V2 — pode ser construído já:**
- perfil médio da categoria (peso igual por atleta / event-weighted / leave-one-out);
- distribuição de técnicas e desvio vs baseline de elite no-gi, com simetria de ponderação;
- bootstrap de estabilidade das divergências;
- concentração da amostra (share da top-1, HHI);
- efetividade sobre resolvidos, cobertura, valor marginal de cobertura;
- rede de transições e reward−risk — `network_metrics` já entrega isso sem rating.

**Depende da camada compartilhada (não do rating):**
- a seção 5 de constelações: construção, comparação com o baseline, robustez
  (`STABLE` / `PARTIALLY STABLE` / `ATHLETE-DRIVEN`) e gate de publicação.

**Não depende nunca:**
- qualquer número de Glicko-2. O relatório de categoria não importa de `rating_v2/`. Se um dia
  importar, o revisor deve tratar como defeito de arquitetura, não como feature.

Consequência prática de sequenciamento: o relatório pode ser implementado **antes** da engine, desde
que a seção 5 espere `analysis/constellations/detect.py` (wave 4). As seções 1–4 e 6–11 não têm
dependência nenhuma da V2.

## Constelação de categoria ≠ constelação de atleta

O mesmo detector, entradas diferentes:

| | Constelação de atleta | Constelação de categoria |
|---|---|---|
| Entrada | grafo de transições do atleta | transições da divisão, **normalizadas por atleta** antes de agregar |
| Ponderação | ocorrência bruta | athlete-balanced (senão a atleta dominante define a comunidade) |
| Gate | suporte mínimo de lutas | suporte + **mais de uma atleta contribuindo** + estabilidade mínima |
| Rótulo de risco | — | `ATHLETE-DRIVEN` quando a comunidade some ao remover a dominante |

O gate de "mais de uma atleta" é o que permite escrever *"este padrão aparece no corpus da divisão,
mas hoje é sustentado principalmente por uma atleta"* em vez de chamar aquilo de "meta da categoria".
Com uma atleta concentrando a maior parte dos eventos próprios em +65 kg, esse gate é a diferença
entre relatório e ficção.
