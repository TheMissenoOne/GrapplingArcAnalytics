# Ontologia canônica — estado, ação, sequência (fase N0)

Fase N0 do programa de normalização da ontologia. Aditiva no CONTRATO e **não aditiva na
saída**: ela troca quem decide se um rótulo é estado ou ação, e isso move a classe de 15 pares
`(type, label)`. Nada de `node_key` se move (isso é N1, e exige replay); nada de rating se move
(medido, §6).

Espelho previsto no App: `docs/ONTOLOGY.md`, escrito na fase N3 junto com os goldens. Este
documento é a fonte; o do App é a tradução da mesma tabela para quem lê TypeScript.

---

## 1. A invariante do dono

> **Estado é onde os grapplers estão. Ação é o que eles fazem. Sequência é como um estado vira
> outro. Perspectiva é metadado estruturado — nunca um segundo nome de estado.**

Desdobrada nas decisões de 2026-09-03:

| # | Decisão |
|---|---|
| 1 | **Estado** = representação estática de posicionamento: guardas, controles, configurações posicionais. `Top`, `Bottom` e `Neutral` são estados genéricos **válidos** — quando a fonte não dá a posição específica, usa-se o genérico mais específico que se possa inferir com segurança. O estado do oponente pode ser derivado da relação posicional conhecida (`half guard` já diz que alguém está por cima). |
| 2 | **Ação** = dinâmica, implica tentativa ou efetivação de mudança de estado: berimbolo, armbar, guard pass, sweep, submission/finish, takedown, **back take**. |
| 3 | **Sequência** = combinação ordenada de estados e ações. Um rótulo `"A to B"` **nunca** vira um estado falso; ele se decompõe onde couber — por tabela curada, na ingestão (N2). |
| 4 | **Posições simétricas** (50/50, single leg X, shin to shin) são `neutral` por default. Top/bottom só quando o dado sustenta. |
| 5 | `bout_flags` / `perspective_reliable` **ficam como estão**: mecanismo interno de recusa, nunca exposto ao usuário. |
| 6 | O estado resultante de uma ação bem-sucedida que **fecha** a sequência usa as regras que já existem (`inference_table` / `resolve_closing_anchor`), nunca um segundo conjunto. A UI de Success/Failure é do App, em fase posterior. |

A regra de ouro herdada da fase anterior continua valendo: **estado nunca é inferido, só ação**
(`03_ARESTA_COMO_CAMINHO.md` §7-8). As âncoras (`start top`/`start bottom`/`start neutral`/
`finish`) são moldura de apresentação e a exceção declarada — decisão 1 as reconhece como os
estados genéricos válidos, não como invenção.

---

## 2. O contrato

```
State {
  id,                       # node_key = canonicalize(_normalize_name(label))
  aliases[],                # grafias que colapsam neste id (names.SYNONYMS + biblioteca)
  family,                   # guard | control | anchor | ...
  variant?,                 # "knee shield", "deep" — refina a family, não cria id novo
  perspective: {
    actor_role,             # top | bottom | controlling | controlled | neutral | unknown
    orientation             # top | bottom | neutral (eixo único, state_orientation.json)
  }
}

Action {
  id,                       # mesma normalização — a identidade canônica NÃO muda (invariante 1 do 03)
  aliases[],
  type,                     # pass | takedown | sweep | submission | escape | transition | guard | control
  actor_role
}

Sequence = State →[Action…]→ State      # já é o contrato de 03_ARESTA_COMO_CAMINHO.md
```

**Perspectiva é metadado, não nome.** `Top Half Guard` é `half guard` + `{actor_role: top}`, não
um estado próprio. `Body Triangle (Bottom)` é `body triangle` + `{actor_role: controlled}`. Hoje
os dois existem como `node_key` separados no corpus; separá-los em id + perspectiva move
`node_key` e portanto é N2, não N0.

**Os dois eixos de `attribution` continuam separados de propósito** (`attribution._AXES`):
top/bottom e controlling/controlled respondem perguntas diferentes, e costas pegadas por baixo
são `controlling` **e** `bottom` ao mesmo tempo. `state_orientation.json` tem UM eixo; onde ele
mente é justamente `back control`. Documentado, não resolvido aqui.

---

## 3. Uma autoridade só, e a ordem de precedência

Havia duas. `analysis/attribution.py` mantém um `category ∈ {STATE, ACTION, TRANSITION}` curado
por `(type, label)`, com uma justificativa escrita por linha, e `analysis/taxonomy_kind.kind_of`
**nunca o lia** — decidia por `_FORCED_ACTION_TYPES` (seis tipos) ou por `lamas_chain.lamas_state`
(tokens de ação de Lamas). Discordavam em **526 eventos / 23 pares** dos 10 016 do corpus, e o
site publicava `Front Headlock` e `Collar Tie` como ações enquanto a tabela curada as chamava de
posições.

A precedência agora, em `taxonomy_kind.kind_of`:

| # | Fonte | Por quê nesta posição |
|---|---|---|
| 1 | `type == 'concept'` → `transparent` | `transparent` existe para que um conceito nunca caia silenciosamente numa das outras duas classes (regra D1). Uma linha posicional curada não pode desfazer isso. |
| 2 | `attribution.classify(type,label).category`, **só quando `source == "table"`** | É a resposta curada, com motivo escrito, à pergunta exata que `kind_of` faz. `STATE→state`, `ACTION`/`TRANSITION`→`action`. |
| 3 | `_FORCED_ACTION_TYPES` / `lamas_state` | O heurístico de sempre, agora explicitamente o fallback: responde pelos pares que ninguém curou. |

Dois detalhes que são a diferença entre funcionar e não funcionar:

- **`source == "table"` é o discriminador, não a `category`.** `classify` SEMPRE devolve algo;
  `"type_default"` quer dizer "ninguém curou este par, aqui está o que o tipo implica" — que é o
  mesmo chute do fallback, pior informado. Só linha curada ganha.
- **A autoridade só fala sobre os oito tipos reais de evento** (`attribution.EVENT_TYPES`). Os
  tipos próprios da biblioteca (`defensive`, `concept`, …) e as linhas de bookkeeping
  (`("match","match")`) também chegam a `classify`, e a resposta dela lá é um dar de ombros
  tipado como `TRANSITION` — lê-lo como "ação" promoveria 31 linhas de importação a técnica.

### 3.1 O furo que a resolução pela biblioteca abre

`kind_of_entry` resolve o rótulo pela biblioteca do App ANTES de classificar, porque o `type`
logado é comprovadamente podre (80 de 114 entradas com snapshot obsoleto). Isso é certo e
continua. Mas a biblioteca lista `back take` como **variação de `Back Control`**, então
`resolve_library_entry("Back Take")` devolve uma POSIÇÃO para o que a decisão 2 chama de ação — e
nenhuma ordenação de autoridade a jusante desfaz um rótulo que já foi substituído por outro.

Ponte, explicitamente temporária: `_LIBRARY_VARIANTS_THAT_ARE_ACTIONS = {"back take"}`, conferido
sobre o rótulo **cru**, antes da resolução. N1 dá a `back take` entrada própria na biblioteca
(`type: transition`) e o conjunto morre.

### 3.2 Split de rótulo composto: só por tabela curada, só na ingestão

`"Guard Pass to Mount"`, `"Leg Entanglement / Heel Hook Entry"` — 38 rótulos compostos no corpus.
A regra:

- a decomposição vem de `data/taxonomy/composite_labels.json`, **curada à mão** (ainda não
  existe; ausente lê como vazia). Nunca regex em produção, nunca heurística no compilador;
- ela roda na **ingestão** (`convert_dump` / `apply_events`), nunca no `chain_compiler` — o
  compilador continua vendo eventos atômicos. A cicatriz que fixou essa regra: uma primeira
  tentativa de derivar posição no compilador fabricou **160 ações fantasma** em 281 lutas;
- falsos positivos fixos e fechados: `shin to shin guard`, `chest-to-chest half guard`,
  `50/50 guard`. Se a lista precisar crescer, o critério está errado, não a lista;
- o split **não cria estado novo**: ele separa uma ação de um estado que o log já nomeou.

---

## 4. As cicatrizes que N0 encontrou e o que fez com cada uma

| Cicatriz | Medida | N0 |
|---|---|---|
| Duas autoridades discordando | 526 eventos, 23 pares (top: `control/Front Headlock` 137, `guard/Guard Pull` 114, `control/Body Lock` 68, `escape/Turtle Position` 58, `control/Escape to Turtle` 46, `control/Collar Tie` 42) | **Fechada.** `attribution` é a primeira fonte. |
| `back take` é variação de `Back Control` na biblioteca, e a ação vira estado | 19 eventos `transition/Back Take` no corpus, 128 `control/Back Take` no dump do dono | **Ponte** `_LIBRARY_VARIANTS_THAT_ARE_ACTIONS`; entrada própria em N1. |
| `Body Triangle (Bottom)` = `controlling` em `attribution.py:199` e "quem está debaixo" em `lamas_chain.py:211-213` | 2 eventos | **Fechada** para `controlled`/quem está debaixo (`_a(STATE, DEFENDS, CONTROLLED, CONTROLLING)`), com o comentário apontando para a outra camada. |
| `_GUARD_BOTTOM`/`_CONTROL_BACK` são listas de ORIENTAÇÃO, e a `category` vinha de carona do tipo | família guard-pull (149 eventos) + `arm drag to back take`/`crab ride to back take` | **Fechada** por `_ACTIONS_FILED_AS_POSITIONS`: corrige só a `category`, **nenhum papel se move**. Puxar guarda deixa você por baixo (por isso a linha pertence a `_GUARD_BOTTOM`) e ainda assim é algo que se faz. |
| `50/50`, `single leg x`, `shin to shin` liam `bottom` na tabela de orientação e `neutral` em `attribution` | 4 rótulos | **Fechada** para `neutral` (decisão 4). O teste que fixava a contradição como backlog agora fixa o conjunto vazio. |
| 9 rótulos com dupla identidade conforme o `type` logado | 11 pela medição própria da auditoria | **NÃO fechada** — ver §6. |
| `graph_nodes` de atleta sem ontologia | 286 `node_key` distintos com `type='technique'` | **NÃO tocada** — N3. |
| `state_orientation.json` cobre 39 rótulos; 52 de 74 rótulos curados leem `neutral` | 53 estados do corpus sem linha, 59 depois de N0 | **NÃO fechada** — N2. |

---

## 5. As fases, e por que N1-N3 exigem replay

| Fase | O que move | Replay? |
|---|---|---|
| **N0** (esta) | quem decide `state` vs `action`; três linhas de orientação; a classe de 15 pares `(type,label)` | **Não.** `node_key` não muda, e o rating não lê classe (§6). |
| **N1** — aliases + biblioteca | `names.SYNONYMS` (`close/closed guard`, `take down/takedown`, `snap down/snapdown`, `near fall/nearfall`, `north south` ×3, `shin on/to shin`, `leg (lock) entanglement`), `back take` e `saddle` fora das variações de `Back Control` | **Sim, completo.** Colapsar um alias FUNDE dois `node_key`; todo `computed_elo`, `graph_edges.elo`, `graphs.user_elo`, `athletes.elo` e `elo_series` chaveado neles precisa ser refeito (`docs/rating_v2/08_ESTADO_DO_CUTOVER.md`), mais re-pin e regeneração do site. Junta-se ao replay já pendente do North South — **UM** replay para tudo. |
| **N2** — compostos e perspectiva | `data/taxonomy/composite_labels.json` novo, expansão na ingestão, `state_orientation.json` 39 → ~85 linhas, prompt do refinador | **Sim.** Um evento composto vira 1-2 eventos: a `sequence` das lutas muda, então tudo derivado dela muda. Opera sobre `matches.sequence` do BANCO, nunca sobre os dumps (cicatriz `dumps-diverged-from-db`). |
| **N3** — grafos de atleta + App | `graph_nodes.type` passa a vir da biblioteca/`attribution` no `replay_and_persist_athlete`; goldens cross-repo byte-idênticos; `corpus_paths` filtra `kind` pela autoridade nova | **Sim** (é o mesmo replay de N1/N2). Sem migração de schema. |

---

## 6. Medido, não estimado

Corpus de prod, somente leitura, 2026-09-03: **10 016 eventos, 742 lutas, 342 pares
`(type,label)`**. Dump privado do dono: 281 lutas, 2 421 eventos.

**O que N0 moveu** — 15 pares `(type,label)`, listados em
`data/taxonomy/audit_baseline.json → reclassified`: 334 eventos do corpus + os 128
`control/Back Take` que só aparecem no dump. Onze viraram `state` (os grips e clinches:
`Front Headlock` 137, `Body Lock` 68, `Collar Tie` 42, `Double Underhooks`, `Hooks In`,
`Two-on-One Control`, `Rear Body Lock`, `Two-on-One Wrist Control`, `Russian Tie`,
`Clinch Knees`) e quatro viraram `action` (`Escape to Turtle` 46, `Back Take` 19+128,
`Jump Guard` 2, `Smother` 1).

**O que N0 não moveu, e é o que torna a mudança barata:**

- `rating_v2.node_rating.observations_for_side` produz multiconjunto **idêntico**, sem exceção
  (`tests/test_ontology_parity.py`). Ele lê o evento CRU por `node_key_of(label)` e nunca
  pergunta a classe. Nenhum ELO se move; nenhum replay é exigido por N0.
- `data/rating/markov_action_weights.json` e todos os goldens de Markov/Glicko: `--check` verde.
  `lamas_state` não foi tocado, então nenhum peso CDP/BTK se moveu.
- `node_key`: intocado. É a âncora de compatibilidade inteira (invariante 1 de `03`).

**Efeito colateral, no dump de 281 lutas:** ocorrências de ação OBSERVADAS 1390 → **1548** e
ações INFERIDAS 433 → **321**. As duas na direção certa: 175 eventos que eram nós agora são
ações reais, e os genéricos que o compilador tinha de inventar em volta deles deixaram de ser
necessários. Os dois números estão fixados em `tests/test_actions_parity.py`.

**A auditoria, antes → depois de N0** (`scripts/audit_ontology.py`):

| família | antes | depois |
|---|---|---|
| `dual_identity` | 11 | **11** |
| `alias_candidates` | 6 | 6 |
| `composites` | 38 | 38 |
| `states_without_orientation` | 53 | **59** |
| `athlete_nodes_typed_technique` | 286 | 286 |

Duas leituras honestas dessa tabela, ambas contra o que o plano previa:

1. **A troca de autoridade NÃO fechou as duplas identidades "de graça".** Fechou três
   (`escape to turtle`, `jump guard`, `smother`) e abriu três (`hooks in`, `russian tie`,
   `twoonone wrist control`). Causa: `attribution._LABEL` e as listas posicionais são chaveadas
   por `(type, label)`, então um grip curado sob `control` não tem linha sob `transition`, onde
   `_FORCED_ACTION_TYPES` continua forçando ação. Fechar isso de verdade exige a classe ser
   chaveada pelo RÓTULO, não pelo par — que é a biblioteca de N1.
2. **`states_without_orientation` cresceu porque N0 funcionou.** Os 10 grips que viraram estado
   não têm linha em `state_orientation.json` (`front headlock`, `collar tie`, `body lock`, …).
   É exatamente o trabalho de N2, e o baseline foi gravado DEPOIS da mudança para que
   `--check` recuse crescimento a partir daqui.

---

## 7. A auditoria como portão

```bash
uv run python -m scripts.audit_ontology            # relatório (banco, ou --dump arquivo.json)
uv run python -m scripts.audit_ontology --check    # falha se qualquer família crescer
```

Baseline em `data/taxonomy/audit_baseline.json`. Cinco famílias, e cada uma é uma pergunta que
uma fase seguinte responde: dupla identidade (N1), candidatos a alias (N1), compostos (N2),
estados sem orientação (N2), `graph_nodes` de atleta sem ontologia (N3). Sem banco,
`athlete_nodes_typed_technique` fica `None` e o baseline é preservado — medir zero por ausência
de banco não é medir zero.

---

## 8. Fora de escopo de N0, por decisão

- **`guard recovery`** lia `state` sob `guard` (28 eventos) e `action` sob `escape` (7), e o
  mesmo `guard recovery` é chave de `inference_table.generic_actions`. Dupla identidade E
  colisão com o vocabulário genérico. **Fechado por N2** (§9.4 abaixo) — o rótulo OBSERVADO
  agora é sempre ação; o genérico de `inference_table.json` (D2, usado só pelo compilador para
  ligar dois estados adjacentes sem ação observada entre eles) continua intocado, questão
  diferente, sem colisão.
- **`orientation_of`** continua prometendo "rótulo não resolvido lê `neutral`, nunca chuta"
  (`03` §8.2). A leitura mais larga da inferência continua em `orientation_for_inference`, com
  `source: declared | derived` dizendo qual nível respondeu.
- **`the-system.html` / "The Data"** listando ações como "posições": consumidor do defeito, não
  causa. N4.
- **App**: nenhum arquivo tocado. Os goldens `taxonomyKindGolden.json` e `chainCompilerGolden.json`
  do lado App ficam DIVERGENTES até a fase N3 portar `taxonomyInference.ts`/`chainCompiler.ts` e
  regenerar com os mesmos bytes; `tests/test_cross_repo_fixtures.py` fica vermelho nesses dois
  até lá, de propósito.

---

## 9. N2 — rótulos compostos e perspectiva (2026-09-04)

Fecha os dois itens que N0 deixou pendurados: rótulos "A to B"/"X / Y" que empacotam um estado E
uma ação num nó só, e a cobertura de `state_orientation.json` (39 → 93 linhas). Nada de
`node_key` se move para os rótulos JÁ atômicos; os compostos decompostos SIM ganham `node_key`
novos (os das partes) — por isso esta fase, como N1, exige um replay (§9.5).

### 9.1 A tabela curada

`data/taxonomy/composite_labels.json`, chaveada por `analysis.names._normalize_name`. Três
formas — nunca regex em produção, nunca heurística no compilador:

```
{"action": "<rótulo>", "to": "<rótulo>" | "top"|"bottom"|"neutral"}
    -> evento de ação, depois um segundo evento para "to" — OMITIDO quando "to" é uma palavra de
    orientação nua ("Escape to Standing" -> neutral): o alvo era vago na fonte, e a âncora de
    saída que o compilador já infere (`taxonomy_kind.resolve_closing_anchor`) responde a mesma
    pergunta para uma ação sem estado declarado a seguir — emitir um nó literal reinventaria
    esse mecanismo.
{"state": "<rótulo>", "action": "<rótulo>"}
    -> evento de estado, depois evento de ação (a posição já existia; dela, um movimento).
{"state": "<rótulo>", "perspective": {"actor": "top"|"bottom"}}
    -> UM evento só. O RÓTULO NÃO MUDA — perspectiva é metadado, nunca um segundo nome de
    estado (a invariante do §1). "Top Half Guard" continua "Top Half Guard"; ganha só o campo
    `perspective`.
```

Curados: 21 pares `{action,to}`, 4 pares `{state,action}` (a família Leg Entanglement), 4
perspectivas (`Top Half Guard`, `Top Control (Half Guard)`, `Body Triangle (Bottom)`,
`Head-Arm Control (Top)`). Deixados de fora, em `_skipped` com o motivo escrito: pares
"Nome / Nome" onde os dois lados são nomes ALTERNATIVOS da mesma finalização (`Armbar / Choi
Bar`, `Katagatame / Darce`, …) — decompô-los duplicaria a ocorrência, não a descreveria melhor
— e um caso (`Pull Guard / Sit Guard`) cujo alvo pode ser o `seated guard` já curado sob outra
grafia, decisão de fusão de N1, não de N2.

### 9.2 Onde a expansão roda

`analysis/composite_labels.py:expand_composite`/`expand_sequence`, chamada de
`db.repository.expand_sequence` em **todos** os pontos que escrevem `matches.sequence`:
`register_match`, `register_matches_bulk` (o caminho de volume — `dump_import.py`) e
`update_match` (edição no admin). Não existe UM ponto único de fato — são três funções de
escrita, cada uma o entry point real do seu caminho (dump, admin paste/edit, scraped import) —
então a garantia de "uma implementação só" vem do helper compartilhado, não de um único call
site. `chain_compiler` nunca vê um rótulo composto: a lição das 160 ações fantasma
(`03_ARESTA_COMO_CAMINHO.md` §3.2) é que inferência de estado pertence à ingestão, nunca ao
compilador.

Cada evento gerado mantém todo campo do original exceto `label`/`type` (`ts`, `actor`/
`actor_id`, `successful`, `points`, …, duplicados nas duas metades quando o composto separa) e
ganha `source_label` = o rótulo bruto original, para auditoria. `type` da metade é um genérico
fixo (`"transition"` para ação, `"control"` para estado) — a classificação real roda depois via
`analysis.taxonomy_kind.kind_of_entry`, que resolve pela biblioteca primeiro e só cai no `type`
bruto como último recurso, então o genérico raramente importa.

### 9.3 `state_orientation.json`: 39 → 93 linhas

Cobre as 53 entradas que `scripts/audit_ontology` media em `states_without_orientation` mais
`straddle` (o alvo novo de `Leg Drag to Straddle`, §9.1). `Body Triangle (Bottom)` = `bottom`
(a leitura de `lamas_chain`, "quem está debaixo" — a mesma resposta que N0 já deu ao PAPEL em
`attribution.py:199`; agora a ORIENTAÇÃO da posição concorda). Incertezas marcadas para revisão
humana: `smash half guard` → `top` (nome de estilo de passagem, não posição parada — lido como
"quem pressiona está em cima", mas o rótulo pode nomear o LADO de baixo em vez da técnica de
quem passa); `kimura trap`/`leg lace`/`half nelson`/`ride out`/`straight jacket` → `top` por
convenção de wrestling (controle aplicado de cima), sem confirmação por transcrição.

Efeito: `scripts/audit_ontology` `states_without_orientation` 53 → 0; `composites` 38 → 11 (cai
exatamente para o conjunto `_skipped`); `dual_identity` 11 → 10 (`guard recovery` fechado, §9.4).
`alias_candidates` (1) e `athlete_nodes_typed_technique` (286) são N1/N3, intocados.

### 9.4 `guard recovery`

Dupla identidade real: `state` sob `guard` (28 eventos), `action` sob `escape` (7). Menor
correção coerente com o contrato — o mesmo padrão já usado para a família guard-pull
(`analysis.attribution._ACTIONS_FILED_AS_POSITIONS`, §4 acima): o rótulo observado é sempre
AÇÃO (é um movimento — recompor uma guarda perdida — não uma postura), só a `category` muda,
nenhum papel (`actor_role`/`target_role`) se move. `RULES_VERSION` de `attribution.py`: 2 → 3.
O genérico `inference_table.json → generic_actions["guard recovery"]` (D2, vocabulário do
compilador para ligar dois ESTADOS adjacentes sem ação observada entre eles) é uma tabela
diferente, uma pergunta diferente, e fica exatamente como estava.

### 9.5 Prompt do refinador

`docs/PROMPT_events_sidecar.md` ganhou duas regras: "um evento = um estado OU uma ação, nunca
'A to B'/'X / Y' num rótulo só" (com o exemplo de decomposição) e "perspectiva de cima/baixo é
metadado do evento, nunca um segundo nome de estado — não inventar 'Top …'/'… (Bottom)'" (fecha
a torneira para os 4 casos de §9.1), mais "registre o estado inicial de cada troca, não só a
ação que a fecha" (o under-registration medido: Back Control sozinho é 44% de todo evento de
estado do corpus, porque a transcrição narra a finalização e nunca a posição que a permitiu).

### 9.6 Reprocesso

Escrita em prod — dono. Runbook: `docs/repairs/2026-09-04_n2_composite_reprocess.md`. Roda
sobre `matches.sequence` do BANCO (nunca sobre os dumps — cicatriz `dumps-diverged-from-db`) e
ANTES do replay já pendente de N1, no MESMO replay (um replay cobre N1+N2, como `03` já previa).
