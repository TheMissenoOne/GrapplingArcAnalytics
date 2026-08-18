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
