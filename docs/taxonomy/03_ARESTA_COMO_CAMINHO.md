# Aresta = caminho — o contrato `actions[]` (Fase 0)

Fase 0 do plano "aresta como caminho": introduzir `actions[]` na `Transition` como mudança
**aditiva e retrocompatível**. O compilador continua emitindo exatamente uma ação por aresta —
saída canônica idêntica à de antes, evento por evento. Nada migra até esta fase fechar (pedido
explícito do dono: *"antes de qualquer migração, adicionem testes de paridade"*).

Este documento é o contrato: a forma da `Transition`, as três invariantes que ele fixa, e o
inventário de quem lê dado de ação hoje — verificado por grep, não copiado do plano.

---

## 1. A forma

Alvo do produto inteiro (App + Analytics + site), especificado no plano do dono:

```
Transition {
  sourceState, targetState,            # sempre observados (ou âncora de apresentação)
  actions: [ {key, label, type, actor, inferred, sourceEventIndex} ],   # ordenada
  terminal, occurrenceId
}
```

**O que existe hoje, nesta Fase 0**, do lado Analytics (`analysis/chain_compiler.py`):

```python
@dataclass(frozen=True)
class ChainAction:
    key: str
    label: str
    type: str
    actor: str | None
    inferred: bool
    source_event_index: int | None


@dataclass(frozen=True)
class ChainEdge:
    source_key: str
    target_key: str
    actions: tuple[ChainAction, ...]   # sempre 1 elemento nesta fase
    terminal: bool

    # adaptador de compatibilidade — cada um é uma @property lendo actions[0]
    action_key: str        # -> actions[0].key
    action_label: str      # -> actions[0].label
    action_type: str       # -> actions[0].type
    actor: str | None      # -> actions[0].actor
    inferred: bool         # -> actions[0].inferred
    source_event_index: int | None  # -> actions[0].source_event_index
```

`occurrenceId` do plano não tem equivalente ainda neste repo — `ChainEdge` não carrega
identidade de ocorrência própria (ela é implícita na posição da aresta na lista `CompiledChain.edges`).
Não introduzido nesta fase porque nenhum teste de paridade exige — registrado aqui para não se
perder quando a Fase 1+ precisar dele.

`terminal` já existia e é campo de nível de ARESTA (não de ação individual) — não migrou.

---

## 2. As três invariantes

1. **Identidade canônica da ação não muda.** Continua `canonicalize(_normalize_name(label))`
   (`analysis/names.py`) / `normalizeLabel()` no App. É a âncora de compatibilidade inteira: tudo
   que hoje chaveia por `node_key` de ação (rating, Markov, dossiê) continua vendo a mesma chave,
   porque `ChainAction.key` é derivado exatamente como `ChainEdge.action_key` sempre foi.

2. **Relação canônica ≠ ocorrência.** `(source_key, target_key)` é a relação; cada ocorrência
   (cada `ChainEdge` individual na lista `CompiledChain.edges`) guarda seu próprio `actions`. Duas
   passagens diferentes de Guarda Fechada para Montada são uma relação e duas ocorrências — o
   compilador já produz isso hoje (uma `ChainEdge` por travessia real), esta fase não muda essa
   parte.

3. **Ninguém depende do índice de uma ação.** Uma ação inferida pode ser inserida no meio de
   `actions[]` sem que a saída de um consumidor CONFORME mude — ver `tests/test_actions_parity.py`
   (P3). Consumidores que hoje só leem `actions[0]` (via o adaptador escalar) NÃO são conformes
   ainda; ver §4.

**Adaptador de compatibilidade.** Não existe uma função `read_transition` separada neste repo —
o próprio `ChainEdge` É o adaptador: as seis propriedades escalares leem `actions[0]` e produzem
exatamente o que um chamador via campos escalares sempre leu (`tests/test_actions_parity.py::test_p4_*`).

---

## 3. Testes de paridade (`tests/test_actions_parity.py`)

| # | O que prova |
|---|---|
| **P1** | `test_p1_action_multiset_matches_golden` — o multiconjunto `(action_key, actor) -> count` produzido compilando o `mock_user_bundle.json` do App é idêntico ao golden gravado ANTES desta mudança (`scripts/export_actions_parity_fixtures.py`, `data/rating/actions_parity_golden.json`). A tally caminha por `edge.actions` inteiro, nunca por `edge.action_key`, então continua sendo prova real quando uma aresta passar a carregar mais de uma ação. |
| **P2** | `test_p2_observations_for_side_is_byte_identical_pinned` — golden pin de `analysis.rating_v2.node_rating.observations_for_side` sobre um bout de fixture. Esta função lê os eventos CRUS do `sequence` — nunca `ChainEdge`/`actions[]` — e por isso este teste não pode mudar em NENHUMA fase da migração; se precisar mudar, algo passou a alcançar o input desta função pelo caminho errado. |
| **P3** | Três testes: (a) um leitor que consome `actions[]` inteiro é invariante à posição da ação inferida no meio; (b) `observations_for_side` está estruturalmente isento (nunca vê `ChainEdge`); (c) `scripts.render_map_prototypes.Aggregate.add_edge` é **DEPENDENTE DE POSIÇÃO, HOJE, DOCUMENTADO E NÃO CONSERTADO** — ver §4. |
| **P4** | `test_p4_legacy_scalar_fields_round_trip_through_the_actions_adapter` + `test_p4_compile_chain_output_is_still_single_action_edges` — um registro construído com os seis campos escalares de sempre lê de volta idêntico através do adaptador, e `compile_chain` continua emitindo exatamente uma ação por aresta. |

---

## 4. Inventário de consumidores — verificado por grep, 2026-08-31

Cada linha abaixo foi conferida contra o código nesta sessão, não copiada às cegas do plano.
Correções ao inventário original do plano estão marcadas **[CORRIGIDO]**.

### Consomem `edge.action*` escalar (via o adaptador — leem só `actions[0]`)

| Repo | Local | Confirmado | Destino |
|---|---|---|---|
| Analytics | `analysis/chain_compiler.py` — `ChainEdge` (agora `:148-201` aprox.), `_edge_from_pending` | ✅ | vira `actions[]` — FEITO nesta fase |
| Analytics | `scripts/render_map_prototypes.py` — `Aggregate.add_edge` (`:489-`, chave `(source, target, action_key, actor)`) | ✅ confirmado — `key = (source_key, target_key, e.action_key, e.actor)`, ambos lidos via o adaptador escalar | **conhecidamente dependente de posição** — ver P3(c) abaixo |
| Analytics | `scripts/render_map_prototypes.py` — `_index_parallel_links` (`:359-379`) | ✅ confirmado, mas opera sobre DICTS já achatados por `Aggregate` (`link["from"]`/`link["to"]`), não sobre `ChainEdge` diretamente — dependência de posição é HERDADA de `Aggregate.add_edge`, não própria | chave perde `action_key` na Fase 1+ |
| Analytics | `scripts/render_map_prototypes.py` — `_splice_inferred_states` (`:600-641`) | ✅ confirmado, mesma ressalva — opera sobre dicts pós-`Aggregate`, não sobre `ChainEdge` | splice morre na Fase 1+ |
| Analytics | `scripts/shadow_chain_compiler.py` — `compile_corpus`'s `action_volume[e.action_key] += 1` | ✅ confirmado (linha ~85), consome `ChainEdge` diretamente, mesma classe de dependência de posição que `Aggregate.add_edge` | acompanha — reporta apenas, sem persistência |
| App | `src/services/map/mapGraph.ts` — `MapEdge` (`:27-36`, campos escalares) | ✅ confirmado, campos idênticos aos do Python pré-migração | `actions[]` + `actionsKey` (join ` `, precedente em `mapCollapse.ts:96`) |
| App | `src/services/map/mapAggregate.ts:99` — `addEdge`, chave `` `${sourceKey} ${targetKey} ${e.actionKey} ${e.actor}` `` | ✅ confirmado — join por espaço, mesmo padrão de bug do `systemDominance.ts:167-174` | mesmo defeito de split-por-espaço com `node_key` multi-palavra |
| App | `src/services/map/mapGate.ts:28-70` — `spliceInferredStates` | ✅ confirmado, espelha `_splice_inferred_states` char-por-char (mesmo algoritmo, mesma docstring "Mirrors Analytics") | **deleta** na Fase 1+ (vira `actions[]` serializado, ex. `"a>b"`) |
| App | `src/services/map/mapCollapse.ts:114-119`, `:314-315` | ✅ confirmado — `row.actions` já é um `Map<label,count>` (argmax de rótulo dominante), não o `actions[]` do contrato — **hoje já perde dado** (mantém só o rótulo mais frequente) | vira subsequência completa |
| App | `src/services/map/mapView.ts:79-93` — `indexParallelLinks` | ✅ confirmado, linha 79 exatamente | arcos paralelos passam a significar rotas alternativas |
| App | `src/screens/mapScreenViewModel.ts:117-128` — `mapLabel: string \| null` | ✅ confirmado, campo em `TreeGraphMapEdge`/`toTreeGraphEdges`, linha 124 | único canal de ação até o renderer → `mapActions: string[]` |
| Site | `site/graph.js` | ⚠️ **[CORRIGIDO]** — o plano diz "`l.label` string única". Falso: **o `link` do `graph.js` hoje NÃO TEM campo de rótulo nenhum** — o contrato documentado no cabeçalho do arquivo (`site/graph.js:8-9`) é `link: { from, to, fighter?, weight?, arrow?, dashed? }`. `label` existe só em `node`. Não é "perde para um rótulo escalar", é "não existe ainda" — o campo precisa ser CRIADO, não migrado. | precisa de campo (novo) multi-rótulo |

### Consomem **action node keys** (superfície de compatibilidade de rating) — intactos

| Local | Confirmado | Veredito |
|---|---|---|
| `analysis/rating_v2/node_rating.py:observations_for_side` | ✅ confirmado por leitura completa da função — itera `sequence` (dicts crus com `label`/`successful`/`actor_id`), nunca importa `chain_compiler`, nunca vê `ChainEdge` | **INTACTO**, estruturalmente — não é possível ele quebrar nesta migração |
| `analysis/rating_v2/node_rating.py:project_onto_graph` (`:328-374`) | ✅ confirmado — código real: `edge.elo = sum(elos) / len(elos)` sobre os DOIS nós extremos, comentário no próprio arquivo: *"Edge ELO is the mean of its endpoints — the same derivation `athlete_elo` uses. It has to be re-derived HERE because it is the only per-node number that survives to the DB (`graph_edges.elo`)..."* | ⚠️ **ÚNICA QUEBRA REAL CONHECIDA.** Pressupõe que ação é nó — com ação na aresta, os extremos são dois ESTADOS e a média perde o sinal técnico. Coberto por P2 (a função elo-de-aresta em si ainda não tem teste próprio nesta fase — só a regressão de `observations_for_side`, que é o que P2 pede). |
| `analysis/athlete_elo.py:_node_shares`, `analysis/markov_weights.py` | ✅ keyed por `node_key`/código Lamas, nunca por `ChainEdge` | intactos |
| `analysis/transitions/build_graph.py:network_from_sequences` | ✅ confirmado — derivação de produção separada, todo evento vira nó; não importa `chain_compiler` | substituída só na Fase 6, fora do escopo desta fase |
| `analysis/network_metrics.py` (`edge_arrow`/`edge_dashed`) | ✅ opera sobre volume agregado de `network_from_sequences`, não sobre `ChainEdge` | intacto |
| `export/site_data.py:_to_graphview` | não lida com `ChainEdge` (consome `graph_edges` do banco) | invisível ao modelo novo até a Fase 6 |
| `db/models.py` `graph_edges` | `edge_key = "{source}→{target}"`, unique em `(graph_id, source_key, target_key)`, sem coluna de ação | a parede da Fase 6 — não tocada aqui |

---

## 5. Consumidor conhecidamente dependente de posição (não consertado, por instrução)

`scripts/render_map_prototypes.py:Aggregate.add_edge` e `scripts/shadow_chain_compiler.py`'s
`action_volume` tally leem SOMENTE `e.action_key`/`e.actor` (o adaptador escalar = `actions[0]`).
`tests/test_actions_parity.py::test_p3_scalar_adapter_consumer_is_known_position_dependent`
constrói a MESMA tripla de ações (`_OBS_1`, ação inferida no meio, `_OBS_2`) em duas ordens
diferentes e prova que `Aggregate.add_edge` produz uma CHAVE de agregação diferente para cada
ordem, mesmo com o multiconjunto de ações idêntico — porque a chave é
`(source_key, target_key, actions[0].key, actor)`.

**Não corrigido nesta fase** (Fase 0 é contrato + testes, não migração de consumidor — instrução
explícita do dono). Migra na Fase 1+, junto com `_index_parallel_links`/`_splice_inferred_states`,
que herdam a mesma dependência por operarem sobre os dicts que `Aggregate` já achatou.

---

## 6. O que NÃO mudou nesta fase

- Nenhum estado sintético foi removido (`scramble`, `chained submission`, `top transition`,
  `bottom transition` continuam existindo — isso é Fase 1).
- Nenhuma regra de inferência de ação mudou (`state_pair_to_action` continua chaveada só por
  tipo — isso é Fase 2).
- Nenhum replay Glicko/Markov/Lamas foi tocado. `test_p2_*` é a prova disso: se ele precisar
  mudar em uma fase futura, a fase quebrou a regra.
- `db/models.py:graph_edges` não mudou de forma — nenhuma migração de schema nesta fase.

*Nota de estado (2026-08-31): as Fases 1 e 1b (abaixo) já landaram sobre este contrato — a lista
acima descreve o escopo DA FASE 0 no dia em que foi escrita, não o estado atual do repo.*

---

## 7. Fase 1b (2026-08-31) — âncoras servem as duas pontas, cobertura de 100%

A Fase 1 matou os 4 estados inferidos de sequência sem perder nenhuma ação, mas deixou um
defeito: quando nenhuma âncora resolvia, o compilador emitia `source_key`/`target_key` = `""` —
um nó fantasma de chave vazia, 269 ocorrências no corpus do dono, o SEGUNDO maior nó do grafo
por grau. O `scramble` de volta com outro nome.

**Decisão do dono que resolve isso:** os três genéricos orientados (`start neutral`/`start
top`/`start bottom`, node_key inalterado) deixam de ser exclusivos de ABERTURA e passam a servir
as DUAS pontas de uma cadeia — os rótulos perdem o prefixo "Início" ("Neutro"/"Por Cima"/"Por
Baixo"). Exemplo real do mecanismo abaixo: uma cadeia que TERMINA em passagem chega no nó
`start top` (o passador termina por cima); uma que termina em puxada de guarda chega em `start
bottom` (a atleta termina por baixo). `finish` (`role: 'finish'`) continua exclusiva de
submissão — não muda. O campo `role` dos três genéricos orientados vira `'anchor'` (era
`'start'`).

**Mecanismo — regra, não linhas hardcoded.** `data/taxonomy/inference_table.json` ganhou só 2
linhas declarativas novas em `action_pair_to_state` (abertura): `$start|sweep -> start bottom`
(uma raspagem se EXECUTA de baixo — o executor começa por baixo e reverte; curado em
`analysis/attribution.py`) e `$start|transition -> start neutral` (transição genérica não
sustenta afirmação de orientação). A tabela continua sem nenhuma linha nova para FECHAMENTO —
`submission|$terminal -> finish` continua sendo a única linha declarativa e continua tendo
precedência.

Toda outra ponta (abertura OU fechamento) sem linha declarativa resolve agora por
`analysis.taxonomy_kind.resolve_anchor_by_role(table, action_type, action_label)`: lê
`analysis.attribution.classify(action_type, action_label).actor_role` (a mesma fonte curada que
a Fase 2 vai usar para a regra de inferência de ação) e mapeia `TOP -> start top`, `BOTTOM ->
start bottom`, qualquer outra coisa (`controlling`/`controlled`/`executor`/`defender`/
`neutral`/`unknown`) `-> start neutral`. `classify` sempre devolve um papel, então esta função
sempre resolve — nenhuma ponta fica mais sem âncora. Chamada em dois pontos de
`analysis/chain_compiler.py`: `_opening_state` (depois do check PGD/CDP e da linha declarativa
`$start|type`) e o fechamento de `compile_chain` (depois de `submission|$terminal`).

**A chave vazia deixou de ser emitida.** `_opening_state` e o bloco de fechamento agora sempre
devolvem uma âncora real — `resolve_anchor_by_role` tem tipo de retorno não-opcional
(`dict[str, Any]`, nunca `None`), então o "defeito" do item 4 do plano (ponta sem resposta) é
impossível por construção: se `table["generic_states"]` algum dia não tiver `start
neutral`/`start top`/`start bottom`, a função levanta `KeyError` em vez de inventar uma chave
vazia. `tests/test_actions_parity.py::test_no_empty_endpoint_edges_and_no_generic_out_degrees_the_real_graph`
trava o invariante sobre o corpus real: zero arestas com endpoint vazio, e nenhum genérico com
grau acima do maior nó real.

**Medido sobre as 281 lutas do corpus (`/home/vetor/GrapplingArc/_analytics_export.json`,
via `scripts/shadow_chain_compiler.py`):**

| métrica | antes | depois |
|---|---|---|
| arestas com endpoint vazio | 269 | **0** |
| ocorrências totais de ação | 1789 | 1789 (inalterado) |
| distribuição de `len(actions[])` | inalterada | inalterada (a mudança só toca âncoras, nunca o buffer de ações) |
| grau de `start neutral` | 107 | 326 |
| grau de `start top` | não medido antes | 26 |
| grau de `start bottom` | não medido antes | 74 |
| grau de `finish` | 113 | 113 (inalterado — fechamento por submissão não mudou) |
| maior nó real (`mount`) | 349 | 349 (inalterado) |

Todo genérico continua abaixo do maior nó real (326/113/74/26 < 349) — critério do dono
satisfeito. `start neutral` cresceu porque agora absorve tanto aberturas quanto fechamentos sem
orientação clara: dos 269 casos antes órfãos, `transition` soma 136 ocorrências entre abertura
(72) e fechamento (64), a maior fatia — e `sweep` (36 na abertura) foi para `start bottom` pela
linha declarativa nova, não para `start neutral`.

**Limitação conhecida, não corrigida aqui (fora de escopo — Fase 2).** O plano do dono ilustra
a nova semântica com "uma cadeia que termina em raspagem chega no nó por cima; uma que termina
em escapada chega em por baixo" — mas a REGRA que o mesmo plano manda implementar (item 3:
`classify(type,label).actor_role` mapeado `TOP`/`BOTTOM`/resto) não produz esse resultado para
`sweep`/`escape`: o `actor_role` de tipo-padrão de ambos em `attribution.py` é `executor`
(genérico), não literalmente `top`/`bottom` — só `guard`, `control` e `pass` carregam
orientação literal no `actor_role` de tipo-padrão hoje. Fechamento em `sweep`/`escape` resolve
para `start neutral`, medido no corpus real, não para o exemplo do plano. O comentário do
próprio `attribution.py` ("a sweep ENDS with the sweeper on top") não está expresso no
`actor_role`, só na prosa — ajustar isso é dado curado adicional em `attribution.py`, fora do
escopo que esta fase recebeu ("não cresça a regra de inferência de AÇÃO"). Registrado aqui em
vez de silenciosamente ajustado para bater com o exemplo do plano.

---

## 8. Fase 2 (2026-08-31) — a tabela vira vocabulário, a decisão vira função

### 8.1 O defeito, e por que uma tabela não podia consertá-lo

`state_pair_to_action` era chaveada só por `"tipo_a|tipo_b"` (`resolve_pair`, cujo próprio
docstring diz *"Keys are by event TYPE, never by label"*). Três dos seis exemplos do dono
colidiam entre si nessa chave:

| exemplo | chave antiga | colidia com |
|---|---|---|
| `Guarda A → Guarda B ⇒ Raspagem A` | `guard\|guard` | `Meia-Guarda A → Guarda Fechada A ⇒ Transição de Guarda` |
| `Controle A → Guarda A ⇒ Inversão B` | `control\|guard` | `Controle A → Guarda B ⇒ Recomposição B` |
| `Guarda A → Controle B ⇒ Passagem B` | `guard\|control` | resolvia como `guard exit` |

A decisão mudou para `analysis.taxonomy_kind.infer_transition_actions`, que lê **tipo, rótulo e
ator de cada ponta** + **o buffer de ações já observadas**. `state_pair_to_action` continua
existindo como o vocabulário e como a resposta para pares que não fazem afirmação posicional
(mantém o `"*|*" → "transition"`).

**Vocabulário +3** em `generic_actions`: `sweep` (Raspagem), `reversal` (Inversão),
`guard pass` (Passagem de Guarda). O `sweep` é genérico por decisão — a regra nunca inventa
subtipo de raspagem.

⚠️ **Colisão de chave, registrada e ACEITA.** As três chaves novas são também chaves canônicas de
rótulos reais do corpus (`"Sweep"` 207 eventos, `"Reversal"` 16, `"Guard Pass"`+`"Pass"` 55). Uma
ação inferida e uma observada passam a dividir o mesmo `node_key`. É o que a invariante 1 deste
contrato pede (identidade = `canonicalize(_normalize_name(label))`, ponto), e `ChainAction.inferred`
é o discriminador que todo consumidor já tem. **Importa na Fase 6**: `graph_edges` conflataria as
duas sem esse campo. Batizar os genéricos com um nome fora do vocabulário seria a troca pior.

### 8.2 O eixo que faltava: orientação de SAÍDA

§7 registrou a limitação: `classify(type,label).actor_role` devolve `executor` para
`sweep`/`escape`/`takedown` — uma RELAÇÃO, não uma posição. Bloco novo
`action_exit_orientation` em `data/taxonomy/inference_table.json`, curado por TIPO com uma
linha `"*"` declarada como default (nunca um chute):

| tipo | saída | base |
|---|---|---|
| `sweep` | `top` | ordem do dono + a prosa do próprio `attribution._LABEL` ("a sweep ENDS with the sweeper on top") + os 5 sweeps `successful=True` seguidos de estado (4 top, 1 neutro) |
| `takedown` | `top` | ordem do dono; os 44 takedowns do corpus são todos `successful=True` |
| `pass` | `top` | já era o que `classify('pass',…)` dizia — comportamento inalterado |
| `guard` | `bottom` | ação de tipo `guard` = puxada de guarda |
| `escape` | `neutral` | **MEDIDO, e contradiz o plano** — ver 8.5 |
| `submission`/`transition`/`control`/`*` | `neutral` | nenhuma afirmação |

`orientation_of` **não foi tocada** (ordem do dono: a recusa em chutar é a razão de ela existir, e
`export_taxonomy_kind_fixtures` espelha a saída dela no App). O caminho de inferência ganhou
`orientation_for_inference`, em três níveis, e o retorno (`StanceReading`) **diz qual nível
respondeu**:

1. tabela declarada, sob a chave canônica;
2. tabela declarada, sob o rótulo canônico da BIBLIOTECA — é o que resgata `"Back Take"`, o
   terceiro maior nó do grafo (grau 211), cuja linha curada está sob `"Back Control"`;
3. `attribution.classify(...).actor_role`, rotulado `derived`.

Medido sobre as listas curadas de `attribution`: **52 dos 74 rótulos** que deveriam carregar
orientação liam `neutral` via `orientation_of` (70%, e **13 de 13** pegadas de controle). Com os
três níveis: **0 de 74**. `_GUARD_NEUTRAL` mantém `neutral` — 50/50 é simétrico por construção —
exceto pelos quatro rótulos onde a tabela declarada e `attribution` já se contradiziam
(`5050 guard`, `single leg x`, `single leg x guard entry`, `shin to shin guard`): item do backlog
"não tocar agora" do dono, **herdado, não resolvido em silêncio** — a tabela declarada continua
sendo a verdade e o teste trava exatamente quais rótulos ela vence.

Cinco valores, não três: `attribution` mantém `top/bottom` e `controlling/controlled` como eixos
**separados** de propósito (`_AXES`) — costas pegadas por baixo são `controlling` e `bottom` ao
mesmo tempo, e colapsar os dois já jogou fora 53% dos eventos uma vez. A regra só compara
**dentro** de um eixo; guarda fechada (`bottom`) contra pegada de kimura (`controlling`) é
incomparável e cai na tabela. A ressalva do plano sobre `back control` continua registrada, não
resolvida.

### 8.3 A ordem da regra

1. **Ações observadas são imutáveis** em ordem e posição. `InferredInsert.index` é uma posição
   no buffer ORIGINAL para inserir ANTES.
2. **Buffer vazio** ⇒ exatamente uma ação (nó não encadeia em nó): inversão ⇒ `sweep` se o novo
   dominante era o guardeiro da origem, senão `reversal`; sem inversão ⇒ `guard pass` /
   `guard recovery` / `guard transition` / `control transition` conforme as famílias; sem
   afirmação posicional comparável ⇒ a tabela declarativa.
3. **Buffer não vazio** ⇒ **no máximo UMA** inserção, e só quando os dois estados OBSERVADOS
   invertem no mesmo eixo. Posição: antes da primeira ação observada cuja orientação de ENTRADA
   já pressupõe a nova dominância (uma passagem exige o passador em cima). **Redundância**: se
   alguma observada já explica a inversão pela orientação de SAÍDA, não insere nada.
4. **Ator**: gated por `actor_readable`.

⚠️ **A orientação de SAÍDA só pode SUPRIMIR uma inferência, nunca criar uma.** 196 dos 228 eventos
`sweep` do corpus têm `successful = NULL`; tratar a saída de uma tentativa como fato é ler um
resultado que o log não registrou — a mesma linha D7 que `compile_chain` não cruza. Primeira
versão desta fase avançava uma posição rolante a cada saída observada e **fabricou 160 ações
extras**, incluindo `closed guard --[sweep, reversal, sweep, reversal, …]--> closed guard`, onde
repetições consecutivas de uma única tentativa logada oscilavam a posição. Medido, rejeitado,
registrado aqui.

### 8.4 Abstenção de ator

`compile_chain`/`compile_two_sided` ganharam `actor_readable`. É o veredito de
`attribution.bout_flags` **passado para dentro**, não re-derivado: `compile_two_sided` deriva o
`one_sided` a partir dos próprios baldes (importando `MIN_EVENTS_FOR_ONE_SIDED`, não copiando), e
um chamador que tenha o `bout_flags` real passa `role_reliable`. Com `False`, uma DIFERENÇA de
ator deixa de ser evidência: `Mount A → Side Control B` lê `control transition` em vez de
`reversal B`. Medido neste dump: **57 de 281 lutas (20,3%) são one-sided** — não os 43,9% do
corpus de prod (700 lutas), que é uma população diferente.

`ChainAction` ganhou `actor_is_opponent: bool = False` (aditivo, com default). Uma cadeia
compilada por lado nomeia UM atleta, então "Controle A → Guarda A ⇒ **Inversão B**" tem dono real
e sem nome ali; `actor` carrega o nome só quando a entrada nomeia os dois, e a flag carrega de
quem é nos dois casos. Sem ela, as 47 inversões do corpus sairiam com `actor=None` e um renderer
não teria como colorir nenhuma. **Fase 5 espelha este campo no App.**

### 8.5 Medições sobre as 281 lutas (`_analytics_export.json`), antes → depois

| métrica | antes (Fase 1b) | depois (Fase 2) |
|---|---|---|
| ocorrências de ação **observadas** | 1390 | **1390** (invariante — nenhuma criada, nenhuma perdida) |
| ocorrências de ação **inferidas** | 399 | **433** (+34) |
| total | 1789 | 1823 |
| grau `start neutral` | 326 | **277** |
| grau `start top` | 26 | **75** |
| grau `start bottom` | 74 | 74 |
| grau `finish` | 113 | 113 |
| maior nó real (`mount`) | 349 | 349 |
| fallback `transition` pelado | 92 (23,1% das inferências) | 88 (20,3%) |
| arestas com endpoint vazio | 0 | 0 |

Fechamento de cadeia (tipo da última ação → âncora):

| tipo | antes | depois |
|---|---|---|
| `submission` | `finish` 113 | `finish` 113 |
| `transition` | `start neutral` 64 | `start neutral` 64 |
| `escape` | `start neutral` 32 | `start neutral` 32 |
| `sweep` | `start neutral` 31 | **`start top` 31** |
| `takedown` | `start neutral` 18 | **`start top` 18** |
| `pass` | `start top` 12 | `start top` 12 |
| `guard` | `start bottom` 2 | `start bottom` 2 |
| `control` | `start neutral` 1 | `start neutral` 1 |

Ações inferidas, por chave: `control transition` 202, `transition` 88, `guard transition` 57,
`reversal` **47**, `sweep` **26**, `guard exit` 11, `guard recovery` 1, `guard pass` 1.
(`guard exit` 27→11 e `guard recovery` 21→1 porque a maioria desses pares É uma inversão.)

Comprimento de `actions[]` (1→26): `{1: 666, 2: 144, 3: 63, 4: 46, 5: 28, 6: 13, 7: 11, 8: 5,
9: 2, 10: 4, 11: 4, 14: 1, 19: 1, 26: 1}`.
Posição das 433 inferidas: **399** em arestas sem nenhuma observada (o caso buffer-vazio),
**28** no fim de um buffer, **4** na cabeça, **2 no MEIO** entre observadas.

### 8.6 Cinco coisas que o dado contradisse, e não foram ajustadas para bater

1. **Escapada NÃO termina por baixo.** O plano ilustra "uma cadeia que termina em escapada chega
   em por baixo". Dos 83 eventos `escape` do corpus, **75 são literalmente "Escape to Standing"
   (60) ou "Stand-up Escape" (15)** — 90% escapam para os PÉS. Só 2 escapadas são seguidas de
   algum estado, então não há evidência de resultado em nenhuma direção; os rótulos são a
   evidência. `escape → neutral`, e os 32 fechamentos em `start neutral` do "antes" estavam
   **certos**, não errados.
2. **Quatro dos seis exemplos do dono não podem ocorrer no corpus.** `compile_two_sided` compila
   cada lado separadamente, então dentro de uma cadeia o ator é constante — medido: **0 de 600**
   pares de estados adjacentes têm atores diferentes. `Guarda A → Guarda B`,
   `Guarda A → Controle B`, `Controle A → Guarda B` e `Controle A → Controle B` são provados por
   teste unitário e valem 0 ocorrências no corpus (`guard pass` inferido: 1; `guard recovery`: 1).
   A metade da regra que o corpus exercita é o delta de orientação do MESMO ator.
3. **`start neutral` continua sendo o nó nº 2 do grafo** (277 contra `mount` 349). Caiu 49, pelo
   motivo certo (sweep/takedown saíram), mas os 64 fechamentos + 85 aberturas em `transition`
   genérico continuam lá. Passa o critério do dono; não é uma vitória.
4. **O fallback `transition` pelado quase não se mexeu (92 → 88), e não é defeito da regra.**
   Dos 88, ~49 são pares `strike|strike` (38) e `penalty|penalty` (11) — tipos de evento que não
   são grappling e que `kind_of` lê como "state" por default. É higiene de ingestão, não regra de
   inferência. O relatório do shadow já registra que o compilador não recebe filtro de
   `reset`/`match`/`penalty`/`strike`.
5. **`defensive retreat` nunca dispara.** As linhas `defensive|guard` e `*|defensive` de
   `state_pair_to_action` referenciam um tipo `defensive` que não existe em
   `attribution.EVENT_TYPES`. Mortas antes da Fase 2, mortas depois. Não removidas aqui (fora de
   escopo, e removê-las mexe na tabela que o App espelha).

### 8.7 Paridade

- **P2 (`observations_for_side`) byte-idêntico** — nada tocou o caminho de entrada dele. É a
  prova de que nenhum código de rating/Markov/Glicko foi mexido nesta fase.
- **P1 dividido em duas metades**, porque as duas nunca foram a mesma coisa: as linhas
  `inferred=false` do golden são a invariante (22 ocorrências no mock bundle, antes da Fase 0 e
  depois de toda fase desde), e as `inferred=true` são a saída da regra, que MUDA quando a regra
  muda (2 → 5, três raspagens que a tabela por tipo lia como `guard exit`). O fixture ganhou a
  coluna `inferred` para que uma mudança de regra não possa se esconder atrás da contagem de uma
  ação observada, e `test_p1_observed_actions_are_the_invariant_the_inference_rule_may_never_move`
  trava a metade que não pode andar.
- Suíte: **2329 passed / 3 failed / 1 skipped**. Os 3 vermelhos são os mesmos de antes desta fase
  (`test_golden_fixture_matches_this_implementation`,
  `test_app_inference_table_matches_analytics_source`, `test_generator_check_flag_is_green`) —
  comparam a tabela daqui com o espelho do App, que é dívida da **Fase 5**. Nenhum vermelho novo,
  e nada foi mascarado: regenerar o golden local deixaria
  `test_both_repos_carry_the_same_fixture_bytes` vermelho no lugar.

## 9. Fase 3 (2026-09-01) — métricas de caminho (`analysis/path_metrics.py`)

Módulo novo, puro, sem I/O além do artefato Markov já existente. Interface fixa — a Fase 4
(protótipo) importa estes nomes como estão:

```python
@dataclass(frozen=True)
class PathMetrics:
    length: int             # nº de ações na trilha (observadas + inferidas)
    observed: int           # ações NÃO inferidas
    observed_ratio: float   # observed / length; 0.0 quando length == 0
    support: int             # nº de ocorrências da relação (source, target, actor) — do caller
    terminal: bool           # edge.terminal
    role_delta: str          # 'none' | 'inversion' | 'same-actor-shift' | 'unknown'
    strength: float | None   # média ponderada (Markov mean-1) das ações OBSERVADAS com rating

def path_metrics(edge: ChainEdge, *, support: int, rating_of: Callable[[str], float | None],
                 block: Mapping[str, float] | None) -> PathMetrics
def metrics_for_paths(edges: Iterable[ChainEdge], *, support_of, rating_of, block) -> list[tuple[ChainEdge, PathMetrics]]
```

### 9.1 `strength`

Reusa o contrato existente, não reimplementa peso: `analysis.markov_weights.relative_shares`
sobre o código `analysis.lamas_chain.lamas_state` de CADA ação da tupla — as shares são
calculadas sobre a **tupla inteira** de `edge.actions` (preserva a distribuição real da
transição), e só DEPOIS a média pondera apenas o subconjunto OBSERVADO com rating: uma ação
`inferred=True` nunca entra (não foi observada, mesmo que `rating_of` responda para a sua
chave), e uma ação cuja `rating_of(key)` devolve `None` também não. Os pesos do subconjunto
usado são renormalizados, então `strength` continua sendo uma média de verdade sobre quem
qualifica — não fica achatada pela massa de peso excluída. `strength` é `None` quando nada na
aresta qualifica.

`rating_of` é injetado porque a fonte varia por chamador (Glicko-2 por `(athlete, node_key)` no
corpus público, `computedElo` do bundle no lado App) — este módulo nunca abre um DB nem um
bundle.

### 9.2 `role_delta`

Compara os dois estados-extremo da aresta (`source_key`/`target_key`) via
`taxonomy_kind.orientation_for_inference`, nos dois eixos que `taxonomy_kind` já mantém
separados (`_STANCE_AXIS`): topologia (`top`/`bottom`) e controle (`controlling`/`controlled`)
— uma guarda fechada (`bottom`) e uma pegada de kimura (`controlling`) fazem afirmações em eixos
diferentes e não são comparáveis.

| condição | valor |
|---|---|
| mesmo eixo, mesmo lado (ex.: `mount` → `side control`, ambos `top`) | `none` |
| eixos diferentes, ou algum extremo lê `neutral`/sem afirmação | `unknown` |
| mesmo eixo, lado invertido, e alguma ação da aresta é atribuída ao **oponente** (`actor_is_opponent=True`) | `inversion` (o "Inversão B" do modelo — dominância passou para quem não é dono da cadeia) |
| mesmo eixo, lado invertido, sem ação atribuída ao oponente | `same-actor-shift` (o "Raspagem A" do modelo — o próprio dono da cadeia inverteu a própria posição) |

Aprovado como está pelo dono (2026-09-01).

### 9.3 Limitação declarada — tipo do estado não sobrevive na aresta

`ChainEdge` carrega só a CHAVE canônica de cada extremo (`source_key`/`target_key`), nunca o
`(type, label)` original do evento — o `type` do estado nunca chega na aresta.
`orientation_for_inference` precisa de `event_type` só para seu terceiro nível (o fallback via
`attribution.classify`); os dois primeiros níveis (tabela declarada pela chave canônica, depois
pelo nome canônico da biblioteca) não precisam de tipo e resolvem a maioria dos rótulos sem ele.

`path_metrics` recupera o tipo via `taxonomy_kind.resolve_library_entry(key)` quando a chave é
uma entrada conhecida da biblioteca de técnicas do App; quando não é, passa tipo vazio. Isso
degrada honestamente (nunca chuta um tipo) — para os ~13 rótulos de pegada/controle em pé
(`_CONTROL_GRIP` de `attribution.py`) que só resolvem pelo terceiro nível do §8.2 e não estão na
biblioteca, o extremo lê `unknown` em vez do seu `stance` real. Medido sobre o corpus de 281
lutas (989 arestas): `none` 388, `unknown` 481, `same-actor-shift` 75, `inversion` 45 — a fatia
de `unknown` é dominada pelas âncoras `start neutral` (sem afirmação posicional por construção),
não só por este gap.

Teto: linkar o `type` real do estado até `ChainEdge` (ou aceitar um `state_type_of: Callable[[str],
str | None]` ao lado de `rating_of`). Não vale o custo para um primeiro corte sobre 4 categorias
de `role_delta`.

---

## 10. Fase 4 (2026-09-01) — bundling de caminhos e a variante 13 do protótipo

Camadas 2 e 3 das quatro do dono. `analysis/path_bundling.py` é a lógica (pura, testada);
`scripts/render_map_prototypes.py` ganhou a variante **13 — Caminhos** (`13-caminhos.html`), que
só desenha. As variantes 1–12 saem **byte-idênticas** (verificado com `diff -r` antes/depois);
mudam apenas `graph.js` (patches novos, todos condicionados a campos que só a 13 emite),
`index.html` (a linha nova) e `metrics.json` (o bloco novo).

### 10.1 O que é um segmento

A maior sequência CONTÍGUA de ações percorrida por **exatamente** o mesmo conjunto de caminhos,
entre dois pontos. `[1,2]` é um segmento só enquanto todo caminho que anda o `1` também anda o
`2` e ninguém entra no meio; assim que um terceiro caminho divide só o `2`, a corrida racha,
porque as duas metades deixam de carregar o mesmo conjunto.

`Point.kind` é `state` (vértice real do grafo semântico) ou `branch`/`merge`/`branch-merge`
(**artefato visual**, nunca persistido, nunca um `node_key`).

### 10.2 As três regras que garantem que o desenho não mente

1. **Igualdade de ação canônica é o único critério de compartilhamento** — nunca rótulo, nunca
   similaridade. Duas posições de fronteira viram o mesmo ponto quando concordam num PREFIXO
   (mesmo estado de origem + mesmas ações até ali) ou num SUFIXO (mesmas ações restantes + mesmo
   estado de destino), transitivamente. Trie para frente + trie invertida; o índice de k-gramas
   contíguos cai das mesmas duas tabelas — a TRANSITIVIDADE é o índice de k-gramas:
   `A --[1,2,3]--> C` e `B --[5,2,3]--> C` dividem o `2` interno pelo sufixo comum, mesmo com
   origens diferentes. O que fica DELIBERADAMENTE de fora é a corrida ancorada em NENHUMA das
   pontas (`A --[1,2,3]--> C` vs `B --[4,2,5]--> D`): duas cadeias que nem abrem nem fecham no
   mesmo lugar não dividem base, só reusam um verbo — e um índice de k-gramas solto colapsaria
   todo `sweep` de meio de cadeia num traço só, entregando uma multidão de `branch-merge`, que é
   a forma exata da "conectividade visual falsa" que o dono proibiu.
2. **Uma posição interna nunca funde num ponto de estado.** Se `A --[1,2]--> C` e `A --[1]--> X`
   dividissem o ponto depois do `1`, o desenho afirmaria que o primeiro caminho passa por `X` —
   um estado inventado no meio da cadeia, exatamente o que a Fase 1 apagou.
3. **Nenhuma fusão pode fazer um caminho cruzar a si mesmo.** ⚠️ Esta regra NÃO estava no
   desenho original — foi descoberta rodando o algoritmo sobre o corpus público (668 caminhos,
   989 ocorrências) e é o único ponto do plano da Fase 4 que o dado contradisse. Ações repetidas
   (`back take --[triangle attempt ×4]--> back take`) fundem a 1ª e a 3ª lacuna do caminho longo
   através de um irmão de duas tentativas: o caminho passa a se cruzar, `walkable_routes` deixa
   de ser a cadeia dele, e **o desenho passou a licenciar 19 rotas que nunca aconteceram e a
   perder 3 que aconteceram** (ex.: `half guard --[sweep, sweep]--> start top`, montada com
   metades de `back take --[rear naked choke, sweep]--> start top`). A fusão que faria isso é
   recusada (`_Union._owners`); com a recusa, sobre o mesmo corpus: **0 fantasmas, 0 perdidas**.
   Fusões de posições de ESTADO nunca são recusadas — `A --[x]--> A` tem as duas pontas ali por
   construção.

### 10.3 A prova

`BundledGraph.walkable_routes()` enumera TODA caminhada de ponto-de-estado a ponto-de-estado
cujos segmentos compartilham pelo menos um `path_id` do começo ao fim, e o teste afirma que esse
conjunto é **igual** ao de entrada. É o teste de "rota inexistente" no sentido forte: com a
regra 3 valendo, a caminhada de cada `path_id` é simples, então uma caminhada com interseção
global não-vazia só pode ser a de um caminho real. `tests/test_path_bundling.py` roda os cinco
casos do dono + as três regras + o invariante, e repete o invariante sobre o bundle real dele.

### 10.4 Medido

| | bundle do dono (privado) | corpus público (281 lutas) |
|---|---|---|
| render paths | 42 (66 ocorrências) | 668 (989 ocorrências) |
| distribuição de `len(actions)` | `{1: 40, 2: 2}` | `{1: 355, 2: 136, 3: 61, 4: 46, 5: 28, 6: 13, 7: 11, 8: 5, 9: 2, 10: 4, 11: 4, 14: 1, 19: 1, 26: 1}` |
| pontos | 20 (20 estado, 0 artefato) | 136 (71 estado, 19 branch, 20 merge, 26 branch-merge) |
| segmentos | 42 (0 compartilhados) | 736 (129 compartilhados) |
| ações num traço compartilhado | **0,0%** | **11,6%** (135/1167) |
| maior tronco | 1 caminho | 23 caminhos (`sweep`) |
| `walkable_routes == entrada` | sim | sim |

⚠️ **O bundling é inerte no bundle do dono, e isso é um fato do log, não do algoritmo.** 40 dos
42 caminhos dele carregam UMA ação: as 114 entradas alternam estado/ação (73 estados, 41 ações),
e uma transição entre dois estados observados consecutivos recebe exatamente uma ação. Sem
posição interna não existe fronteira interna, logo não existe tronco. O que o dono ganha na
variante 13 é o LAYOUT determinístico e o painel de métricas; o bundling passa a valer quando a
cadeia tiver comprimento — o corpus de lutas já tem (39% dos caminhos com ≥2 ações).

### 10.5 Layout — Python, não JS

O plano pedia portar `decisionFlowLayout.ts` para o JS do protótipo. O layout foi escrito em
**Python** e enviado como `x`/`y` fixados (`n.pin`). Três razões, na ordem em que pesaram:
`graph.js` já honra `n.x`/`n.y` + o patch `n.pin`, então um grafo totalmente posicionado não
precisa de NENHUM conceito novo no renderer; o determinismo vira provável por `pytest` e pelo
`diff -r` em vez de morar numa função JS que nada neste repo executa; e a convenção deste repo
para "o App é dono do TS, nós somos do espelho" é um espelho PYTHON (`network_metrics` ↔
`directedEdges.ts`, `presentation.py` ↔ `ratingV2Presentation.ts`). O App já TEM
`decisionFlowLayout.ts` — uma transcrição JS dentro de uma string Python seria uma terceira
cópia, não um caminho mais curto para o port.

O algoritmo é o dele: rank por BFS multi-fonte com visited-set (ciclos são reais aqui), ordenação
determinística dentro do rank, `x` do rank. O roteamento SPLIT/MERGE é a única parte deixada de
fora **de propósito**: lá as junções existem para abrir o leque de saída de um nó; aqui o leque
já é objeto de primeira classe — `Point(kind='branch'|'merge'|'branch-merge')`, produzido pelo
bundler a partir do PREFIXO DE AÇÃO, que é exatamente a mudança que o plano pediu. elkjs/dagre
continuam fora (`userDecisionFlow.ts:32-45`).

**Cruzamentos, medidos** (segmentos retos entre pontos fixados): moldura circular com as âncoras
fora da ordenação = 83 cruzamentos em 42 links (bundle do dono) e 16 em 21 (mock do App). Com as
âncoras contando na baricentragem e a moldura numa elipse larga e baixa (`rx` 2.00, `ry` 1.30,
`spread` 0.35, 2 varreduras): **41 e 5**. Mais varreduras PIORAM (a heurística da mediana não é
monótona): 6 varreduras custaram +6 cruzamentos.

### 10.6 Layout de âncoras configurável

`_PENTAGON_ANGLES` virou uma linha de `_ANCHOR_STRUCTURES`; a linha do pentágono aponta para a
constante original, e é por isso que as variantes 1–12 não se mexeram.

| estrutura | vértices | finalização |
|---|---|---|
| `pentagono` | os 5 de sempre | uma por atleta |
| `losango` | neutro à esquerda, top em cima, bottom embaixo, finalização à direita | **unificada** |
| `triangulo` | top acima-esq., bottom abaixo-esq., finalização à direita; neutro no meio da aresta esquerda | **unificada** |

Finalização unificada é UM vértice com dois atletas: desenhada com preenchimento partido ao meio
(`n.split`, patch na CÓPIA do `graph.js`), porque dobrar as duas num lugar só nunca pode esconder
de quem é.

### 10.7 Pendências e tetos conhecidos

- `_paths_view` não aplica o gate adaptativo das 11/12 — a 13 mostra TODOS os caminhos no escopo
  Global, porque esconder caminho é esconder o objeto de estudo. As pills de sistema continuam
  como PREDICADO (um caminho entra quando uma das pontas é membro; a outra ponta vira stub).
- Rótulos de ação são a camada secundária (`principais` por padrão = peso ≥ 2; no celular, só o
  que estiver selecionado). É a exigência de mobile do dono, não uma economia.
- `Point.id` de um estado é `s:{node_key}` e **contém espaços**. Nada aqui parte id por espaço —
  mas é exatamente o defeito vivo de `systemDominance.ts:167-174` no App, e o port da Fase 5 tem
  de nascer sabendo.
- Um ponto `branch-merge` é honesto quanto às rotas (§10.3), mas o olho ainda pode "seguir" uma
  entrada de p1 até uma saída de p2 num traço estático. O que desfaz isso é a seleção (destaque
  atravessa os troncos compartilhados). No bundle do dono não existe nenhum; no corpus são 26.
