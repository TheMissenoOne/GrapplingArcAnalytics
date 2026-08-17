# Comparação de detectores — `athlete_systems.py` × `constellations/detect.py`

Wave 6 (`02_PLANO_DE_EXECUCAO.md`), critério do ADR-08 (`01_DECISOES.md`): o detector
compartilhado só é declarado sucessor do antigo se **ganhar em estabilidade sob bootstrap sem
perder cobertura**. Medido aqui, não decretado.

## Metodologia

**Ferramenta reusada, não reescrita.** `analysis/constellations/compare.py` (Jaccard por
comunidade + agregado, escrita na wave 4/5 e até agora sem chamador) é a instrumentação —
`scripts/compare_athlete_system_detectors.py` só adiciona a leitura no banco, o adaptador para
o detector antigo, e o laço de bootstrap compartilhado.

**Alinhamento de entrada — a diferença é documentada antes de comparar, como o ticket pede.**
Em produção os dois detectores **não** leem a mesma coisa:

- `analysis/athlete_systems.py` roda hoje sobre o grafo do atleta **persistido**
  (`graphs`/`graph_edges`, `export/ontology.py`) — peso de aresta uniforme = 1 (`graph_edges`
  não guarda contagem de transição), sem proveniência por luta.
- `analysis/constellations/detect.py` roda sobre um `nx.DiGraph` construído por
  `analysis/transitions.network_from_sequences` a partir de sequências de eventos por luta
  (`Match.sequence`), com peso de aresta = contagem real de transições.

Comparar essas duas entradas misturaria "detector diferente" com "dado diferente" — exatamente
o que o ticket pede para não fazer. Este relatório evita a mistura alimentando **os dois
detectores com o mesmo grafo**: as sequências por luta (só eventos próprios do atleta, via
`analysis.category_constellations.sequences_by_athlete`, que já existia e é reusada) constroem
um `nx.DiGraph` único; o detector novo roda direto nele; o detector antigo roda num
`AthleteGraph` adaptado do mesmo grafo (mesmos nós, mesmos pesos de aresta — conversão em
`scripts/compare_athlete_system_detectors.py:_digraph_to_athlete_graph`). O resultado isola a
variável que a wave pede para medir — o algoritmo — e não testa o pipeline de produção do
detector antigo (isso é outra pergunta, fora do escopo desta wave).

**Cobertura de dado, medida, não assumida.** A lista do ticket cita "Gordon Ryan 114" lutas —
esse é o total de linhas `matches` com Gordon Ryan como participante. Só **16** têm eventos de
sequência atribuíveis a ele como ator (17 têm `sequence` não vazio; uma não tem nenhum evento
dele). O corpus de point-by-point é uma fração pequena do corpus de resultado — a maioria das
`matches` tem só placar/vencedor, sem replay de técnica. Isso vale para todo atleta medido, não
só Gordon Ryan; é por isso que a tabela abaixo reporta `n_lutas` (lutas com sequência própria),
não o total de confrontos do atleta.

**Amostra.** Os 5 nomeados no ticket + 10 extras pela contagem de `matches` no banco, todos com
≥ 5 lutas próprias com sequência (piso para o bootstrap ter algum sentido — abaixo disso o
script roda e reporta cobertura/Jaccard, mas pula a estabilidade e anota o motivo). 15 atletas
mediram estabilidade; nenhum caiu abaixo do piso e ficou de fora do bootstrap nesta amostra.

**Estabilidade sob bootstrap, medida do mesmo jeito para os dois.** Para cada atleta: 100
reamostras com reposição das lutas próprias, mesma reamostra alimentando os dois detectores em
cada iteração (não dois bootstraps independentes — o mesmo sorteio, para que a única fonte de
diferença entre os dois números seja o algoritmo, não ruído de amostragem). Jaccard best-match
médio entre a partição de cada reamostra e a partição base, via `compare_partitions` — o mesmo
instrumento usado no resto do pipeline (`stability.py`, wave 4). Ambos os detectores incluem
singleton como comunidade própria nesta medição (`min_system_size=1` do lado antigo) — do
contrário todo nó sem par contaria automaticamente como divergência e o Jaccard mediria
principalmente a política de corte, não a estabilidade da partição.

**Cobertura, no corte de produção.** O detector antigo roda com seu próprio default
(`min_system_size=2`) para a coluna de cobertura — é o que ele faz hoje, não uma variante
inventada para o teste. `old_coverage` = fração dos nós do grafo que caem em algum sistema
`size ≥ 2`; quem fica de fora simplesmente não aparece em nenhuma saída do detector antigo. O
detector novo nunca derruba um nó — toda comunidade de tamanho 1 vira uma "constelação" trivial
de um membro — então sua cobertura bruta é sempre 100%; a coluna comparável é
`1 − singleton_share`: fração dos nós que caem numa comunidade **não trivial** (tamanho ≥ 2).

Achado colateral, corrigido dentro do escopo desta wave (não em `athlete_systems.py`, que está
proibido): `analysis/constellations/detect.py` dividia por zero quando o grafo tinha ≥ 2 nós e
zero arestas (todos isolados) — Louvain devolve uma comunidade-singleton por nó,
`nx.community.modularity` divide pelo grau total, que é 0. Apareceu numa reamostra durante esta
medição, não num caso sintético. Corrigido com um guard (`und.number_of_edges() > 0`) +
regressão em `tests/test_constellations.py::test_detect_multiple_isolated_nodes_no_zero_division`.

## Tabela por atleta

`n_lutas` = lutas com sequência própria (não o total de confrontos — ver acima).
`novo`/`antigo` = nº de comunidades (maior comunidade, singleton_share ou cobertura).
`jaccard` = Jaccard agregado simétrico entre as duas partições no mesmo grafo (`min_system_size=1`
dos dois lados). `estab.` = Jaccard médio de bootstrap (100 reamostras) contra a própria base.

| Atleta | n_lutas | nós/arestas | novo: grupos (maior, %singleton) | antigo: grupos (maior, cobertura) | Jaccard | estab. novo | estab. antigo |
|---|---:|---|---|---|---:|---:|---:|
| Gordon Ryan | 16 | 45/119 | 4 (15, 0%) | 6 (14, 100%) | 0.615 | 0.489 | 0.451 |
| Craig Jones | 29 | 41/90 | 5 (12, 20%) | 5 (9, 98%) | 0.731 | 0.576 | 0.533 |
| Leandro Lo | 12 | 14/21 | 3 (8, 33%) | 3 (5, 93%) | 0.812 | 0.689 | 0.666 |
| Kade Ruotolo | 11 | 31/34 | 5 (10, 0%) | 5 (10, 100%) | 1.000 | 0.589 | 0.595 |
| Nick Rodriguez | 13 | 25/42 | 5 (10, 20%) | 4 (8, 96%) | 0.589 | 0.585 | 0.525 |
| Tye Ruotolo | 8 | 17/18 | 5 (6, 20%) | 3 (6, 94%) | 0.838 | 0.651 | 0.692 |
| Felipe Pena | 10 | 23/29 | 7 (7, 29%) | 5 (7, 91%) | 0.786 | 0.531 | 0.591 |
| Giancarlo Bodoni | 12 | 30/62 | 7 (10, 14%) | 6 (10, 97%) | 1.000 | 0.588 | 0.546 |
| Helena Crevar | 12 | 52/109 | 8 (12, 25%) | 7 (10, 96%) | 0.551 | 0.470 | 0.497 |
| Mica Galvão | 7 | 26/59 | 5 (9, 0%) | 4 (8, 100%) | 0.551 | 0.442 | 0.526 |
| Vagner Rocha | 9 | 33/68 | 4 (11, 0%) | 4 (11, 100%) | 0.908 | 0.598 | 0.567 |
| Roberto Jimenez | 6 | 15/28 | 4 (7, 0%) | 3 (8, 100%) | 0.736 | 0.691 | 0.722 |
| Jake Strauss | 7 | 10/11 | 5 (3, 40%) | 3 (3, 80%) | 1.000 | 0.730 | 0.670 |
| Shawn Melanson | 7 | 13/17 | 3 (6, 0%) | 3 (7, 100%) | 0.886 | 0.581 | 0.596 |
| Victor Hugo | 10 | 27/47 | 5 (8, 0%) | 5 (8, 100%) | 0.589 | 0.562 | 0.519 |

Nenhum dos dois deixou algum dos 15 atletas totalmente sem estrutura (`no_structure_new` /
`no_structure_old` = 0 em todos).

## Agregado (n=15)

| Métrica | Novo (`constellations`) | Antigo (`athlete_systems`) |
|---|---:|---:|
| Estabilidade média (bootstrap, Jaccard) | 0.5848 | 0.5797 |
| Estabilidade mediana | 0.585 | 0.567 |
| Atletas onde vence | 8/15 | 7/15 |
| Cobertura não-trivial média | 86.6% (`1 − singleton_share`) | 96.3% (`min_system_size=2`) |
| Grupos por atleta (média) | 5.0 | 4.4 |
| Maior grupo (média) | 8.9 nós | 8.3 nós |
| Sem estrutura nenhuma | 0/15 | 0/15 |

Jaccard agregado entre as duas partições (mesmo grafo): **0.773** — concordância real, os dois
detectores acham majoritariamente a mesma estrutura de base; onde divergem é sobretudo em quanto
cada um fragmenta a franja (nós de baixo grau viram singleton no Louvain com mais frequência do
que na modularidade gulosa).

## Veredito contra o ADR-08

**O critério é: o compartilhado só substitui se ganhar em estabilidade E não perder em
cobertura. Nenhuma das duas condições se sustenta nesta amostra.**

1. **Estabilidade — empate, não vitória.** Diferença média de 0.005 (0.5848 vs 0.5797), 8 vitórias
   contra 7 numa amostra de 15 — dentro do ruído de amostragem do próprio bootstrap, não um
   ganho. Não é "o novo é pior"; é "os dois são estatisticamente equivalentes em estabilidade
   neste corpus". Isso por si só já barra o "ganhar em (b)" do ADR-08.
2. **Cobertura — o antigo é melhor, mensuravelmente.** 96.3% dos nós do antigo caem em algum
   sistema não-trivial contra 86.6% do novo. A causa é o próprio algoritmo, não um artefato do
   adaptador: Louvain em resolução 1.0 isola mais nós de baixo grau em comunidades-singleton do
   que a modularidade gulosa (`greedy_modularity_communities`) que o antigo usa — visível na
   coluna `%singleton` da tabela (0% a 40% por atleta no novo, contra o corte fixo em 2 do
   antigo). Isso é uma perda de cobertura clara, o segundo critério do ADR-08, e sozinho já
   fecha a decisão.

**Não substitui.** Coexistem, como o ADR-08 já previa como resultado possível. `athlete_systems.py`
continua sendo o que alimenta `export/ontology.py` e `export/site_data.py` — nenhuma remoção,
nenhuma migração de consumidor. O motivo por escrito é este relatório.

Dito de outro jeito, porque a pergunta pede a resposta medida e não a que justificaria o trabalho
da wave 4: **o detector compartilhado não é pior no que faz — concorda 77% com o antigo e é
igualmente estável — mas também não é melhor no eixo que decide a troca.** A wave 4 continua
valendo pelo motivo pelo qual foi construída (uma camada topológica sem rating, compartilhada
entre o rating engine e o relatório de categoria, ver ADR-08's "por quê" e `category_constellations.py`)
— só não pelo motivo de substituir o detector de sistema do atleta.

## O que não mudou

- `analysis/athlete_systems.py` — intocado (proibido nesta wave, e agora também justificado pela
  medição: não perde).
- `analysis/archetype.py` — fora de escopo, outro eixo (agrupa atletas, não nós), continua
  existindo de qualquer forma.
- Nenhuma escrita no banco. Leitura via `db.base.get_session_factory` só com `SELECT`.

## Correção feita nesta wave (fora de `athlete_systems.py`, dentro do escopo)

`analysis/constellations/detect.py` — divisão por zero em `nx.community.modularity` quando o
grafo tem ≥ 2 nós e nenhuma aresta (todos isolados); `len(raw) > 1` era verdadeiro (uma
comunidade-singleton por nó) mas o grau total é 0. Guard adicionado
(`und.number_of_edges() > 0`), regressão em
`tests/test_constellations.py::test_detect_multiple_isolated_nodes_no_zero_division`. Sem esse
guard o script de comparação quebra ao reamostrar um atleta cujas lutas próprias às vezes não
produzem nenhuma transição (evento único por luta).
