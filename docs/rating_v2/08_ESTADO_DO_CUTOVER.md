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

## Falta

- **Migração dos consumidores, um a um** (ADR-02), agora que ADR-14 existe: dossiê → `GA_ELO` do
  site → export do App. **Nenhum foi migrado.** `GA_ELO` ainda é V1. Trocar a fonte do `GA_ELO`
  reordena um ranking público — é decisão de produto, não consequência técnica desta wave.
- **UI do App**: radar/insights/share ainda leem a V1.
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
