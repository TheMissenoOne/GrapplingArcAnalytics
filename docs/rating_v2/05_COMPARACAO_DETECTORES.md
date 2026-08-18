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

---

## Wave 6b — o antigo (e o novo) na entrada REAL de produção

A wave 6 mediu os dois detectores com o mesmo grafo derivado de sequências, para isolar o
algoritmo — e deixou escrito, como ressalva não medida, que em produção `athlete_systems.py`
nunca vê esse grafo. Ele consome `graphs`/`graph_edges` persistido via `export/ontology.py`:
peso de aresta forçado a 1 (`graph_edges` não guarda contagem de transição), sem proveniência
por luta, só nós grappling (filtro `export.ontology.eligible_grappling_graphs`). Esta seção mede
o detector antigo — e, para contexto, também o novo — nessa entrada real, para os mesmos 15
atletas da wave 6.

**Instrumento.** `scripts/compare_prod_input_athlete_systems.py`, read-only (só `SELECT` em
`graphs`/`graph_edges`/`technique_nodes`/`athletes`/`matches`). Não escreve, não toca
`analysis/athlete_systems.py`. Reusa `analysis/constellations/compare.py` (o mesmo instrumento
da wave 6) e a seleção de atletas + `measure_athlete` de
`scripts/compare_athlete_system_detectors.py` — os números "seq" abaixo são recalculados nesta
mesma rodada, contra o mesmo snapshot do banco, em vez de retranscritos do relatório da wave 6
(por isso batem com a wave 6 até a 3ª/4ª casa decimal, não exatamente — ver "drift do banco vivo"
abaixo).

**Achado prévio à medição em si, achado por conta própria: chave de nó incompatível entre as
duas entradas.** `network_from_sequences` rotula nós pelo label de exibição canônico de
`analysis.technique_match.clean_label` (`"Closed Guard"`); o `node_key` persistido é
`canonicalize(_normalize_name(label))` (`"closed guard"`, minúsculo, colapsado por sinônimo —
é o que `analysis.athlete_elo.replay_matches`, o próprio construtor do grafo persistido, usa).
Comparar as duas partições sem normalizar dava Jaccard 0.0 para os 15 atletas — não porque as
estruturas divergiam, mas porque toda string "diferia" por causa (bug de medição, corrigido antes
de qualquer número deste relatório ser produzido — `_to_node_key_space` no script, regressão em
`tests/test_compare_prod_input_athlete_systems.py::test_cross_input_jaccard_normalizes_display_label_casing`).
Fica registrado porque é exatamente o tipo de erro que a mistura de entradas que a wave 6 evitou
propositalmente teria escondido — e porque bate no mesmo contrato de `node_key` que
`04_CONTRATOS_E_RISCOS.md`/CLAUDE.md raiz tratam como char-for-char entre módulos.

**Drift do banco vivo.** O banco é o Supabase de produção, sob escrita por outros processos
(replay, publish). Duas rodadas consecutivas desta medição já viram contagem de aresta diferente
para o mesmo atleta (ex.: Giancarlo Bodoni, 62 → 63 arestas). Os números abaixo são todos de UMA
única rodada consistente (mesmo snapshot para todas as colunas), não uma colagem de rodadas
diferentes.

**Bootstrap na entrada de produção — o buraco que a wave 6 apontou, agora nomeado.** O bootstrap
da wave 6 reamostra lutas (a unidade de observação real). O grafo persistido não tem unidade por
luta para reamostrar — é exatamente essa ausência que a wave 6 marcou como não medida. A unidade
mais próxima disponível é a própria aresta (`graph_edges` já é deduplicado a uma linha por
`(source, target)`, então também não há multiplicidade real para reamostrar): cada reamostra sorteia
`len(edges)` arestas com reposição — sortear a mesma aresta duas vezes a torna mais pesada nessa
reamostra, a única alavanca que esta entrada tem para variar estrutura entre reamostras. Isso é uma
instrumentação estruturalmente mais fraca que o bootstrap por luta da wave 6, e a estabilidade medida
aqui deve ser lida como esse tipo de evidência — direção informativa, não uma segunda wave 6.

### Tabela por atleta

`cov` = cobertura (antigo: `min_system_size=2`, fração em sistema não-trivial; novo: `1 −
singleton_share`). `cross-jacc` = Jaccard simétrico do detector ANTIGO consigo mesmo, entre a
partição no grafo de sequência e a partição no grafo de produção (mesmo atleta, `min_system_size=1`,
chaves normalizadas para o mesmo espaço) — responde diretamente "a entrada muda a resposta".

| Atleta | seq: antigo grupos(maior,cov) | prod: antigo grupos(maior,cov) | prod: novo grupos(maior,cov) | cross-jacc (antigo, seq×prod) | estab.antigo seq | estab.antigo prod | estab.novo seq | estab.novo prod |
|---|---|---|---|---:|---:|---:|---:|---:|
| Gordon Ryan | 6 (14, 100%) | 5 (13, 100%) | 5 (13, 100%) | 0.578 | 0.4505 | 0.4551 | 0.4891 | 0.4643 |
| Craig Jones | 5 (9, 97.6%) | 7 (9, 100%) | 5 (14, 100%) | 0.368 | 0.5331 | 0.4568 | 0.5758 | 0.4590 |
| Leandro Lo | 3 (5, 92.9%) | 4 (5, 100%) | 3 (5, 100%) | 0.608 | 0.6661 | 0.5996 | 0.6888 | 0.6212 |
| Kade Ruotolo | 5 (10, 100%) | 5 (8, 100%) | 5 (8, 100%) | 0.903 | 0.5950 | 0.5528 | 0.5887 | 0.5555 |
| Nick Rodriguez | 4 (8, 96.0%) | 5 (7, 100%) | 5 (7, 100%) | 0.636 | 0.5248 | 0.4836 | 0.5850 | 0.5319 |
| Tye Ruotolo | 3 (6, 94.1%) | 3 (6, 100%) | 4 (5, 100%) | 0.692 | 0.6916 | 0.5944 | 0.6510 | 0.6631 |
| Felipe Pena | 5 (7, 91.3%) | 5 (7, 100%) | 5 (7, 100%) | 0.674 | 0.5906 | 0.6404 | 0.5306 | 0.6412 |
| Giancarlo Bodoni | 6 (10, 96.7%) | 6 (10, 100%) | 6 (9, 100%) | 0.772 | 0.5463 | 0.5247 | 0.5885 | 0.5527 |
| Helena Crevar | 7 (10, 96.2%) | 5 (15, 100%) | 6 (11, 100%) | 0.472 | 0.4971 | 0.3779 | 0.4704 | 0.3898 |
| Mica Galvão | 4 (8, 100%) | 4 (8, 100%) | 4 (7, 100%) | 0.534 | 0.5262 | 0.4639 | 0.4424 | 0.4678 |
| Vagner Rocha | 4 (11, 100%) | 5 (10, 100%) | 5 (10, 100%) | 0.751 | 0.5670 | 0.4539 | 0.5979 | 0.4634 |
| Roberto Jimenez | 3 (8, 100%) | 4 (5, 100%) | 4 (5, 100%) | 0.632 | 0.7224 | 0.5792 | 0.6909 | 0.6159 |
| Jake Strauss | 3 (3, 80.0%) | 2 (4, 100%) | 2 (4, 100%) | 0.478 | 0.6698 | 0.6755 | 0.7305 | 0.6552 |
| Shawn Melanson | 3 (7, 100%) | 3 (6, 100%) | 3 (5, 100%) | 0.886 | 0.5964 | 0.5963 | 0.5808 | 0.6427 |
| Victor Hugo | 5 (8, 100%) | 5 (7, 100%) | 6 (7, 100%) | 0.570 | 0.5188 | 0.4946 | 0.5623 | 0.4774 |

Nenhum dos 15 atletas ficou sem estrutura em nenhuma das quatro combinações (entrada × detector).

### Agregado (n=15)

| Métrica | seq (wave 6) | prod (esta wave) |
|---|---:|---:|
| Cobertura, antigo (`min_size=2`) | 96.3% | **100.0%** |
| Cobertura, novo (`1 − singleton_share`) | 86.6% | **100.0%** |
| Estabilidade, antigo (média bootstrap) | 0.5797 | 0.5299 |
| Estabilidade, antigo (mediana) | 0.567 | 0.5246 |
| Estabilidade, novo (média bootstrap) | 0.5848 | 0.5467 |
| Novo > antigo em estabilidade (contagem) | 8/15 | **13/15** |
| Cross-jaccard (antigo, seq×prod, por atleta) | — | média 0.637, mediana 0.632, faixa 0.368–0.903 |

### O que isso responde: algoritmo ou dado?

**A razão que fechou a decisão da wave 6 (cobertura) era do DADO, não do algoritmo.** Na entrada
de produção, `graphs_for_clustering` constrói o nó a partir dos extremos de aresta — não existe
mecanismo nenhum para um nó de grau 0 entrar no grafo. Um grafo derivado de sequência TEM nós assim
(a última posição de uma luta, sem sucessor próprio — o `"lonely node"` do fixture de teste é
justamente esse caso). É essa possibilidade estrutural, presente na entrada de sequência e ausente
na entrada de produção, que produzia singletons no Louvain (0–40% por atleta, wave 6) — na entrada
real, `new_singleton_share` é **0% nos 15 atletas, sem exceção**. Cobertura do novo detector sobe de
86.6% para 100% não porque o algoritmo mudou, mas porque a entrada real nunca lhe dá um nó isolado
para isolar. E cobertura do antigo, que já era alta (96.3%), também vai a 100% pelo mesmo motivo —
os dois empatam em cobertura na entrada real, o segundo critério do ADR-08 ("não perder cobertura")
deixa de ser um obstáculo.

**A estabilidade, na entrada real, favorece o novo com folga maior que na wave 6** — 13 de 15
atletas (contra 8/15 na entrada de sequência), média 0.5467 contra 0.5299 (Δ = 0.0168, mais de 3× o
Δ de 0.005 que a wave 6 chamou de "dentro do ruído"). Isso é evidência real na direção de o novo
detector generalizar melhor sobre o grafo agregado (peso uniforme, sem proveniência) que sobre o
grafo rico em contagem real — plausível: perder a variação de peso tira precisamente o sinal que dá
ao greedy-modularity antigo sua vantagem de "absorver" nós de baixo grau, ao passo que o Louvain do
novo detector é menos dependente dessa variação. Mas — ver a ressalva do bootstrap acima — esta
estabilidade foi medida com reamostragem por aresta, não por luta, porque a entrada real não tem
unidade por luta. Não é uma segunda wave 6 com o mesmo rigor; é a melhor medição possível com o dado
que existe hoje, e aponta na mesma direção em 13 dos 15 casos, o que é mais que ruído de amostragem
isolado poderia produzir de forma consistente.

**Achado à parte, do próprio ticket: as duas entradas produzem partições visivelmente diferentes
para o MESMO atleta com o MESMO detector.** O cross-jaccard do detector antigo contra si mesmo
(mesmo atleta, grafo de sequência vs. grafo de produção) tem média 0.637 e vai de 0.368 (Craig
Jones) a 0.903 (Kade Ruotolo) — nem colapsa para 0 (as estruturas concordam mais que discordam, em
geral) nem chega perto de 1 (não são a mesma resposta). **O que o produto mostra hoje sobre os
"sistemas" de um atleta depende tanto de qual grafo alimenta o detector quanto de qual detector é
usado** — a pergunta do ticket original tinha razão em suspeitar disso.

### Isso muda o ADR-08?

**Sim, na base empírica — não decreto a mudança de decisão aqui.** O critério do ADR-08 é "só
substitui se ganhar em estabilidade E não perder em cobertura". Medido na entrada real:

1. **Cobertura — deixa de ser um obstáculo.** A wave 6 fechou a decisão citando isso "sozinho": 86.6%
   vs 96.3%, perda mensurável. Na entrada real, cobertura empata em 100% dos dois lados, por um
   motivo estrutural (nó sem aresta não existe nessa entrada), não por escolha de parâmetro. Essa
   razão de rejeição, especificamente, não se sustenta contra o dado real.
2. **Estabilidade — deixa de ser empate, mas com instrumento mais fraco.** 13/15 e Δ 3× maior que a
   wave 6 é uma direção consistente, não ruído — mas foi medida com bootstrap por aresta, não por
   luta, porque a entrada real não guarda proveniência por luta. Não tem o mesmo peso probatório que
   a wave 6 teve ao isolar o algoritmo.

**Proposta de texto de revisão para o bloco de medição do ADR-08** (`01_DECISOES.md`, a inserir
como novo parágrafo após o já existente "Medido na wave 6" — não substituindo aquele parágrafo,
que registra o que foi medido e por quê):

> **Revisão em 2026-08-17 (wave 6b), medição na entrada real de produção.** A wave 6 mediu os dois
> detectores no mesmo grafo derivado de sequência, deliberadamente — e apontou como não medido que
> `athlete_systems.py` em produção consome outra coisa. Medido em
> `05_COMPARACAO_DETECTORES.md`#wave-6b: na entrada real (`graphs`/`graph_edges` persistido via
> `export/ontology.py`), a cobertura EMPATA em 100% dos dois lados (o défice de 86.6% vs 96.3% da
> wave 6 era propriedade do grafo de sequência — nós de grau 0 só existem lá) e a estabilidade
> passa a favorecer o novo detector em 13 de 15 atletas (Δ médio 0.017, contra 0.005/8-de-15 na
> wave 6). A razão de cobertura que fechou a decisão da wave 6 não se sustenta contra o dado real.
> A vantagem de estabilidade é mais forte na entrada real, mas foi medida com bootstrap por aresta
> (a entrada real não tem proveniência por luta para reamostrar), instrumento estruturalmente mais
> fraco que o bootstrap por luta da wave 6 — **não** tem o mesmo peso probatório. **Reabrir a
> decisão de coexistência, condicionado a**: (a) dar proveniência por luta ao `graph_edges`
> persistido (ou replay que a preserve) para que a estabilidade na entrada real possa ser
> bootstrapada por luta, no mesmo padrão de rigor da wave 6; e (b) revalidar em corpus maior que os
> 15 atletas medidos aqui. Até essa remedição, o ADR-08 permanece "coexistir" por decisão
> declarada, não por evidência ainda intacta — a evidência que o justificava (cobertura) mudou de
> lado.

Esta seção só propõe o texto — aplicar a revisão em `01_DECISOES.md` (e decidir se/quando dar
proveniência por luta ao `graph_edges`) é uma decisão de produto fora do escopo desta wave, e
`analysis/athlete_systems.py` continua proibido de editar aqui.

### O que não mudou (nesta wave também)

- `analysis/athlete_systems.py` — intocado.
- Nenhuma escrita no banco. Leitura via `db.base.get_session_factory` só com `SELECT`.
- `analysis/archetype.py` — fora de escopo, como na wave 6.
