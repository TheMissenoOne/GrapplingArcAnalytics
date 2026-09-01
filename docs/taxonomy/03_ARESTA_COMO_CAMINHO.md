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

### 10.5.1 Relaxação de bolhas de rótulo, e o aspecto (dono, 2026-09-01)

> *"the layout is clustering the labels because all the states are aligned in layers… use some
> force simulation and create sort of bubbles… so that nothing that's readable is overlapped,
> while keeping the anchor nodes fixed… it might be too stretched."*

O rank é uma COLUNA, e o rótulo é ~10× mais largo que o ponto que ele nomeia: dois ranks vizinhos
na mesma linha têm os pontos a 300 unidades um do outro e os NOMES por cima um do outro. A grade
acerta a ORDEM e erra a única coisa que o olho lê. Então a grade virou o ponto de partida, e
`flow_layout` ganhou duas etapas finais, nesta ordem:

1. **Aspecto (`_compact`)** — o eixo longo encolhe por `k`, o curto cresce por `1/k`
   (`FLOW_TARGET_ASPECT = 1.5`, piso `FLOW_COMPACT_MIN = 0.4`). **Área constante** é a metade que
   precisou de medição: encolher só o x acertava o aspecto e deixava a caixa 2,4× pequena demais
   para os rótulos que ela tem de conter — a relaxação ficava sem para onde empurrar e parava com
   19 sobreposições. É uma transformação AFIM e uniforme, então não cria nem destrói um único
   cruzamento: os 41-em-42 medidos em §10.5 sobrevivem intactos.
2. **Relaxação (`_relax`)** — cada rótulo desenhado é uma CAIXA (`label_half_extent`, largura por
   nº de caracteres × `FLOW_LABEL_EM` × `FLOW_LABEL_CHAR` + padding — a mesma constante 0.55 do
   `labelLayout.AVERAGE_CHAR_RATIO` do App). Um número FIXO de rodadas (`FLOW_RELAX_ROUNDS = 120`)
   empurra cada par sobreposto pela translação MÍNIMA (eixo de menor penetração, `+
   FLOW_RELAX_SLACK` para o par assentar com folga em vez de convergir em "encostado"), com uma
   mola que puxa de volta ao slot da camada e **decai a zero** — mola constante assenta em
   `empurrão == mola`, que é um equilíbrio COM sobreposição (medido: 11 pares ainda encostados
   depois de 400 rodadas). **As âncoras nunca se movem**: entram no laço como obstáculo e nunca
   no mapa de deslocamento. Segunda passada, só com os nomes de ESTADO, porque a camada
   secundária não pode desempatar contra a primária (medido: um nó em impasse de três corpos
   assentava 13 unidades dentro do nome da âncora ao lado).

É CAIXA e não disco de propósito: um rótulo tem ~200 unidades de largura e ~12 de altura, e um
disco que o contivesse reservaria 200 unidades de altura também — o oposto exato da segunda
metade do pedido.

O `label_len` (contagem de caracteres por id de ponto E de segmento) é do CHAMADOR, porque só ele
sabe a locale: "Finalização" tem 11 e `finish` tem 6, e uma bolha dimensionada na chave crua é
uma bolha dimensionada para um desenho que ninguém vê. O critério vale "na escala de fit" porque
os dois renderizadores desenham o rótulo DENTRO do transform do mundo — o zoom escala posição e
glifa pelo mesmo fator, então sobreposição em unidades de mundo é invariante de escala.

**Medido, `mock_user_bundle`, antes → depois** (o teste é `tests/test_flow_layout_relax.py`;
pares "rótulo de aresta × sua própria ponta" ficam fora dos três, ver §10.7):

| estrutura | caixa antes | aspecto antes | caixa depois | aspecto depois | nome×nome | nome×ação | ação×ação |
|---|---|---|---|---|---|---|---|
| `triangulo` | 1200 × 260 | **4,62** | 684 × 452 | **1,51** | 0 → **0** | 3 → 3 | 2 → 3 |
| `losango` | 1200 × 338 | **3,55** | 780 × 520 | **1,50** | 2 → **0** | 3 → 4 | 2 → 1 |
| `pentagono` | 1085 × 321 | **3,38** | 723 × 482 | **1,50** | 0 → **0** | 4 → 4 | 3 → 3 |

Custo: 1,8 ms → 8,0 ms por página (`_paths_view` inteira). Determinismo: sem RNG, sem relógio,
sem teste de convergência — rodada fixa sobre iteração ORDENADA, só `+,-,*,/` e comparações (o
único `sqrt` está na compactação, e `math.sqrt`/`Math.sqrt` são ambos IEEE-corretos). A golden
`flow_layout_golden.json` bateu bit a bit com o port TS na PRIMEIRA execução, incluindo o caso
`crowded` que existe só para exercitar as duas etapas novas.

### 10.5.3 Espalhamento e aspecto do VIEWPORT (dono, 2026-09-01, terceira passada)

> *"The force graph on the app has not been applied. It still too stretched."*

A relaxação de §10.5.1 só separa caixas que JÁ se sobrepõem, e pelo eixo de MENOR penetração —
para um rank cheio de caixas largas e baixas esse eixo é sempre o vertical, então o rank continuou
uma COLUNA. Medido no bundle do dono (15 estados, 33 segmentos, rótulos em pt-BR): 11 pontos
livres em **5** valores distintos de `x`, 36 cruzamentos, **8,3%** do quadro coberto por algo
legível. Nenhuma relaxação conserta isso, porque não havia sobreposição.

Duas etapas novas, nesta ordem — **espalhar, depois dobrar**:

1. **`_spread`** (`FLOW_SPREAD_ROUNDS = 60`, `FLOW_SPREAD_MARGIN = 24`, `FLOW_SPREAD_PULL = 0.06`)
   — toda bolha de ESTADO repele toda outra dentro da própria caixa de rótulo mais uma margem
   (elipse normalizada pela caixa do par), e uma mola fraca puxa cada ponto para a MÉDIA dos
   vizinhos. A repulsão preenche o quadro; a atração é o que deixa um ponto sair do `x` do seu
   rank — com ela em 0 os cruzamentos SOBEM (36 → 57) e as colunas sobrevivem (5 → 8 valores de
   `x`). Âncoras entram como obstáculo e como vizinho, nunca no mapa de deslocamento.
2. **`_compact(target_aspect)`** — o alvo deixou de ser a constante 1,5 e virou PARÂMETRO: 1,5 não
   é propriedade do grafo, é um chute sobre a superfície onde ele é desenhado. Quem mediu o
   próprio container passa `max(w, h) / min(w, h)`; o site, o protótipo e as goldens continuam na
   constante (a fixture cruzada é gerada com aspecto fixo, mais um caso `phone_aspect` que existe
   só para travar o argumento). Espalhar ANTES de dobrar é medido: na ordem inversa o
   espalhamento achatava o desenho de volta para aspecto 2,65 numa tela que pedia 1,79.

**`_pinned_axes`** é o terceiro conserto e é de raiz. Um ponto livre espremido entre DUAS âncoras
imóveis no mesmo eixo está num ciclo-limite: empurrá-lo para longe de uma o enfia na outra pela
mesma quantidade. Medido no caso `crowded`/`triangulo`: `side control` ficou 1,84 unidades dentro
de `start neutral` E de `start bottom` pelas 120 rodadas, e subir para 200 rodadas não mudou nada,
porque o ponto fixo daquele par de forças É a sobreposição. O par cuja saída barata está travada
passa a resolver pelo outro eixo — a saída sempre esteve lá (44,5 unidades na horizontal).

**Medido, bundle do dono, antes → depois** (viewport útil 390×700 e 1280×700; ocupação = caixa
desenhada na escala de fit sobre a área do viewport):

| | antes | depois |
|---|---|---|
| ocupação, 390×700 | 49,2% | **83,2%** |
| ocupação, 1280×700 | 52,2% | **89,8%** |
| escala de fit (tamanho do texto) | 0,309 / 0,576 | **0,534 / 1,005** |
| `x` distintos entre 11 pontos livres | 5 | **11** |
| cruzamentos | 36 | **14** |
| cobertura legível do quadro | 8,3% | **14,8%** |
| pares nome×nome sobrepostos | 0 | **0** |

Duas das três parcelas foram medidas isoladas: o aspecto do viewport sozinho vale +12/+15 pontos
de ocupação, o espalhamento sozinho +6/+9, e o resto é o enquadramento (App: `computeFitTransform`
passou a enquadrar a CAIXA em vez de uma caixa centrada no centróide ponderado num layout pinado —
0,3086 → 0,3413 de escala, 49,2% → 60,2% de ocupação, antes de qualquer mudança de layout).

⚠️ **Rejeitado por medição:** encolher a elipse da moldura (`FLOW_ANCHOR_RX_SHARE` 2,0 → 1,5/1,2/1,0)
aumenta a cobertura legível (14,8% → 19,8/24/27%) e QUEBRA o critério duro — 1 a 2 pares de nomes
de estado sobrepostos no aspecto do celular em todas as variantes menores — além de piorar
ocupação (83 → 74/67%) e cruzamentos (14 → 27/31). A moldura larga fica.

⚠️ **O bundle do site precisa ser regerado**: `_spread` move toda posição de `pathGraph`
(`GA_OCEAN` e os inline de `grapple-*`/`breakdown-*`). É `export/site_data.py`, mesmo precedente
de §10.5.2 — a mudança de código não invalida o `item_hash` sozinha.

### 10.5.2 Estrutura de âncora: o triângulo, em todo lugar (dono, 2026-09-01)

`DEFAULT_ANCHOR_STRUCTURE` passou de `pentagono` para `triangulo`, e é o default de verdade: o
App **removeu a fileira de pills** de estrutura (`NetworkScreen`, chaves de i18n apagadas nas duas
locales) e os três displays do site (`the-ocean`, `grapple-*`, `breakdown-*`) herdam o default via
`corpus_paths.path_payload`. A tabela `ANCHOR_STRUCTURES` continua com as três linhas — o
protótipo é justamente a página onde elas são comparadas. A chave segue `triangulo` (pt-BR, como
foi cunhada): renomear churnaria toda golden por uma grafia.

⚠️ As variantes 1–12 do protótipo ficaram presas ao pentágono POR NOME (`_PENTAGON_STRUCTURE` em
`render_map_prototypes`), não ao default. Elas carregam a garantia de byte-identidade do `diff -r`
e o default agora é uma afirmação sobre os PRODUTOS, não sobre a página de comparação.

⚠️ **O bundle do site precisa ser regerado**: as posições de `GA_OCEAN.pathGraph` e dos
`pathGraph` inline mudaram (default novo + as duas etapas). É `export/site_data.py`, e a mudança
de código não invalida o `item_hash` sozinha — mesmo precedente de `BREAKDOWN_VERSION`/`DOSSIER_VERSION`
em §12.7.

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
  que estiver selecionado). É a exigência de mobile do dono, não uma economia — e é também a
  razão de a relaxação de §10.5.1 dar a última palavra aos nomes de estado.
- **Teto declarado da relaxação: três classes de par que ela não resolve, todas por geometria de
  TRAÇO e não de ponto.** (a) o rótulo de uma aresta contra a PRÓPRIA ponta — ele mora no meio do
  traço, logo sempre a meia aresta das duas pontas, e nenhum arranjo de pontos separa um ponto
  médio das suas próprias extremidades; (b) um LAÇO (`start top --[headquarters pass]--> start
  top`) tem o ponto médio EM CIMA do nó; (c) traços PARALELOS entre o mesmo par têm o mesmo ponto
  médio de corda, embora `mapEdgeArcGeometry` os abra em leque e rotule cada um no meio do ARCO.
  (b) e (c) ficam fora do conjunto de bolhas; (a) fica fora do laço de pares. O conserto dos três
  é do renderizador, e o site já o fez uma vez (§12.5: o rótulo de ação sai do traço, na
  perpendicular). Caminho de upgrade: passar `parIndex`/`parCount` para dentro do layout e
  deslocar a bolha na mesma perpendicular do leque.
- **`FLOW_RELAX_MAX_BUBBLES = 240`**: acima disso a relaxação é PULADA inteira e o layout é a
  grade de sempre. É o oceano (§12.4 já declara um layout em camadas errado para um grafo com 3
  fontes e 139 nós num rank). O laço é O(bolhas²) por rodada; o upgrade é um grid hash, não um
  orçamento maior.
- `Point.id` de um estado é `s:{node_key}` e **contém espaços**. Nada aqui parte id por espaço —
  mas é exatamente o defeito vivo de `systemDominance.ts:167-174` no App, e o port da Fase 5 tem
  de nascer sabendo.
- Um ponto `branch-merge` é honesto quanto às rotas (§10.3), mas o olho ainda pode "seguir" uma
  entrada de p1 até uma saída de p2 num traço estático. O que desfaz isso é a seleção (destaque
  atravessa os troncos compartilhados). No bundle do dono não existe nenhum; no corpus são 26.

---

## 11. Fase 5 (2026-09-01) — o port para o App, e o que a paridade encontrou

A camada de dados existia só em Python. Esta fase a espelha no App, com golden para cada peça, e
fecha os 3 testes cross-repo que estavam vermelhos desde a Fase 2
(`test_golden_fixture_matches_this_implementation`, `test_app_inference_table_matches_analytics_source`,
`test_generator_check_flag_is_green`). Espelho curto do lado do App: `GrapplingArcApp/docs/EDGE_AS_PATH.md`.

### 11.1 Os geradores, e a disciplina

| gerador | golden (nos DOIS repos) | espelho no App |
|---|---|---|
| `export_taxonomy_kind_fixtures` | `data/rating/taxonomy_kind_golden.json` | `services/taxonomyInference.ts` |
| `export_chain_compiler_fixtures` (novo) | `data/rating/chain_compiler_golden.json` | `services/chainCompiler.ts` |
| `export_map_aggregate_fixtures` (novo) | `data/rating/map_aggregate_golden.json` | `services/map/mapAggregate.ts` |
| `export_path_bundling_fixtures` (novo) | `data/rating/path_bundling_golden.json` | `services/map/pathBundling.ts` |
| `export_path_metrics_fixtures` (novo) | `data/rating/path_metrics_golden.json` | `services/map/pathMetrics.ts` |
| `export_flow_layout_fixtures` (novo) | `data/rating/flow_layout_golden.json` | `services/map/flowLayout.ts` |
| `export_actions_parity_fixtures` (passou a escrever nos dois) | `data/rating/actions_parity_golden.json` | `__tests__/actionsParity.test.ts` |
| `export_node_key_fixtures` | `data/rating/node_key_golden.json` | `utils/__fixtures__/nodeKeyGolden.json` |

`tests/test_cross_repo_fixtures.py` roda, para cada um, os dois testes que pegam coisas
diferentes: `--check` verde (o arquivo em disco é o que o código de HOJE gera — pega um golden
obsoleto) e bytes idênticos nos dois lados (pega uma edição à mão de um lado só).

### 11.2 `orientation_for_inference` no App sem um segundo port de `attribution.py`

O nível 3 da regra é `attribution.classify(...).actor_role` — 74 rótulos curados num módulo
Python. Em vez de um segundo port deles, o gerador **achata `classify`** em
`actor_role` (`"tipo|rótulo" -> papel`) + `actor_role_default` (por tipo), dentro do
`taxonomy_kind_golden.json`, junto com a `state_orientation` verbatim. `classify` é uma função
PURA de tabelas finitas, então achatar não aproxima nada: as linhas curadas são enumeráveis, e
cada linha é produzida CHAMANDO `classify`, nunca relendo as tabelas dela nesta ordem — a
precedência (`_LABEL` > o conjunto curado do tipo > o default do tipo) fica preservada por
construção. `test_golden_actor_role_block_answers_exactly_like_classify` é o que mantém isso
honesto. Mesma disciplina de `library_lookup.json`, na direção contrária.

O golden carrega também as **256 leituras compostas** de `orientation_for_inference`
(`{value, source}`) — o `source` importa tanto quanto o `value`: dois lados podem concordar em
`top` com um lendo a linha curada e o outro derivando via `attribution`, e no dia em que a linha
curada mudar eles param de concordar sem nenhum teste dizendo isso.

### 11.3 Três defeitos que a paridade encontrou, todos consertados na RAIZ

1. **`Aggregate.add_edge` lia `actions[0].inferred`** — a dependência de posição que o §5 deste
   documento registrou como "conhecida, documentada e não consertada". Estava VIVA no bundle do
   dono: `half guard --[sweep(inferida), knee cut pass(observada)]--> side control` lia
   "inferida", enquanto o espelho dela
   `de la riva --[berimbolo(observada), sweep(inferida)]--> back control` lia "observada" — mesmo
   conteúdo, resposta diferente, só pela ordem. As políticas de gate (`no_inferred_edges`,
   `inferred_min2`) filtram por esse campo, então a leitura antiga escondia uma observação atrás
   de uma inferida e derrubava a aresta. Agora: `all(a.inferred for a in e.actions)`.
2. **`systemDominance.ts` partia a chave de agregação no espaço** (`key.split(' ')`) — o mesmo
   defeito que `9e61921` consertou só no `mapCollapse.ts`. Um qid É um `node_key` e um `node_key`
   CONTÉM espaços, então o Louvain recebia fragmentos ("closed", "guard") que não são nós: **toda
   comunidade detectada em dado multi-palavra estava silenciosamente errada**, e as fixtures
   escondiam isso usando chaves de uma palavra só. Os endpoints agora viajam NA LINHA.
3. **O índice de identidade da biblioteca no App usava `getAllNames`**, que também devolve o
   `type`/`tipo` do nó. Certo para BUSCA, errado para IDENTIDADE: pôs as dez palavras de tipo
   (`guard`, `control`, `pass`, `sweep`, `submission`, `takedown`, `escape`, `transition`,
   `defensive`, `concept`) no índice, então um evento com o rótulo literal "Sweep" — 207 deles no
   corpus — resolvia para a primeira técnica que a biblioteca lista, e `resolveGroup`
   REESCREVIA o nome e o tipo da entrada para os dela. A Analytics nunca fez isso (`_name_variants`
   é name + translations + variations): **10 chaves divergentes de 689**, medidas. O App agora
   espelha `_name_variants`. Achado pelo bloco de sondas de `orientation_for_inference` — que é o
   teste que mantém o conserto.

### 11.4 Medido

| | |
|---|---|
| casos do golden do compilador | 22 de cadeia única + 3 de dois lados, **todos idênticos ao Python na primeira execução** |
| casos de bundling | 13 (os 5 do dono + as 3 regras + ação repetida + laço + os degenerados), incluindo `walkable_routes` |
| casos de métricas | 11 |
| casos de layout | 4 fixtures × 3 estruturas de âncora = 12 conjuntos de posições + 4 de ranks |
| sondas de orientação | 256 (`{value, source}`), 0 divergências depois do conserto 11.3.3 |
| P1 sobre o `mock_user_bundle` | multiconjunto `(action_key, actor, inferred)` **idêntico** nos dois repos |
| agregado sobre o `mock_user_bundle` | 13 estados, 21 arestas, 17 handovers — **idênticos** nos dois repos |
| suíte Analytics | 2398 passed, 1 skipped; ruff e mypy limpos |
| suíte App | 3004 passed, 304 suites; lint, tsc e `build-editor --check` limpos |

### 11.5 Fora de escopo, por instrução

Nenhuma tela e nenhum renderizador. `mapScreenViewModel` emite `mapActions: string[]` (o canal
novo) e mantém `mapLabel` como uma ponte DERIVADA (o mesmo array unido) porque os dois
renderizadores de aresta de hoje desenham uma string por arco; a onda das telas apaga `mapLabel`.
Nada de Lamas/Markov/Glicko foi tocado — `test_p2_observations_for_side_is_byte_identical_pinned`
continua verde e é a prova disso.

---

## 12. Onda A (2026-09-01) — os mapas do site público sobre o compilador

Os três displays de mapa do site (`the-ocean.html`, `grapple-*.html`, `breakdown-*.html`) passam
a desenhar o modelo "aresta = caminho". Derivação nova, renderizador novo, **contrato aditivo**:
nenhum payload antigo mudou de forma.

### 12.1 Derivação — `analysis/corpus_paths.py` (arquivo novo)

Porta de entrada separada da do protótipo, de propósito. `scripts/render_map_prototypes.py`
agrega `you`/`partner` do bundle PRIVADO do dono; o site agrega dois atletas REAIS de uma
`matches.sequence` pública. Os três módulos de análise embaixo são os mesmos
(`chain_compiler` → `path_bundling` → `path_metrics`/`flow_layout`); o que difere é o modelo de
ator e a regra de perspectiva — exatamente as duas coisas que **não** podem ser compartilhadas
entre um bundle privado e um artefato público.

```
aggregate_bouts(bouts, collapse_actors=False) -> PathAggregate   # camada 1
render_paths(agg)                             -> [RenderPath]     # camada 2
path_payload(agg, structure=…, min_count=1)   -> {nodes,links,paths,stats}   # camadas 3+4
```

| escopo | entrada | ator |
|---|---|---|
| breakdown | a luta, dois lados (`_sequence_view`) | `a`/`b` qualificados — a montada dela não é a dele |
| dossiê | só os eventos DELE, em todas as lutas (`_athlete_path_graph`) | um lado só |
| oceano | o corpus inteiro (`_corpus_bouts`) | **colapsado** — o oceano é o espaço técnico do corpus, e a montada de A e a de B são o mesmo fato |

Regras herdadas do protótipo e reimplementadas aqui: a âncora do lado `b` é espelhada
(`_perspective_key`) para o mapa ter **um** eixo vertical; âncoras e genéricos `shared` nunca são
qualificados por ator; `_index_parallel_links` abre em leque duas arestas entre o mesmo par.

Duas coisas que o lado do site precisou e o protótipo não: **rótulo em inglês** (o
`inference_table.json` nomeia os genéricos em pt-BR, que é a locale do App — a `action_key` dos
genéricos já É o nome em inglês, então title-case da chave é a tradução, sem segunda tabela para
divergir), e o **timestamp do vídeo na AÇÃO** (uma ação é o que aconteceu num instante, e neste
modelo a ação mora na aresta — então o seek do breakdown pendura em `link.ts`, não no nó).

### 12.2 Custo — medido, não estimado (742 lutas finais, 10 016 eventos)

| | tempo |
|---|---|
| compilar + agregar o corpus inteiro | **0,33 s** |
| `path_payload` do oceano (bundling + layout) | **0,91 s** |
| os 642 atletas, um payload cada | **1,6 s** |
| uma luta (a maior, 84 eventos) | **0,01 s** |

Nada aqui é o gargalo. O `build_ocean` que roda ao lado custa ~300 s e é anterior a este
trabalho. Peso do oceano em bytes: 1,66 MB cru / **104 KB gzipped** (o Pages serve gzip).

### 12.3 O único gate, e por que ele só existe no oceano

Sem gate o corpus desenha **2 370 caminhos sobre 221 pontos**, e o `flow_layout` empilha **139
deles no MESMO rank** — um mundo de 2 700 × 22 200, aspecto 8,2, ilegível em qualquer zoom. Com
`min_count=2` (o caminho tem de ter acontecido pelo menos duas vezes): 52 pontos, 396 traços,
aspecto 2,3 — a mesma ordem do mapa de força que ele substitui (68 nós / 964 arestas).

O custo está declarado, não escondido: caem as ocorrências únicas e com elas quase todas as
trilhas longas (tinta compartilhada 14,6% → 5,5%). Essa é a linha editorial **só aqui** — o
oceano sempre publicou um "top slice", e uma trilha vista uma vez em 742 lutas é uma anedota
sobre o corpus. O dossiê e o breakdown são afirmações sobre UM atleta e UMA luta, então nenhum
dos dois é gateado: lá a ocorrência única é o assunto.

⚠️ **Gates que foram medidos e recusados**: suporte da RELAÇÃO (`>=5` ainda deixa 1 741
caminhos), top-K estados (`K=20` deixa 1 851 — os caminhos são muitos entre poucos estados),
`count>=2 or length>=3` (1 111). Nenhum resolve a densidade sem virar arbitrário.

### 12.4 Teto conhecido — `flow_layout` não escala para um grafo denso

Diagnóstico medido: no corpus só 3 pontos (de 221) têm grau de entrada 0, e a BFS multi-fonte
sai deles e alcança quase tudo em 1–2 saltos. Um layout em camadas então não tem o que
estratificar: 9 ranks, um deles com 139 nós. O layout está certo para um grafo em forma de
CADEIA (a luta, o dossiê — as capturas mostram os dois lendo bem); o oceano ganha uma moldura
legível mas não um fluxo.

Correção sugerida, **não aplicada** (é `analysis/flow_layout.py`, de outro dono): semear os ranks
nas **âncoras**, que são as fontes e os sorvedouros semânticos e hoje ficam de fora da grade
(`free = [p for p in ids if p not in anchor_slots]`).

### 12.5 Renderizador — `GAGraph.mountPaths` em `GrapplingArc/site/graph.js`

Uma SEGUNDA entrada, deliberadamente não uma flag no `mount()`. O payload já vem posicionado, e
a simulação de forças não é só desnecessária aqui, é antagônica — a variante 13 do protótipo
precisa desligá-la com `charge:0/linkDist:1/gravity:0/bounded:false/collide:false` e fixar todo
nó. Um grafo estático também não precisa de laço de animação: `mountPaths` desenha sob demanda
(resize, pan, zoom, seleção), que é o que torna 2 400 traços viáveis num celular.

Três consequências que valem mais que a economia de linhas:

1. **A retrocompatibilidade vira estrutural.** `git diff site/graph.js` tem exatamente dois
   hunks: uma inserção depois do fim de `mount()` e a linha do `global.GAGraph = {…}`. **Zero
   linhas dentro de `mount()` mudaram** — logo todo hero/card/dossiê/breakdown/oceano antigo
   desenha idêntico, e isso é uma prova, não uma alegação.
2. **`scripts/render_map_prototypes.py:_patch_graph_js` continua funcionando.** Ele aplica ~20
   patches por âncora de string EXATA no `site/graph.js` real, e `tests/test_render_map_prototypes.py`
   roda isso contra o arquivo de verdade. Portar as features para dentro do `mount()` teria
   quebrado 8 dessas âncoras (o loop de links, `fitTarget`, a linha do raio, a do `dim`, a do
   `mapLabel`, o par do preenchimento, o handler de clique) e deixado a suíte vermelha.
3. `GrapplingArcWeb/src/vendor/graph.js` (cópia vendorizada) não diverge no caminho comum.

Features portadas da variante 13: posições fixas (`n.pin`), losango de âncora, preenchimento
partido (`n.split`), ponto de andaime (`n.junction`), curva quadrática com leque paralelo
(`par`/`parCount`) e arco de retorno (`bow`/`back`), rótulo por segmento (`l.label`/`l.actions`),
tracejado do fantasma (`l.inf`), realce explícito por `pathIds`, `minZoom`, seleção de aresta.

Quatro correções que o dado real forçou e o protótipo não tinha visto (todas medidas em captura
headless 1280×800 e 390×840):

- **Rótulo em espaço de tela, não de mundo.** O raio do nó encolhe com o zoom, o texto não: um
  deslocamento em unidades de mundo enterra o rótulo dentro do nó assim que o mapa é ajustado
  (o fit de um mapa posicionado é ~0,3, nunca 1,0). O mesmo vale para o anel de seleção.
- **Nenhum gate de zoom em rótulo.** Com `cam.k >= 0.85` um mapa de 6 nós saía com DOIS rótulos.
  Todos são oferecidos e a passada de prioridade + colisão decide (nó antes de aresta, âncora
  antes de estado).
- **Rótulo de ação ACIMA do traço, de nó ABAIXO do nó.** Numa cadeia horizontal — que é o que um
  layout de fluxo produz — os dois caem na mesma linha e os de nó, colocados primeiro, comiam
  toda ação.
- **A reserva de margem do fit tem teto.** "Scissor Sweep → Guard Pull → Scissor Sweep → Single
  Leg Takedown" tem ~420 px; num telefone de 390 px isso reserva mais margem do que existe tela e
  colapsa o mapa a uma miniatura.

Mobile: fit sobre os limites reais (raio + rótulo), **orientação vertical** quando a tela é alta
e o fluxo é largo (troca de coordenadas, não segundo layout), `touch-action: pan-y` (nunca
`none` — a tela vira armadilha de scroll), pinça, e `inset` para o mapa não ser centrado
embaixo do HUD flutuante do oceano.

### 12.6 Prova

| peça | prova |
|---|---|
| nenhuma rota fantasma | `tests/test_corpus_paths.py::test_no_phantom_route_over_a_corpus_shaped_aggregate` — `walkable_routes()` == entrada, sobre o agregado que o exportador realmente monta |
| determinismo | dois runs + ordem de entrada trocada, JSON idêntico (o bundle é COMMITADO) |
| dado privado nunca entra | teste de AST: `analysis/corpus_paths.py` não importa nada de `db.` nem de `schemas.app_types`; `_corpus_bouts`/`_athlete_path_graph` leem `Match.sequence` e nenhum campo de posse de grafo |
| forma do payload | `pathIds` não vazio em todo traço, `actions[]` presente, todo nó fixado, âncoras dentro do vocabulário |
| pt-BR não vaza | asserção explícita sobre o JSON inteiro |
| ids do bundle | `scripts/check_site_bundle.py` (Q1) agora cobre `GA_OCEAN.pathGraph` — `stateKey` e chave de ação contra as DUAS canonizações do repo (com e sem `clean_label`), com os genéricos da tabela excluídos |
| retrocompatibilidade | `git diff site/graph.js` = 2 hunks, nenhum dentro de `mount()`; `mount()` legado remontado em headless |
| protótipo intacto | `tests/test_render_map_prototypes.py` 46 passed |
| suíte | 2407 passed, 1 skipped; ruff e mypy limpos |

### 12.7 Modo de preview do exportador

`uv run python -m export.site_data --out <scratch> --only <slug> …` constrói e renderiza só
aquelas páginas de detalhe. Existe porque um run completo é o N+1 de ~10–12 min sobre o Supabase
remoto (skill `site-export-perf-campaign`) e iterar num renderizador contra esse laço não é
viável. Os globais que ele escreve são PARCIAIS por construção, então `main()` recusa apontá-lo
para o diretório do site de verdade, e ele usa `.export_cache/preview/` — um preview jamais pode
substituir o cache compartilhado por meia dúzia de itens e transformar o próximo export real num
run frio.

`BREAKDOWN_VERSION` / `DOSSIER_VERSION`: o `item_hash` cobre os campos de DB do item, que é o
contrato certo para DADO — mas uma mudança de CÓDIGO que acrescenta uma chave (`path_graph`)
deixa todo item em cache válido e silenciosamente sem ela, e o renderizador cai no fallback no
corpus inteiro. Mesmo precedente do `PROFILE_VERSION`.

### 12.8 Fora de escopo

`strength` sai `null` no site: o `rating_of` é injetável e nenhum dos três chamadores tem os
ratings Glicko por nó à mão sem um N+1 novo. `GrapplingArcWeb/src/vendor/graph.js` continua na
cópia antiga (item de backlog: re-vendorizar). Os cards pequenos das páginas escritas à mão
(`index.html`, `breakdowns.html`, `grapple-like.html`) seguem no `graph` legado — são arte
ambiente, e o payload novo é aditivo justamente para não os tocar.

## 13. Família de rota, variante e ocorrência não resolvida (decisão do dono, 2026-09-01)

**Princípio:** inferência pode criar evidência que falta; nunca cria topologia canônica
redundante. Caminho concreto domina caminho genérico. Uma transição genérica inferida é
evidência PROVISÓRIA da família de rota e é refinada, absorvida ou rebaixada a evidência não
resolvida assim que existe caminho concreto. Uma observação de sessão nunca é multiplicada
entre várias variantes para pontuação.

### 13.1 Três níveis — os mesmos três que o §1 já tinha, nomeados

| nível | nome | identidade | já existia como |
|---|---|---|---|
| estado → estado | **família de rota** | `(source, target, actor)` | relação canônica (§1, invariante 2) |
| cadeia de ações | **variante** | `(família, chave das ações OBSERVADAS)` | chave de agregação — hoje inclui as inferidas (§13.3 muda isso) |
| travessia de uma sessão/luta | **ocorrência** | `occurrenceId` + `actions[]` próprio | ocorrência (§1) |

Uma ocorrência **resolve** para uma variante ou fica **não resolvida** na família.
`support` (§3) já é contagem da família; `count` de um caminho é contagem da variante.

### 13.2 O que é concreto e o que é genérico

- **Variante concreta**: pelo menos UMA ação observada na cadeia. `[Armbar(obs), Sweep(inf)]`
  é concreta — carrega evidência real e tem chave própria (`armbar`). A lacuna inferida é
  anotação da ocorrência, desenhada em fantasma, nunca identidade.
- **Placeholder genérico**: a cadeia inteira é inferida (`all(a.inferred)`), ou vazia. Só ele
  é substituível. O rótulo genérico (`sweep`/`reversal`/`transition`) é ANOTAÇÃO do balde não
  resolvido, não identidade: `A→[sweep?]→B` e `A→[reversal?]→B` são UM balde,
  "não resolvido (sweep ×3, reversal ×1)".
- **Só o genérico é substituído.** Uma variante parcialmente inferida NUNCA é fundida em outra
  (regra literal do dono): `A→[Armbar(obs), ?]→B` continua variante própria ao lado de
  `A→[Armbar, Wrestle-Up]→B`; o bundling (§4) já compartilha o tronco `Armbar` visualmente,
  e colapsar afirmaria que Wrestle-Up aconteceu.

### 13.3 Regra do agregador (ordem executável)

```
estados   ← pontas observadas (âncoras nas pontas, §1b)
ações     ← eventos observados entre elas; inferência preenche lacunas (§2)
se alguma ação observada:            ocorrência → variante chave(subsequência observada); cria se nova
senão, se a família tem ≥1 variante: ocorrência → família.unresolved (rótulo = palpite inferido)
senão:                               emite placeholder genérico (fantasma) — é família.unresolved disfarçado
```

**Absorção é recomputação, não migração.** App, site e protótipo derivam o mapa de sessões/
lutas a cada build; nada é guardado por variante. Quando uma variante concreta chega (sessão
editada, vídeo processado, luta importada), no próximo compile o placeholder simplesmente não
é emitido e as ocorrências dele contam na família. Só as tabelas da Fase 6 precisam da regra
de migração (§13.6).

### 13.4 Pontuação — o que "propagar pelos caminhos existentes" significa

Uma ocorrência não resolvida entre A e B **propaga contexto de família**, não observação de
técnica:

| muda | não muda |
|---|---|
| `support` da família (todos os caminhos A→B a exibem) | `count` de cada variante |
| tráfego estado→estado, importância do nó, limiares de filtro (avaliados na família) | rating por técnica / `strength` (§3 já ignora inferidas) |
| painel: "A → B: 3 variantes · 2 não resolvidas" | evidência Glicko (`observations_for_side` lê eventos crus — inferidas nunca entram; `ratingV2Evidence.ts` idem) |

**Invariante P5 (teste, nos dois repos):** uma ocorrência toda inferida contribui **0**
observações de rating, **+1** em `support` da família e **0** em qualquer `count` de variante.
É verdade por construção hoje; o teste é o que mantém a Fase 6 (ELO da aresta derivado de
`actions[]`) honesta. Nenhum tronco fantasma A→B é desenhado para o tráfego não resolvido —
com `[Sweep]`, `[Wrestle-Up]`, `[Armbar,Sweep]` não há prefixo de ação comum, o "tronco" é o
próprio estado A; o não resolvido aparece na espessura/importância do estado e no painel.

### 13.5 Proveniência (aditivo)

`ChainAction.provenance ∈ {user, video_high, video_low, inferred}` (default `user`;
`inferred=True ⇔ provenance='inferred'`, o booleano continua como adaptador). Ordem de
autoridade: `user` ≥ `video_high` > `video_low` > `inferred`. Precedência só importa quando a
MESMA sessão é relida (refinamento por vídeo): isso é edição da sessão, coerente com "ações
observadas são imutáveis PARA A INFERÊNCIA" (§2), não para evidência melhor.

### 13.6 Fase 6 — forma das tabelas e migração

`graph_edges` (unique `(graph_id, source_key, target_key)`) É a linha da família.
`graph_edge_variants(edge_id, observed_key, actions jsonb)`;
`graph_edge_occurrences(variant_id NULL = não resolvida, session/match ref, provenance)`.
Migração: aresta antiga toda inferida → ocorrência com `variant_id NULL`; só aresta com chave
observada vira variante. Nunca escolher uma variante arbitrária, nunca duplicar entre todas.

### 13.7 Onde mexe (implementação)

`analysis/corpus_paths.aggregate_bouts`, `scripts/render_map_prototypes.Aggregate.add_edge`,
App `services/map/mapAggregate.ts` (chave = subsequência observada; `unresolved` na relação),
payload/view model (`unresolved` por relação), painel (`PathSelectionCard`, `graph.js`
`mountPaths`), teste P5 nos dois repos, goldens `map_aggregate`/`path_bundling` regenerados.
Rating, bundling e layout intocados.

---

## 14. Fase 5c (2026-09-01) — a camada de rótulos, em espaço de TELA

Esta fase não muda o layout de mundo. `analysis/flow_layout.py`, `services/map/flowLayout.ts` e
`flow_layout_golden.json` estão **intocados** — nenhum byte de golden se moveu, e nada aqui exige
regenerar o site.

### 14.1 A causa raiz, medida

Os dois renderizadores desenhavam o rótulo DENTRO do transform do mundo, com o corpo da fonte em
unidades de MUNDO. Então a glifa escala com o zoom, e o tamanho real na tela é o corpo vezes a
escala de fit. Medido no bundle do dono, na escala de fit de cada escopo/viewport:

| escopo | viewport | escala de fit | um "12px" desenha a |
|---|---|---|---|
| Global | 390×700 | 0,534 | **6,4 px** |
| Costas | 390×700 | 0,777 | 9,3 px |
| Global | 1280×700 | 1,005 | 12,1 px |
| Guarda Fechada | 1280×700 | 2,537 | **30,4 px** |

O mapa do celular não estava poluído, estava **ilegível**; o do desktop estava grande demais e com
9 pares sobrepostos em 48 rótulos. E o critério de "zero sobreposição" da §10.5.1 é medido em
unidades de MUNDO, que sob um transform uniforme é **invariante de escala** — ele estava satisfeito
exatamente enquanto o dono lia 6,4 px, e um rótulo que o olho não separa no fit continua
inseparável a 4× de zoom. Por isso o zoom nunca revelava nada.

O conserto tem uma peça: **a glifa é um número constante de pixels de TELA**, e por consequência a
colocação também é decidida em pixels de tela. O rótulo continua ancorado no ponto de MUNDO que
nomeia, dentro de um grupo `1/zoom` que cancela o zoom da câmera — `<Group transform>` aninhado no
Skia, `translate(...) scale(1/k) translate(dx,dy) rotate(θ)` no SVG e no canvas do site.

### 14.2 As duas metades, e por que só uma é compartilhada

| metade | o quê | onde |
|---|---|---|
| **classificação** | classe e prioridade de cada nome, e o piso por zoom | `mapScreenViewModel.ts` (`mapNodeLabelPriority`/`mapSegmentLabelPriority`/`minLabelPriorityFor`) ↔ `site/graph.js` (`pathNodeLabelPriority`/`pathSegmentLabelPriority`/`pathMinLabelPriority`) |
| **colocação** | qual slot, com que alias, dentro de que orçamento | `labelLayout.solveLabelPlacements` (App, um só para Skia e SVG) ↔ `pathSolveLabels` (site) |

Ordem das classes, literal do dono: `selected > anchor > hub state > prominent segment (top-K) >
state > action > minor action`.

⚠️ **A classificação NÃO entra no payload.** Ela é derivada de campos que o payload já carrega —
`node.kind`, `node.size`, `link.weight`, `link.inf` — nos dois lados. Serializar um `labelPriority`
custaria um campo aditivo, um bump de `BREAKDOWN_VERSION`/`DOSSIER_VERSION` e uma regeneração
completa do site por informação que já está lá. É o mesmo precedente de `directedEdges.ts` ↔
`analysis/network_metrics.py`: uma regra, espelhada, com teste dos dois lados. `corpus_paths.py`
fica intocado.

### 14.3 O solver

Guloso, prioridade primeiro, uma passada. `O(candidatos × slots × colocados)` — no mapa, ~50 × 6 × 30.

1. ordem total: prioridade decrescente, id crescente (nunca a ordem do array);
2. um candidato `pinned` (âncora, ou qualquer coisa que a seleção acendeu) ignora o orçamento mas
   **gasta** dele — senão um celular orçado em 10 desenha 14 e o número deixa de significar algo;
3. 4-6 slots em ordem fixa; slot que sai do viewport, encosta em rótulo já colocado ou cai sobre o
   chrome é **rejeitado**, nunca só penalizado;
4. penalidade = distância do slot preferido + área sobre obstáculos MOLES (discos de nó, os traços
   proeminentes); acima do limiar tenta o **alias**; se nem ele couber, o nome é **descartado**.
   Uma pilha ilegível é pior que uma palavra faltando, e o painel carrega o nome inteiro.

**Orçamento** = `área / 28000`, travado em [8, 34]: 390×700 → 10, 1280×700 → 32. Um número, não dois.

**Zoom semântico** é o piso de prioridade: `overview` = estados + troncos top-K, `medium` = ações,
`detail` = tudo. O resto sai de graça — o conjunto de candidatos é o que está NA TELA, então ampliar
também reduz a concorrência.

**Rótulo arqueado** (dono, 2026-09-01): num traço curvo (bow de retorno, leque paralelo) o nome
segue a **tangente** no ponto do rótulo (`MapEdgeArc.curveAngle`, `B'(t)`, virada para ficar de pé);
traço reto continua horizontal. O solver reserva o **AABB da caixa girada**. Parou na tangente e não
no arco real (`TextPath`/`<textPath>`) porque sobre um nome de ~70px a curvatura destes traços é de
poucos graus — o upgrade está nomeado em `mapEdgeGeometry.curveAngle`.

### 14.4 Chrome: remover o obstáculo saiu mais barato que encolher o mapa

O chip de dica e o botão de reset são desenhados DENTRO da área do gráfico. Medido: com o botão de
reset parado no canto inferior direito, o escopo `Guarda Fechada` a 390×700 fitava a âncora de
finalização em y = 676 de 700 — todo slot abaixo saía do viewport, todo slot acima batia no botão, e
o vértice da moldura ficava **sem nome** (1 de 2 âncoras).

Insetar o FIT resolve e custa **10-15% da escala** (0,534 → 0,479 no celular; 1,005 → 0,856 no
desktop), que é um terço da ocupação que a §10.5.3 mediu. Então o obstáculo saiu em vez do mapa
encolher: o **reset só aparece quando há o que resetar** e a **dica some no primeiro gesto**. Com
isso a âncora é nomeada na escala de fit CHEIA (2 de 2, escala inalterada). `computeFitTransform`
ganhou um argumento `inset` mesmo assim, e o único chamador é o enquadramento de SELEÇÃO — onde o
painel realmente cobre metade da tela.

### 14.5 Experimento: fluxo esquerda→direita no celular (dono, 2026-09-01)

Constante `MAP_TALL_SCREEN_FLOW` em `NetworkScreen`, porque os dois são o MESMO layout de mundo sob
uma transformação de apresentação (troca de coordenadas + o `targetAspect` que a dobra mira). Medido
no bundle do dono a 390×840, com esta camada de rótulos aplicada (0 pares sobrepostos em todas as
células):

| escopo | escala de fit (vertical / L→R) | ocupação | nomes |
|---|---|---|---|
| Global | 0,583 / 0,484 | 78,9% / 54,3% | 12 / 12 |
| Costas | 0,992 / 0,717 | 71,0% / 38,7% | 12 / 12 |
| Guarda Fechada | 1,642 / 0,728 | 59,6% / 11,7% | 11 / 8 |

`vertical` ganha em todas. O eixo de rank é o eixo LONGO do desenho, e num celular em pé o eixo
longo da superfície é o vertical; L→R espreme a mesma contagem de ranks em 390px e a dobra devolve
17-56% da escala.

### 14.6 A prova

`GrapplingArcApp/src/services/map/__tests__/mapLabelProof.test.ts` roda o pipeline inteiro
(sessões → escopo → `buildPathView` → view model → fit → solver) em 2 viewports × 3 zooms × todos os
escopos, sobre o mock do App (portão de CI) **e** sobre o export real do dono quando ele está na
máquina (privado, LGPD, `skip` se ausente — mesma forma de `tests/test_path_bundling.py`). Cinco
asserções: nada sobreposto, orçamento respeitado, toda âncora visível nomeada, nada sob o chrome,
nada fora do viewport. `labelSolver.test.ts` cobre o algoritmo (determinismo sob embaralhamento,
prioridade, orçamento, exclusão de chrome, alias, caixa girada) e `mapLabelPriority.test.ts` a
classificação e a paridade das duas cópias do piso de zoom.

### 14.7 Adiado, com o motivo

- **leader lines** — um nome longe do seu ponto precisa de uma linha até ele; sem isso o solver
  prefere descartar a afastar demais, e é a escolha certa por ora (YAGNI v1).
- **arco real de texto** — ver §14.3.
- **traços proeminentes como obstáculo** são amostrados só no ponto do próprio rótulo; amostrar a
  quadrática inteira é o upgrade, nomeado em `mapLabelLayer.ts`.
- **física por rótulo** — o solver é guloso e determinístico de propósito; relaxação já existe no
  layout de MUNDO, que é onde ela pode mover pontos.
