# Estado do cutover — atualizado 2026-08-18

Substitui o registro de 2026-08-17 (fim de sessão com dois agentes mortos pela metade). Aquele
documento pedia três verificações antes de qualquer coisa; as três foram feitas e passaram.

## Verificações que o documento anterior exigia

1. **Suíte verde.** Os 2 testes que falhavam em `tests/test_constellations.py` eram edição em voo de
   um agente interrompido — o próprio agente fechou antes de morrer. `uv run pytest -q`: **1137
   passed**.
2. **`alembic_version` em `0035`** — confirmado, e a `0036` foi aplicada. Prod em **`0036`**, com as
   três tabelas novas com RLS **ligada e zero policies** (nega por padrão), verificado no banco.
3. **Site regenerado.** A luta apagada (Musumeci × Musumeci) não existe mais em nenhum lugar do
   bundle.

## A pergunta que estava aberta: fronteira de `sequenceId`

**Respondida, e o comportamento está correto.** `analysis/transitions/build_graph.py` constrói toda
aresta a partir de índices dentro de UM elemento de `sequences` — o laço externo reinicia
`events`/`by_actor` por luta. Travado por `tests/test_transitions.py::test_no_cross_sequence_edge`.
Não houve transição fantasma entre lutas em nada do que foi medido.

## No ar

- **Prod em `0036`.** Tabelas de nó e constelação criadas, sem consumidor (ADR-03/ADR-08 continuam
  valendo: nenhuma das duas camadas tem metodologia fechada).
- **Run V2 novo persistido: `2645cce4-ca61-4756-9433-848baba9e297`**, 646 atletas,
  `input_hash 05301d58`. O run anterior (`210a5ba7`, 639 atletas, hash `8a803053`) lia um corpus que
  **não existe mais** — não reproduz. O site aponta para o novo.
- **Camada de constelações completa** conforme doc 04 do bundle: `fingerprint`, Jaccard média **e
  p10**, linhagem entre snapshots, taxonomia de nó esparso, metadado de reação nas arestas. Rating
  continua fora da formação de comunidade.
- **`build_constellation_rows_from_detection`** preenchido — o stub existia só porque
  `fingerprint`/`stability_p10` ainda não existiam. Existem.
- **ADR-14 — contrato de apresentação** nos dois lados: `analysis/rating_v2/presentation.py` e
  `GrapplingArcApp/src/services/rating/ratingV2Presentation.ts`, com teste de paridade.
- **App: engine V2 do usuário** (`5c10112`) — evidência por entrada própria, `no_attempt` não gera
  evidência nenhuma, semente por faixa, nó novo semeado no rating global corrente, rating global
  derivado dos nós (precisão dentro do eixo, peso igual entre eixos). **Desligada**: nenhuma tela
  consome. 2040 testes verdes, `tsc` limpo.
- **Reprocessamento de sessões** (ADR-12): o estado carrega `engineVersion` **e**
  `sourceFingerprint`. Versão velha **ou** corpus movido (sessão criada, editada, apagada) dispara
  replay da fonte. Checar só a versão deixaria o rating congelado no dia da última troca de versão.
- **Evento CJI 2 desduplicado** — duas grafias do mesmo evento faziam o exportador sobrescrever uma
  página de evento com a outra e 11 lutas sumiam da listagem. Corrigido no banco e no registry dos
  dumps, `disciplines.json` regenerado na mesma passada (ADR-10).

## Consumidores públicos: migrados (wave 10)

Os três — dossiê (`elo_rank`/`elo_percentile`), `elo_pct` do breakdown e o board `GA_ELO` — passam
todos por `analysis/discipline.py:ranked_pools`. É **uma costura só**; escaloná-los exigiria duplicar
o pool. MMA (Elo do UFC) e wrestling (elo do grafo) intocados.

O board publicado ganhou um **piso de lutas** (`MIN_BOARD_BOUTS = 10`) além do portão de RD. RD
sozinho confunde "poucas lutas" com "muitas lutas, inativo": com RD ≤ 200 só, o board sentava atletas
de 3, 4 e 4 lutas em #5-#8 enquanto o #1 tinha 114. Piso vale **só para ranking publicado** — nunca
para página, denominador de percentil ou peso de análise.

Três defeitos que a migração revelou (não causou), todos corrigidos:
- 11 grapplers classificados como MMA e apagados dos dois boards **e do próprio dossiê** (ADR-15);
- `_bout_counts` com SQL cru não casava chave nenhuma — `Athlete.id` passa por type decorator;
- o "#N Grappling ELO" do dossiê era rank dentro da categoria de peso, e `weight_class` é NULL em
  883 de 1327 atletas (e código opaco no resto). Três atletas exibiam "#1" ao mesmo tempo. Agora é o
  rank do pool inteiro, igual ao board — o percentil ao lado já era geral.

## Falta

- **UI do App**: radar/insights/share ainda leem a V1 (`graphSlice.userElo`, `node.computedElo`).
- **Board de wrestling mostra 100% nas oito linhas** — pré-existente, não é da migração: os ratings
  são quase planos (807,5 repetido) e 807,5/811,0 arredonda para 100%. Um ranking onde toda linha diz
  o mesmo não informa nada. Corrigir é decisão de apresentação (mais casas? só posição? esconder o
  board?), não de rating.
- **Calibração dos parâmetros de usuário** (ADR-13): peso 0,10 por tentativa, 70 Elo por grau de
  dificuldade, RD 220, RD 350 de nó novo. Todos candidatos não medidos, num bloco só. Critério para
  recalibrar: ADR-03 (log loss fora da amostra antes de spread).
- **ADR-08** continua aberto: fechar exige proveniência por luta em `graph_edges`.

## Cicatrizes que valem lembrar

- **`pgrep -f <padrão>` casa com a própria linha do watcher.** Travou um shell por horas.
- **Renomear evento invalida `data/rating_v2/disciplines.json`** — mapa indexado por nome (ADR-10).
  Regenerar na mesma passada, sempre.
- **`scripts.reprocess_all` re-importa os dumps** e sobrescreveria correções feitas direto no banco.
  Para replay pós-correção, `replay_and_persist_athlete` por atleta.
- **Comparar partições exige o mesmo espaço de chave** — `"Closed Guard"` vs `"closed guard"` deu
  Jaccard 0,0 perfeito em 15 atletas. Zero perfeito é cheiro de artefato.
- **`Tammi Musumeci` é pessoa diferente de `Mikey Musumeci`.** Mesclagem por sobrenome fundiria as duas.
- **`as any` num fixture de teste esconde defeito de tipo real** — dois fixtures do App passavam
  `RoundEntry` sem `assoc`; o cast é que estava errado, não o tipo.
- **`site/grapple-like.html` casa com o glob `grapple-*.html`.** Contar arquivos por glob dá 85
  quando há 84 dossiês; não é órfão.
- **SQL cru (`text()`) contorna o type decorator de `Athlete.id`.** As chaves voltam noutra forma e
  toda busca erra em silêncio — sem exceção, sem linha faltando, só um dicionário que nunca casa.
  Aconteceu duas vezes na mesma tarde: no código e no meu próprio probe de diagnóstico, que quase me
  fez reportar um defeito inexistente. Para juntar com resultado de ORM, consulte pelo ORM.
- **Defeito latente só aparece quando o dado volta.** O "#1 Grappling ELO" triplicado existia havia
  tempo; era invisível porque um único atleta exibia o número, e ele era #1 de verdade. Consertar o
  mapa de disciplina devolveu 11 ranks e a contradição ficou visível na mesma hora. Ao restaurar
  dado que estava sumindo, revise o que passa a exibi-lo.

---

## Fase 7 — estado em 2026-08-19

| Item | Estado |
|---|---|
| Cutover da UI (App) | **FEITO.** O replay V2 produz `GraphNode.data.computedElo` nos dois produtores (`sessionSaveService`, `reprocessService.replaySessionHistory`); ~70 leitores migraram sem mudança de call site. Churn medido e travado em `ratingV2Churn.test.ts` |
| Rótulo de confiança | **NÃO exposto, por decisão.** Bandas do ADR-14 intactas (100/200); nada no site nem no app desenha o nível. Com a distribuição de RD medida, `alta` classifica 1 atleta em 646 |
| Tabelas do `0036` | **Esquema reservado, e agora com guarda.** ADR-03 mantém a camada de nó em sombra: critério 2 sem dado (nenhum nó com RD < 150 em 34 das 36 células), mediana de 1 luta por nó. `tests/test_rating_v2_node_layer_shadow.py` falha se um produtor for ligado sem reabrir o ADR |
| Scouting ADCC 2026 | **Já estava atual.** Regenerado em 2026-08-19: byte-a-byte idêntico ao commitado, timestamp incluído (o relatório é determinístico). A premissa de que era anterior à desduplicação do CJI 2 estava errada. `--out` passou a ser diretório — antes era prefixo, e o comando documentado escrevia ao LADO da pasta dos artefatos |
| ADR-08 | **REABERTO, e continua.** Fecha só com proveniência por luta em `graph_edges` (condição a). A condição (b) sozinha é medição sem rigor — ver a nota de 2026-08-19 no ADR |
| Dívida de mypy (issue #4) | **8 dos 16 módulos não-teste aposentados** com anotação/narrowing de verdade, sem relaxar strict. Restam 8, com 4-8 erros cada |

---

## Fase 8 — nota por nó em Glicko-2 nos dois lados (ADR-16, 2026-08-26)

**Decisão do dono, não medição.** As duas condições de reabertura do ADR-03 continuam não
satisfeitas (mediana de 1 observação por nó, zero nós com RD < 150). Quadro medido completo no
ADR-16.

**Âncora fechada por medição, em duas etapas.** A primeira implementação pontuava o nó contra o
global do OPONENTE e mediu `corr(rating do atleta, deslocamento dos nós) = −0,790`: toda técnica
de um atleta dominante lia abaixo do próprio nível. Trocar para o global do PRÓPRIO atleta quase
não mexeu (−0,763) — decompondo, o resto era a SEMENTE ENVELHECENDO (−0,855) e não a evidência
(+0,109). Re-ancorar no global final, preservando o termo de evidência, fecha em **+0,109**.
Detalhe e a lição de método no ADR-16.

### O que mudou

| Lado | Antes | Agora |
|---|---|---|
| Analytics, `computed_elo` do nó | divisão do delta V1 por `athlete_elo._node_shares` | Glicko-2 por nó, `analysis/rating_v2/node_rating.py`, projetado no PRODUTOR |
| Analytics, `graphs.user_elo` / `athletes.elo` | média dos nós V1 (~800) | rating global V2 do atleta (~1750) — o MESMO número que o board publica |
| Analytics, `athletes.elo_series` | trajetória V1 por luta | rating global V2 ao fim do período de cada luta (ADR-02: uma escala por linha) |
| Analytics, baseline populacional | todos os grafos de atleta | só `rated_athlete_graph_ids` (evita misturar escala V2 e V1 num `node_key`) |
| App, peso da tentativa | `ATTEMPT_WEIGHT` plano | `ATTEMPT_WEIGHT × share Markov × n` (bloco `global`, média 1) |
| App, `RATING_V2_ENGINE_VERSION` | `glicko2-user-v1` | `glicko2-user-v2-markov` — ADR-12 dispara replay das sessões sozinho |

Atleta sem estado no run V2 (MMA/wrestling pelo ADR-05, ou toda luta excluída pelo ADR-06)
**fica na V1**: não há o que projetar, e meio grafo projetado é o único estado que não pode ser
persistido.

### Runbook do replay completo — NÃO rodado ainda

Ordem, e o porquê de cada passo. Tudo daqui em diante escreve em produção.

```bash
cd GrapplingArcAnalytics
# 0. Confirme que o corpus não se moveu desde o pin. Se estes dois hashes diferirem,
#    o passo 2 já vai avisar — mas é melhor saber antes de escrever nada.
uv run python -m analysis.rating_v2.replay            # imprime coverage/summary, NÃO persiste

# 1. Se o corpus se moveu: rode o replay global e re-fixe SITE_RATING_RUN_ID.
#    Sem isso, `athletes.elo` (escala V2 nova) discorda do Grappling ELO publicado.
uv run python -m analysis.rating_v2.replay --persist  # -> run_id novo
#    edite analysis/rating_v2/config.py: SITE_RATING_RUN_ID = "<run_id>" + a nota do pin

# 2. Replay por atleta, do jeito que este repo já faz (NUNCA `scripts.reprocess_all`:
#    ele reimporta os dumps e ressuscita os fantasmas da AA-011).
uv run python -m scripts.backfill_edge_bouts --dry-run   # confere contagem, não commita
uv run python -m scripts.backfill_edge_bouts             # ~1300 atletas, SAVEPOINT por atleta

# 3. Baselines derivadas do computed_elo, na ordem em que dependem umas das outras.
uv run python -m analysis.archetype                      # ou o entrypoint do pipeline
uv run python -m scripts.assign_user_archetypes
uv run python -m export.ontology

# 4. Site inteiro (o bundle é gerado, nunca editado à mão).
uv run python -m export.site_data --full                 # ~10-12min, é N+1 conhecido
#    commit + push do repo GrapplingArc (main) — GitHub Pages publica no push
```

**Validação depois, antes de commitar o site:**
- `athletes.elo` de um atleta conhecido == o rating dele em `athlete_rating_states_v2` no run
  fixado (é o mesmo número por construção agora; se divergir, o pin está velho);
- nenhum WARNING de `rating_v2 node replay reads corpus … but SITE_RATING_RUN_ID is pinned to …`
  no log do passo 2;
- `graph_edges.elo` de um grafo projetado na faixa ~1400-2300, não ~800;
- board público e dossiê continuam mostrando **relativo + % + "Grappling ELO"**, nunca rating cru.

**Reversão:** não há migração de schema para desfazer. `git revert` do commit + re-rodar o
passo 2 devolve os números V1, porque o replay é determinístico a partir de `matches`.

### O que ficou de fora, de propósito

- **Persistir estado de nó em `athlete_node_rating_states_v2` (0036).** As tabelas seguem
  reservadas e sem produtor; `tests/test_rating_v2_node_layer_shadow.py` continua verde e
  continua sendo o guarda. O consumidor desta camada é `computed_elo`, e um segundo armazém sem
  leitor é dívida. Caminho de upgrade: `persist_node_states` já existe e recebe exatamente a
  forma que `NodeRating` produz.
- **`analysis/rating_v2/node_replay.py` / `node_periods.py`.** São a evidência congelada do
  ADR-03 (modelo pontuado pela LUTA). Reapontá-los para este modelo invalidaria em silêncio o
  sweep que o ADR cita.
- **Scripts de manutenção que replayam em laço** (`merge_attempt_nodes`, `merge_technique_dups`,
  `date_reconcile`, `repair_actor_ids`, os `insert_*`) não passam `node_ratings`, então pagam uma
  varredura de corpus (~7s) por atleta. Correto, só lento. `replay_participants` e
  `backfill_edge_bouts` já constroem uma vez e passam adiante.
