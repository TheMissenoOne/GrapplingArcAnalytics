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

- **`guard recovery`** lê `state` sob `guard` (28 eventos) e `action` sob `escape` (7), e o
  mesmo `guard recovery` é chave de `inference_table.generic_actions`. É dupla identidade E
  colisão com o vocabulário genérico. Mexer nele reclassifica 35 eventos e colide com um
  genérico — N1/N2, com a biblioteca junto.
- **`orientation_of`** continua prometendo "rótulo não resolvido lê `neutral`, nunca chuta"
  (`03` §8.2). A leitura mais larga da inferência continua em `orientation_for_inference`, com
  `source: declared | derived` dizendo qual nível respondeu.
- **`the-system.html` / "The Data"** listando ações como "posições": consumidor do defeito, não
  causa. N4.
- **App**: nenhum arquivo tocado. Os goldens `taxonomyKindGolden.json` e `chainCompilerGolden.json`
  do lado App ficam DIVERGENTES até a fase N3 portar `taxonomyInference.ts`/`chainCompiler.ts` e
  regenerar com os mesmos bytes; `tests/test_cross_repo_fixtures.py` fica vermelho nesses dois
  até lá, de propósito.
