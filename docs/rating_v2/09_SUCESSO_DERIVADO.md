# 09 — Sucesso derivado e graduado (D7)

**Registro de design. Escrito ANTES da implementação, medido offline, pré-registrado.**
Decisão do dono (2026-08-27), dentro da migração de taxonomia ações/estados (D1–D6, plano
`FRENTE A`). Este doc é o D7 que aquele plano remeteu ao architect.

> **A decisão do dono, destilada.** Tirar `successful`/`unsuccessful` do sequence builder do App.
> O sucesso de uma ação passa a ser avaliado **pela própria cadeia** — o estado alcançado depois
> julga a ação (tentou pegada de costas e não pegou as costas ⇒ a pegada de costas foi ruim).
> Sucesso não é mais booleano: é medido pela **progressão** (pegada de costas que leva direto a
> finalização = muito bom). Única exceção com prompt: **finalização no fim da cadeia** — o toggle
> permanece no builder só aí.

Todos os números deste doc foram medidos offline em 2026-08-27, **sem tocar o banco de produção**,
sobre `/_analytics_export.json` (281 lutas, 2421 eventos, dump plano do corpus) mais os artefatos
versionados dos dois repos. Onde há um número de produção conhecido (ADR-16, 2026-08-26) ele está
citado ao lado, para o leitor calibrar o quanto a amostra representa o corpus inteiro.

---

## Sumário executivo

| Pergunta | Resposta |
|---|---|
| Forma do score | **Binário derivado, `s ∈ {0, 1}`**, com uma cadeia de fontes em três níveis (`terminal → target_state → next_action → none`), no molde do `rrb_progression.VALUE_SOURCES` |
| Score fracionário | **REJEITADO** — medido: RD encolhe exatamente igual para `s = 0,5` e para `s = 1`, então um score fracionário compra confiança sem afirmar nada (§3) |
| Graduação | Realizada como **densidade de acertos na cadeia**, não como magnitude de um score. A tabela RRB continua sendo **peso**, nunca score (§2.4) |
| PtV | **FORA do score v1.** A cicatriz de não-determinismo foi reproduzida e a **causa-raiz é outra do que a registrada** (§4). Conserto verificado em 2 mudanças pequenas, mas é PR próprio |
| População de evidência | **972 observações derivadas contra 654 com flag (×1,49)**; 129 pares (atleta, nó) contra 105 (§5) |
| Escala | Analytics: **zero movimento** em `athletes.elo`/`user_elo`/`elo_series` (o track global é por LUTA e nunca lê observação de nó). App: **−202 pontos (−15,3%)** no global, com **σ preservado** (60,3 → 63,7) (§5.3) |
| Flag no corpus | **Derivar sempre, nos dois lados.** Medido: flag e derivação concordam em **51,2%** — são medidas diferentes, e misturá-las é a mesma falha de duas unidades numa distribuição que este repo já mediu três vezes (§5.4) |
| Faseamento | **Fase 3c**, landing próprio, DEPOIS de 3a/3b. O gate "evidência intocada" da 3a não sobrevive ao D7 e não deve ser reescrito para acomodá-lo (§7) |

Duas decisões precisam do dono e estão marcadas 🔶 no texto: **§2.3** (finalização não-terminal
pontua 0) e **§5.3** (aceitar o degrau de −15% no global do App).

---

## 1. O que muda de fato, e o que explicitamente não muda

Depois de D1–D6 a cadeia é `estado → ação → estado`. Uma aresta de ação tem, por construção,
uma ponta de origem e uma ponta de destino. O D7 é **uma função pura sobre essa janela**:

```
score(edge) = f(estado_origem, família_da_ação, estado_destino, terminal_da_luta)
```

Nada mais entra. Em particular **não** entram: o resultado da luta (isso é o track GLOBAL),
a força do adversário (idem, ADR-16), o rating atual do nó, e nenhum número que já tenha
saído desta mesma engine (ADR-12 — número nunca semeia número).

**Não muda:**

- `analysis/rating_v2/glicko2.py` e `services/rating/glicko2.ts` — matemática intocada, fixtures
  golden intocadas.
- O contrato Markov (`relative_shares` / `relativeShares`, `markovWeightsGolden.json`,
  `markov_weights_golden.json`). A **invariante média-1 continua valendo**: a evidência total de
  uma luta/round segue `WEIGHT × n`, só a divisão entre ações se move.
- `lamas_state` / `lamasCode` — **continuam lendo a flag `successful`**. Ver §6.3: repartir
  attempt/success pela derivação obrigaria a reconstruir `markov_action_weights.json` e a
  reinterpretar o relatório BracketAnalysis inteiro. Isso é uma segunda decisão, com outro raio
  de explosão, e não faz parte desta.
- A escala do rating (ADR-02 / D5). Do lado Analytics ela literalmente não pode se mover (§5.3).

---

## 2. A função de score

### 2.1 A cadeia de fontes, e por que ela é uma cadeia

Uma ação nem sempre tem um estado de destino legível. Em vez de inventar um, o score resolve por
**níveis declarados, na ordem**, e **publica qual nível disparou** — exatamente o dispositivo que
`analysis/rrb_progression.value_table` já usa (`VALUE_SOURCES`), pelo mesmo motivo: um consumidor
tem de conseguir recusar um valor sabendo de onde ele veio.

```python
SCORE_SOURCES = ("terminal", "target_state", "next_action", "none")
```

| Nível | Quando dispara | `s` |
|---|---|---|
| `terminal` | A ação é a finalização que **encerrou a luta**, pela regra do §2.3 | `1.0` |
| `target_state` | Existe um estado de destino legível na janela | `1.0` se a classe do estado ∈ classe esperada da família **e** o estado é do próprio atleta; `0.0` caso contrário |
| `next_action` | Não há estado, mas há uma ação seguinte na janela | `1.0` se a próxima ação é **do mesmo atleta** **e** de família **diferente**; `0.0` caso contrário |
| `none` | Nada disso | **nenhuma observação** |

`none` é uma **recusa, não um zero**. É a mesma regra que o ADR-06 fixou para vencedor ausente, que
o ADR-16 desceu um nível para `successful` nulo, e que `transitions/build_graph.py` aplica como
"aparição sem sucessora fica fora do denominador". Esta é a quarta vez que o repo escreve a mesma
regra; escrevê-la de novo é o custo de não a esquecer.

### 2.2 Janela de lookahead — só o próximo estado, sem desconto

A janela vai da ação até **a primeira das três coisas**: um evento de estado, a próxima ação
(de quem for), ou uma fronteira (`reset`/`referee`/`match`, e no App a fronteira de round —
`perspective_sequence.sequence_boundaries`, que já existe). Nunca atravessa uma fronteira.

**Sem desconto, sem soma sobre o resto da cadeia.** Três motivos, em ordem de peso:

1. Um score descontado sobre o resto da cadeia dá crédito a uma ação por coisas que outras ações
   fizeram — é exatamente a leitura empírica que `lamas_chain.rrb` **nomeou e recusou publicar**
   ("credita cada aparição com o final da própria luta ... o n efetivo é o número de lutas
   absorventes, quatro a seis, usando uma contagem de aparições de até sessenta e sete como se
   fosse o n").
2. Um horizonte descontado carrega um γ, que é uma constante não calibrada a mais (ADR-13), num
   corpus onde o ADR-03 já mediu que `tau` é não-identificável.
3. A progressão longa **já está representada** — como mais observações de sucesso ao longo da
   cadeia (§2.4), que é o que o Glicko soma sozinho.

⚠️ **Uma ação cujo destino é um estado genérico de FALLBACK do compilador (`scramble`, ou a ação
genérica `transition`) não produz observação de `target_state`** — cai para `next_action`. O
`inferred=True` que D2 já carrega na aresta/no nó é o campo que decide isso; não é preciso inventar
nada. Um estado genérico com significado (`chained-submission`, `top-transition`,
`guard-transition`) pontua normalmente.

### 2.3 O caso terminal — a única exceção com prompt

**No corpus:** `win_type == 'SUBMISSION'` **e** a ação é o último passo da cadeia **e** o ator é o
`winner`. As três condições juntas, e o lado vem do **`winner` da luta, nunca do `actor_id` do
evento** — `lamas_chain._absorbing_side` já mediu que **7 das 24** cadeias que o ciclo ADCC trunca
numa SUB marcada arquivam essa finalização sob quem **perdeu**.

Este é o mesmo fato que o contra-exemplo da Amy Campo registra
(`docs/research/lamas_chain_divisions.md:148`): uma luta perdida por DECISÃO carrega
`submission/Knee Bar successful=true` e roda por mais dezessete eventos. **`successful=true` numa
finalização significa que a chave foi encaixada, não que houve toque.** É por isso que a flag não
pode ser o marcador terminal e o resultado da luta pode.

**No App:** o toggle sobrevive **só** neste caso — uma finalização no fim da cadeia. É a única
informação que a sequência não consegue derivar, porque no treino não existe `win_type`.

🔶 **Decisão que precisa do dono: uma finalização NÃO terminal pontua `0`.**
Segue literalmente a regra do dono ("tentou X e não conseguiu X ⇒ X ruim") aplicada por simetria à
finalização. A consequência é grande e está medida: a taxa de sucesso da família `submission` cai
para **0,218** contra 0,459 de flag no corpus. Na prática o nó de finalização passa a responder
"com que frequência a minha finalização **acaba a luta**" em vez de "com que frequência eu
**encaixo**". É uma pergunta mais dura e, na nossa leitura, mais honesta — mas é uma **mudança de
significado do número**, e a alternativa (finalização não-terminal ⇒ **nenhuma observação**) é
igualmente defensável e mais conservadora. Recomendação: pontuar `0`. O dono decide.

### 2.4 Graduação — onde ela vive de verdade

O pedido é "pegada de costas que leva direto a finalização = muito bom". No modelo acima isso
aparece assim:

| cadeia | observações geradas |
|---|---|
| pegada de costas → **costas** → finalização que acaba a luta | `back_take s=1`, `submission s=1` (terminal) |
| pegada de costas → **costas** → nada | `back_take s=1` |
| pegada de costas → o adversário age | `back_take s=0` |

**A graduação é a DENSIDADE de acertos ao longo da cadeia, não a magnitude de um score.** É a forma
que o Glicko-2 carrega honestamente, e é a forma que soma sozinha: duas observações de sucesso
movem o rating mais do que uma. Nada precisa ser inventado para isso funcionar.

**A tabela de valor RRB continua sendo PESO, nunca score.** `rrb_progression` avisa por escrito que
a amplitude dela é pequena de propósito (§8.6: a cadeia mistura mais rápido do que absorve) e que
"consumidor que precisar de mais contraste está aplicando uma transformação própria e precisa
justificá-la no PR dele". §3 mede o que acontece a quem ignora esse aviso.

**Candidato de v2, explicitamente fora do v1:** modular o peso por `w(código_destino) /
w(código_origem)` e **renormalizar para média 1**, preservando a invariante. Amplitude real desse
fator com o bloco `global` de hoje: `0,4752…0,8065` ⇒ razão entre ~0,59 e ~1,70. Vale medir depois
que a camada estiver no ar e houver corpus; não vale abrir o v1 com mais uma constante.

---

## 3. Score fracionário no Glicko-2 — a matemática permite, a medição desaconselha

**A matemática permite, e isso já está estabelecido nos dois repos.** `update_period` multiplica o
termo de informação `g²·E·(1−E)` e o resíduo `g·(s−E)` pelo **mesmo** fator `weight`, então peso
inteiro é aritmeticamente idêntico a repetir a observação e o caso fracionário é a extensão contínua
dessa identidade (ADR-16 item 4, travado por
`test_weight_is_repeat_count_expansion_for_an_integer_weight`). O campo `score` do `Observation` é um
`float` e o próprio Glickman usa `s = 0.5` para empate; `s` fracionário é literatura padrão
(Glickman 1999/2013; a mesma leitura que o TrueSkill dá a resultados parciais).

**O problema não é a matemática. É que o resíduo e a informação se desacoplam.**
Medido com o core real (`analysis/rating_v2/glicko2.update_period`), nó semeado no global do App
(1288,5), RD 350, 8 observações de peso 0,1:

| `s` | rating final | movimento | **RD final** |
|---:|---:|---:|---:|
| 0,000 | 1132,7 | −155,8 | **260,083** |
| 0,372 | 1248,6 | −39,9 | **260,083** |
| 0,500 | 1288,5 | 0,0 | **260,083** |
| 1,000 | 1444,3 | +155,8 | **260,083** |

**O RD é idêntico até a terceira casa nos quatro casos.** Um score de 0,5 encolhe a incerteza
exatamente tanto quanto um resultado decisivo, afirmando nada. Isso é literalmente o que o ADR-06
recusa quando se nega a transformar 271 decisões em empates ("empate fabricado não é neutro: ele
*ancora* o atleta no prior e fabrica confiança sem evidência") — e o que o ADR-16 repetiu um nível
abaixo para `successful` nulo.

**E a variante concreta que estava na mesa — usar a tabela RRB como score — foi medida e é pior
ainda.** Mesmas 8 observações, `s = sub_share(estado)`:

| estado | `s` | rating final | movimento |
|---|---:|---:|---:|
| PGD | 0,4752 | 1280,8 | **−7,7** |
| CDP | 0,4976 | 1287,8 | **−0,7** |
| TKD | 0,5262 | 1296,7 | **+8,2** |
| BTK | 0,5977 | 1318,9 | **+30,4** |
| SUB | 0,8065 | 1384,0 | **+95,5** |
| *binário `s=1`* | *1,0* | *1444,3* | *+155,8* |

Fora de SUB o movimento inteiro cabe em **38 pontos**, contra σ 31,9 medido no corpus de referência
do App — ou seja, todo o sinal de doze estados vale pouco mais de um desvio-padrão, enquanto o RD
cai de 350 para 260 do mesmo jeito. **Rating quase parado, confiança comprada integralmente.**

**Veredito: `s ∈ {0, 1}`.** A continuidade que o Glicko-2 realmente oferece é o `weight`, e é lá
que a graduação já mora (contrato Markov, ADR-16). Score é resultado; peso é quanta informação
aquele resultado carrega. Misturar os dois é o que produz a tabela acima.

---

## 4. PtV — a cicatriz foi reproduzida, e a causa-raiz registrada estava errada

### 4.1 O que estava escrito

A memória do projeto registra "PtV `momentum_series` float jitter across processes is separate +
sub-rounding — did NOT churn breakdown pages in cold-vs-live, left as-is" (2026-07-07, junto do
conserto do não-determinismo dos análogos por `PYTHONHASHSEED`). Isto é: diagnosticado como ruído
de ponto flutuante, abaixo do arredondamento, sem consequência.

### 4.2 O que foi medido agora

Grafo do corpus construído a partir das mesmas 281 lutas em **5 ordens de leitura diferentes**,
`path_to_victory` rodado em cada uma, comparado com a ordem base:

```
shuffle 0:  131 de 172 nós diferem, max |Δ| = 0,158400
shuffle 1:  130 de 172 nós diferem, max |Δ| = 0,167400
shuffle 2:  133 de 172 nós diferem, max |Δ| = 0,407200
shuffle 3:  124 de 172 nós diferem, max |Δ| = 0,050800
shuffle 4:  133 de 172 nós diferem, max |Δ| = 0,300900
```

**0,4072 numa escala `[−1, +1]`, em 76% dos nós.** Não é sub-arredondamento. A primeira hipótese —
que a iteração de valor é Gauss-Seidel in-place e a ordem de varredura vem da ordem de inserção do
grafo — foi **testada e refutada**: ordenar a varredura (`for n in sorted(g)`) não mudou nada.

### 4.3 A causa-raiz de verdade

Comparando os grafos em vez dos valores:

```
shuffle 0: arestas diferem 0   nós diferem 7   TYPE difere 7
    Takedown ('transition', 'takedown')      Kimura ('transition', 'submission')
    Throw ('transition', 'takedown')         Leg Entanglement ('transition', 'guard')
    Escape ('transition', 'escape')          Ground and Pound ('control', 'strike')
    Single Leg Takedown ('transition', 'takedown')
```

**Topologia e pesos são byte-idênticos. Só o atributo `type` do nó muda.**

`analysis/transitions/build_graph.py:100` faz `node_type.setdefault(e["label"], e["type"])` —
**o primeiro tipo visto vence**. Medido: **15 de 211 rótulos do corpus carregam dois tipos
diferentes** (`Takedown` é `takedown` e `transition`; `Body Triangle` é `control` e `submission`;
`Guillotine Attempt` é `submission` e `transition`; …). Qual tipo o nó recebe depende de qual luta
foi lida primeiro — e a leitura é
`export/match_breakdown._final_matches`: `select(Match).where(Match.status == "final")`,
**sem `ORDER BY`**. O Postgres não promete ordem sem ela.

O tipo então entra em `path_to_victory` por dois caminhos: `_terminal_rate` só devolve valor
**quando `type == "submission"`** (um termo absorvente inteiro aparece ou some) e `_shaping` lê o
tipo em `_points_for_entry` e em `position_decision_space`. Cinco a sete nós trocando de tipo
propagam pelo desconto γ=0,8 até 130 nós.

**Isto está no ar.** `export/site_data.py:500` constrói `corpus_g` uma vez e passa `ptv_v` para
`match_breakdown.py:189 → ptv_momentum`, que é a série de momentum de **toda página de breakdown
publicada**.

### 4.4 Conserto verificado (2 mudanças, PR próprio, fora do D7)

1. **Resolver o tipo por MAIORIA**, desempate pelo nome do tipo (uma ordem total), em vez de
   `setdefault`. Leva de 131 nós × 0,4072 para **1 nó × 0,0001** — que é exatamente o
   sub-arredondamento que a memória descrevia.
2. **Varrer em ordem ordenada** (`for n in sorted(g)`) dentro de `path_to_victory`. Com as duas
   juntas: **0 nós diferem, `max |Δ| = 0,00000000` em 5 ordens de leitura**, no `tol=1e-6` atual.

Um `ORDER BY Match.id` em `_final_matches` é desejável de qualquer forma (a ordem de leitura não
deveria ser arbitrária em lugar nenhum), mas sozinho ele só congela uma escolha arbitrária; a
resolução por maioria é o conserto de causa-raiz.

### 4.5 Veredito do D7: PtV fica FORA do score v1

O determinismo é consertável barato — mas ele **não é o motivo principal**:

1. **PtV reintroduz o que o ADR-16 tirou.** `_shaping` chama `athlete_elo._points_for_entry`, o mapa
   de pontos de regulamento. O ADR-16 tirou o `POINT_MAP` da produção da nota do nó de propósito.
   Fazer o score depender de PtV o traz de volta pela porta dos fundos, sem que ninguém decida isso.
2. **PtV não existe no App.** É corpus-wide e derivado do banco. Um score que depende dele não pode
   ter paridade entre as duas engines — e paridade é o que este contrato inteiro existe para
   sustentar.
3. **γ = 0,8 e os pesos de shaping são constantes não ajustadas**, declaradas como tal num comentário
   `ponytail:` no próprio módulo; o PoC-E4 varreu e os defaults ficaram. Pendurar o score de nó nelas
   é abrir um ADR-13 novo sem necessidade.
4. **PtV é um prior por nó, não um resultado por instância.** Prior tem lugar: a semente. E já existe
   um (`node_elo_deviance` → `eloDeviance` na `tech_library`).

**Backlog:** PtV é candidato para a **escada de valor por ESTADO** da v2 — quando os nós forem
estados e fizer sentido perguntar "quanto vale estar aqui". Não para o score de uma ação.

---

## 5. População de evidência e escala (ADR-02)

### 5.1 Cobertura — medido

Corpus offline, 281 lutas. `_analytics_export.json`, 2078 eventos rotulados com ator resolvível,
**57,9% sem flag** (produção 2026-08-26: 67,7% — a amostra é um pouco mais anotada que o corpus
inteiro, o que torna as comparações abaixo **conservadoras**).

Sobre as **1115 instâncias de ação** com ator resolvível:

| fonte do score | n | % | taxa de sucesso | concordância com a flag |
|---|---:|---:|---:|---:|
| `terminal` | 43 | 3,9% | 1,000 | 81,4% (n=43) |
| `target_state` | 301 | 27,0% | 0,302 | 43,9% (n=132) |
| `next_action` | 628 | 56,3% | 0,349 | 51,2% (n=379) |
| `none` (recusa) | 143 | 12,8% | — | — |

| | hoje (só flag) | D7 (derivado) |
|---|---:|---:|
| observações | **654** | **972** (×1,49) |
| pares (atleta, nó) com ≥1 observação | **105** | **129** (+23%) |
| observações por par — mediana / média | 2 / 6,23 | 2 / 7,53 |
| taxa de sucesso | 0,613 (amostra) · 0,459 (prod) | **0,363** |

Por família (derivado): `pass` 0,672 · `back_take` 0,662 · `takedown` 0,630 · `guard_pull` 0,444 ·
`sweep` 0,349 · `escape` 0,297 · `submission` 0,218.

### 5.2 A medição que quase matou o design, e o que ela ensinou

A **primeira** versão desta especificação tinha só o nível `target_state`, com a regra "estado de
destino igual ao de origem ⇒ a ação não moveu nada ⇒ `s = 0`". Medida no corpus:

```
observações derivadas 344 (30,9%)   sem observação 771
razões: no_state_in_window 763  |  no_move 62  |  moved_wrong_class 148
```

**344 contra 654: a derivação PERDIA 47% da evidência**, e 100% dos zeros vinham de "não houve
evento de estado nenhum" e não de "a posição não mudou". O corpus pré-migração registra ~770 eventos
de estado para 1115 ações: **o fluxo de estados é esparso demais para carregar o score sozinho.**
Pontuar essa lacuna como `0` teria fabricado 763 fracassos — o pecado do ADR-06 em escala industrial.

Duas consequências, e as duas estão no design:

1. O nível **`next_action`** existe por causa desta medição, não por elegância. Ele é o que leva a
   cobertura de 30,9% para 87,2%.
2. **`target_state` só pode ser o nível primário depois que o compilador de cadeia (Fase 1/3) emitir
   um estado para cada aresta de ação.** Projetando a tabela de inferência do D2 sobre esta amostra:
   27,8% das ações teriam estado real, 28,4% um genérico com significado
   (`chained-submission` 224, `top-transition` 93), **27,9% o fallback `scramble`** e 15,9% nenhum
   sucessor. Ou seja: **o compilador melhora a mistura de níveis, não a cobertura total** — a
   cobertura já vem do `next_action`. Isso é um argumento a mais para o §7: o D7 depende do
   compilador para a QUALIDADE do score, não para a sua existência.

### 5.3 Escala

**Analytics: zero movimento, por construção.** O track global do Glicko é pontuado pelo RESULTADO
DA LUTA (`replay.build_bouts` / `periods.run_periods_with_snapshots`) e **nunca lê uma observação de
nó**. `project_onto_graph` escreve `graph.user_elo = global_rating`. Portanto `athletes.elo`,
`graphs.user_elo`, `elo_series` e o board público de Grappling ELO **não se movem um ponto**. Só
`computed_elo` (e por derivação `graph_edges.elo`) muda. Simulado com o core real, nó de atleta,
peso 0,25, taxa 0,459 → 0,363, âncora 1750: deslocamento de **0 a −88 pontos** conforme o número de
observações (`n=4` −87,5 · `n=8` −58,2 · `n=16` −34,9 · `n=32` −58,0; em `n ≤ 2` a quantização do
próprio experimento zera a diferença). Com mediana de **1** observação por nó no corpus (ADR-16), a
maior parte dos nós de atleta praticamente não se move — o nó de atleta continua sendo quase todo
prior, exatamente como o ADR-16 registrou.

**App: o global É a agregação dos nós, então ele se move.** `ratingV2Engine` calcula
`aggregateGlobal(nodes, seed)` = média sobre 7 eixos de médias ponderadas por precisão (`1/RD²`) dos
nós daquele eixo. Replay simulado (40 sessões × 6 entradas, 28 nós, 7 eixos, seed fixa, core real):

| | global | RD | média dos nós | **σ dos nós** |
|---|---:|---:|---:|---:|
| hoje (`undefined` = acertou, 77,3% `s=1`) | 1319,2 | 138,8 | 1318,3 | **60,3** |
| D7 (derivado, 36,3% `s=1`) | 1117,4 | 138,8 | 1118,6 | **63,7** |
| D7 com 37,5% caindo em `none` | 1142,2 | 152,2 | 1142,9 | 48,1 |

**Degrau de −201,8 pontos (−15,3%) no global. O σ sobrevive** (60,3 → 63,7, +5,6%).

🔶 **Decisão que precisa do dono: aceitar o degrau. Recomendação: aceitar, sem mitigação.**

Por quê:

- **O nível não é superfície de produto.** Regra de produto vigente e ADR-02: a apresentação é
  sempre **relativa + %** sob o rótulo "Grappling ELO", nunca o rating cru. O `ratingV2Presentation`
  rotula por **RD**, não por rating, então os níveis de confiança não se movem.
- **O que o produto lê é o σ**, e ele sobrevive: assinatura = ≥ +1σ, elo fraco = ≤ −1σ. (O número
  de assinaturas neste replay sintético muda de 7 para 3, mas isso é **uma semente só** e não é
  evidência — o σ é a quantidade estável e é ela que está reportada.)
- **O ADR-12 já reprocessa toda conta** no bump de `RATING_V2_ENGINE_VERSION`. Ninguém vê um pulo;
  vê um número novo, uma vez.
- O `CLAUDE.md` do App **já avisa** que "anything comparing `computedElo` to an absolute constant is
  wrong now". O D7 reforça o aviso, não o cria.

**Mitigações consideradas e recusadas:**

| mitigação | por que não |
|---|---|
| Recentrar o parceiro virtual pela taxa-base nova | Muda o significado do número: o nó passaria a medir "melhor que a média do corpus" em vez de "melhor que cara ou coroa", contradizendo a leitura que o ADR-16 fixou. E é uma constante ajustada nova (ADR-13) |
| Peso menor para score derivado que para terminal | Duas unidades de evidência dentro do mesmo nó — a falha que o §5.4 recusa. E não conserta a direção, só a velocidade |
| Shrink em direção ao global | O Glicko já faz isso: é o RD. Um segundo mecanismo de encolhimento é encolhimento duplo, sem ninguém saber quanto |

### 5.4 A flag continua entrando como insumo? **Não. Derivar sempre, nos dois lados.**

Medido: a derivação e a flag concordam em **43,9%** (`target_state`) e **51,2%** (`next_action`).
Cara ou coroa. **Não são duas medições ruidosas da mesma coisa; são duas coisas diferentes.** A
flag do corpus diz "a chave foi encaixada" / "o anotador viu encaixar"; a derivação diz "a cadeia
foi para onde a ação prometia". A concordância sobe para **81,4%** exatamente no nível `terminal`,
que é onde as duas perguntas coincidem — o que confirma a leitura em vez de a enfraquecer.

Preferir a flag onde ela existe e derivar onde falta colocaria **duas unidades na mesma
distribuição**, dentro do mesmo `node_key`. Este repo já mediu o custo disso três vezes:
âncora antiga contra final (`corr = −0,855`, ADR-16); baseline populacional misturando escala V1 e
V2 (ADR-16, "o z-score começa a medir de qual corpus o atleta é"); e o App com projeção parcial
(σ 354 / 0 assinaturas contra σ 31,9 / 1). Uniformidade também é a única coisa que torna a paridade
App↔Analytics verificável: **o App não tem anotador, então não tem flag para preferir.**

**A flag não é jogada fora.** Ela ganha dois empregos:

1. **Insumo do caso terminal**, no App (o toggle que sobrevive).
2. **Sinal de QA.** A concordância derivado × flag, por família, vira métrica publicada — uma luta
   cuja concordância despenca é uma luta para reanotar. Mesmo espírito do
   `analysis/match_deviance.py`: a discordância é uma lista de recheque, não um erro.

---

## 6. Attempt/success derivado, e o que ele toca

### 6.1 O mapa família → classe de estado esperada

Fixture-pinned nos dois repos, no regime do `markovWeightsGolden` / `taxonomyKindGolden`: gerador
único em Analytics, `--check` byte-idêntico dos dois lados.

**Classes de estado** (avaliadas **relativas ao atleta que agiu** — a convenção `actor` = a atleta de
cujo JOGO o nó é, de `docs/match_event_model.md`):

| classe | vocabulário medido no corpus |
|---|---|
| `BACK` | back control, back take, hooks in, body triangle, rear body lock, standing back control |
| `TOP` | mount, side control, north-south, knee on belly, top control, body lock (top), half guard control |
| `TURTLE` | turtle, escape to turtle |
| `LEG` | leg entanglement, 50/50, single leg X, saddle, ashi |
| `GUARD` | toda postura de guarda |
| `NEUTRAL` | clinch, collar tie, front headlock, standing |

| família da ação | classe(s) esperada(s), do próprio atleta |
|---|---|
| `takedown` | `TOP`, `BACK`, `LEG` |
| `sweep` | `TOP`, `BACK`, `LEG` |
| `pass` | `TOP`, `BACK` |
| `guard_pull` | `GUARD`, `LEG` |
| `back_take` | `BACK` |
| `submission` | **só terminal** (§2.3) |
| `escape` | *(regra própria)* qualquer classe **≠** a classe de origem — sair É a semântica da palavra, e não precisa de ordenação |
| `transition` e qualquer família fora da tabela | **nenhuma** ⇒ cai para `next_action` |

Três propriedades deliberadas:

- **Chegar mais alto continua sendo sucesso.** `pass → BACK` é `1`. A tabela é um piso, não uma
  igualdade. Isso é o que faz "progressão" e "sucesso" não brigarem.
- **A tabela é mantida por MEDIÇÃO.** Todo rótulo de estado que não casar com nenhuma classe é
  contado e publicado (`unmapped_states`, o mesmo dispositivo do `skipped_labels` do `lamas_chain`).
  Medido nesta amostra: **2 rótulos em 770 eventos de estado** (`Body Triangle (Bottom)`,
  `Ground and Pound`). Rótulo não mapeado ⇒ **nenhuma observação**, nunca um zero.
- **Colisão medida herdada:** `body triangle (bottom)` é quem está EMBAIXO de um triângulo de corpo,
  o oposto de uma pegada de costas. Já está em `lamas_chain.LABEL_OVERRIDES`; a mesma entrada vale
  aqui.

### 6.2 Escada de valor por estado — v2, não v1

A forma "certa" da tabela acima é uma **escada medida**: rodar `lamas_chain.rrb` sobre o espaço de
ESTADOS do compilador novo e ler `sub_share(estado)`, obtendo uma ordem em vez de um conjunto. É o
mesmo código, outro espaço de estados, os mesmos portões.

**Não no v1**, por três razões: `rrb` hoje hardcoda `STATES`/`STATE_INDEX` (precisa ser
parametrizado); a evidência absorvente do corpus é de **4 a 6 lutas por recorte** e ZERO no Mundial
2024, o que já é o gate mais frágil do relatório; e o `WEIGHT_FLOOR` do `rrb_progression` avisa
que a amplitude é pequena de propósito. Um conjunto explícito, revisável e fixture-pinned é a
escolha lazy correta enquanto o corpus for este.

### 6.3 O que NÃO é retocado: `lamas_state` continua lendo a flag

Poderia parecer natural fazer o `lamas_state` repartir attempt/success pela derivação — TKDA/TKD
sairiam do score em vez da flag. **Recusado no v1.** Consequências que isso arrastaria:

- `chain_of` muda ⇒ `rrb`/`reward_risk`/`chain_factor` mudam ⇒ **`rrb_progression.value_table` muda**
  ⇒ **`data/rating/markov_action_weights.json` tem de ser reconstruído** e as duas fixtures golden
  regeneradas (contrato cross-repo, §7 do `CLAUDE.md` raiz).
- O relatório BracketAnalysis inteiro muda de significado. O `lamas_chain` **avisa por escrito** que
  "a partição attempt/success segue o LOTE DE ANOTAÇÃO" e que os blocos são comparáveis DENTRO e não
  ENTRE si; trocar a partição debaixo do relatório publicado é outra decisão.

**Não há circularidade** nisso: a derivação lê o estado alcançado e nunca lê um peso. Score →
peso é um caminho acíclico numa passada.

**Teto declarado (ponytail).** Com `lamas_state` intocado, o PESO de uma observação continua
dependendo de uma flag que o SCORE não usa mais. É defensável — score é o que aconteceu, peso é
quanto aquela classe de ação vale como evidência, medida sob a sua própria convenção; e o
`node_rating.py` **já documenta exatamente essa divergência** ("which is why the code lookup and the
score read the same field differently and correctly"). O D7 alarga a divergência, não a cria.
**Caminho de upgrade:** depois que o compilador estiver no ar, reconstruir os pesos sobre códigos
derivados e regenerar as fixtures dos dois lados — PR próprio, medível, com `--check` como gate.

### 6.4 Consumidores de `successful` que precisam de revisão

`successful` é lido em ~25 módulos de `analysis/`+`export/`. O D7 **não** os toca — ele adiciona uma
função de score nova. Mas a revisão da **Fase 4** (que já existe no plano) tem de incluir a pergunta
"este consumidor devia estar lendo o derivado?" para, pelo menos:

| consumidor | por que importa |
|---|---|
| `transitions/build_graph.py` (`ok` da aresta) | alimenta `edge_dashed` ⇒ **contrato cross-módulo com `services/directedEdges.ts`** (`DASH_SUCCESS_MAX`). Mudar o lado de cá sem o de lá quebra a paridade |
| `analysis/defense_rate.py`, `scouting_tables.py`, `category_profile.py` | taxas publicadas no relatório de categoria |
| `analysis/decision_flow._is_successful` | tri-estado próprio (flag, ou a finalização vencedora); é o parente mais próximo do D7 e o candidato mais óbvio a convergir |
| `path_to_victory._terminal_rate` (via `ok_count`) | ver §4 — este já tem um defeito próprio |

---

## 7. Faseamento, riscos e gates

### 7.1 Onde o D7 entra: **Fase 3c**, landing próprio

O plano define a Fase 3 como duas landings independentes (3a App, 3b Analytics) e dá à 3a este
gate:

> **Gate de não-degradação**: teste pinando contagem de observações + rating global no corpus de
> referência antes/depois (**idênticos — evidência intocada**).

**O D7 viola esse gate por construção** — ele existe justamente para mexer na contagem de
observações e no score. Duas saídas:

1. Reescrever o gate da 3a para tolerar movimento. **Não.** Esse gate é a coisa mais valiosa da 3a:
   ele prova que a migração de taxonomia é uma mudança de ESTRUTURA e não de NÚMERO. Perder essa
   prova para acomodar o D7 é trocar o ativo pelo passivo.
2. **D7 = Fase 3c, landing separado, depois de 3a e 3b.** A 3a mantém "evidência intocada,
   byte-idêntico". A 3c é a única landing que move números, e chega com o seu próprio bump de
   `RATING_V2_ENGINE_VERSION`, o seu próprio replay (ADR-12) e o seu próprio gate.

**Recomendação: (2).** A UI (§7.2) pode ir junto da 3a — ela não muda nenhum número enquanto o
score ainda for a flag; entradas novas passam a nascer com `successful: true` e o replay é idêntico.

A 3c depende da 3a/3b porque o nível `target_state` só é bom depois que o compilador emitir estados
(§5.2). O nível `next_action` funcionaria hoje, mas fatiar o D7 nos dois níveis daria **dois** bumps
de versão e **dois** replays de todo o corpus e de toda conta, para uma feature só.

### 7.2 App — o que sai do builder, o que fica, e o legado

| arquivo | o que fazer |
|---|---|
| `src/components/session/sequence/SequenceStepCard.tsx:253` | O pill `landed`/`missed` (linha 97 `isSuccess`, linha 253 `onUpdate(index, { successful: !isSuccess })`, linha 260 os rótulos). **Sai**, exceto quando o passo é do tipo `submission` **e** é o último passo válido da cadeia |
| `src/components/session/sequence/sequenceChain.ts` | `successful: true` continua sendo o default de `appendSuggestion` / `appendEmptyStep` / `normalizeChain` / `toChainOut`. **Nada muda aqui** — o campo continua existindo, só deixa de ser editável fora do terminal |
| `src/screens/session/RoundSheet.tsx:184` | `successful: n.successful !== false` — **intocado** |
| `src/types/session.ts` `RoundEntry.successful` | **Permanece no tipo.** Sessões antigas o carregam e o caso terminal o usa. Removê-lo seria uma migração de dados destrutiva para ganhar um campo |
| i18n | as chaves `landed`/`missed` sobrevivem para o toggle terminal; nenhuma remoção de chave |

**Sessões legadas com flags no replay.** Coerente com o §5.4: **a flag legada é ignorada pelo score**
e a derivação roda sobre a sequência gravada, que continua inteira no AsyncStorage (ADR-12 — a
sessão é o dado original e permanece intocada). Não há código de migração de dados: o bump de
`RATING_V2_ENGINE_VERSION` reprocessa toda conta a partir da fonte, que é exatamente o mecanismo
que o ADR-12 criou para isto. A única leitura legada preservada é a do caso terminal — uma
finalização no fim de uma cadeia antiga com `successful === true` conta como terminal, porque é
literalmente a mesma informação que o toggle novo grava.

### 7.3 Riscos

| risco | tamanho | mitigação |
|---|---|---|
| Degrau de −15% no global do App | **alto, visível ao dono** | §5.3: aceitar, apresentação é relativa; ADR-12 replay; medir σ antes/depois no corpus de referência |
| Nó de finalização despenca (taxa 0,459 → 0,218) | **alto** | 🔶 decisão do dono no §2.3; alternativa conservadora nomeada |
| `next_action` carrega 56% das observações e é o nível mais fraco | médio | `score_source` viaja em toda observação; `n_by_source` por nó, para um consumidor poder recusar. Encolhe sozinho conforme o compilador emite estados |
| A tabela de classes envelhece com o vocabulário | médio | `unmapped_states` publicado + `--check` da fixture no CI dos dois repos |
| Divergência App × Analytics no score | **alto** (é o contrato) | Fixture de vetores de teste compartilhada, gerador único, `--check` byte-idêntico. Mesmo regime do `markovWeightsGolden` |
| PtV não-determinístico continua no ar | médio, **já no ar hoje** | §4.4, PR separado, não bloqueia o D7 |
| `edge_dashed` sai de paridade com `directedEdges.ts` | médio | não mexer nele no D7; entra na revisão da Fase 4 como decisão explícita |

### 7.4 Gates de verificação da 3c

1. **Paridade** — vetores de teste compartilhados: mesma janela, mesmo score, mesma fonte, mesmo
   nível de recusa. Byte-idênticos nos dois repos (`--check`).
2. **Determinismo** — dois replays completos do corpus produzem `bouts_hash` e `computed_elo`
   idênticos; o replay do App é byte-idêntico em duas execuções (a propriedade que a wave 7 já
   provou para o track global).
3. **Não-degradação do rating global**, com a assimetria explícita nos dois lados:
   - **Analytics:** `athletes.elo`, `graphs.user_elo`, `elo_series` **idênticos ao epsilon**. Não é
     uma expectativa, é uma consequência estrutural (§5.3) — se moverem, alguma coisa passou a ler
     observação de nó no track global e isso é um defeito.
   - **App:** o global **move**, e o gate é sobre a **DISPERSÃO**: σ dos nós dentro de ±25% do
     valor de hoje, e a contagem de assinaturas/elos fracos publicada antes/depois no corpus de
     referência (`ratingV2Churn.test.ts` é onde isso já mora).
4. **Cobertura** — `score_source` reportado por replay; `none` acima de ~20% no corpus é motivo de
   parar e olhar, não de seguir.
5. **Recusa nunca vira zero** — teste explícito: rótulo de estado não mapeado, ação sem sucessor,
   fronteira de round e genérico de fallback produzem **zero observações**, não observações com
   `s = 0`. É a regra que este documento mais repete e a mais fácil de perder num refactor.

---

## Procedência

- Medições offline de 2026-08-27 sobre `/_analytics_export.json` (281 lutas, 2421 eventos) e sobre
  `data/rating/markov_action_weights.json` (versão 1, corpus de 913 lutas, `generated`
  2026-08-26T14:44:05Z). **Nenhuma leitura de produção, nenhuma escrita em lugar nenhum.**
- Simulações de rating com o core real `analysis/rating_v2/glicko2.update_period`, que é o gêmeo
  fixture-pinned do `services/rating/glicko2.ts`.
- Números de produção citados vêm do ADR-16 (`01_DECISOES.md`, medições de 2026-08-26) e do
  `CLAUDE.md` do App (corpus de referência: global 1288,5, σ 31,9).
- A amostra offline é **mais anotada** que o corpus de produção (57,9% sem flag contra 67,7%), então
  toda comparação "derivado contra flag" deste doc é **conservadora**: no corpus real a derivação
  ganha mais terreno, não menos.

**Classe de privacidade A, dados públicos de competição** — todo insumo é linha de `matches` de
imagem publicada. Nada aqui lê grafo de usuário nem sessão. Do lado do App, o score derivado é
calculado no device sobre a sessão do próprio dono e nunca sai dele.
