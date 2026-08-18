# Estado do cutover — 2026-08-17, fim da sessão

Retomada rápida. O usuário autorizou o **cutover completo** dos dois lados (ADR-13). Isto registra o
que ficou pronto, o que ficou pela metade e o que exige cuidado ao retomar.

## ⚠️ Ao retomar, verifique nesta ordem

1. **`uv run pytest -q` está VERDE?** No fim da sessão havia **2 testes falhando** em
   `tests/test_constellations.py`, causados por uma edição em voo de `analysis/constellations/`
   (agente interrompido). **Não aplique migration nem regenere o site com a suíte vermelha.**
2. **Migration `0036` foi redigida mas NÃO aplicada.** `alembic_version` em produção deve estar em
   `0035`. Confirme antes de qualquer coisa.
3. **O site público está DESATUALIZADO.** Foi gerado antes das correções de identidade da tarde
   (5 vencedores resolvidos, mesclagem Musumeci, 1 luta apagada, 34 atletas replayados). Regenerar é
   obrigatório — hoje o site mostra uma luta que não existe mais.

## Pronto e em produção

- Corpus corrigido: 139 vencedores + 51 empates + 103 anos + 89 eventos (manhã), mais 5 grafias e a
  mesclagem `Musumeci`→`Mikey Musumeci` (tarde). Vencedores de grappling não resolvidos: **6**.
- `athletes` sem auto-luta (era 1).
- Migration `0035` aplicada: `rating_engine_runs` + `athlete_rating_states_v2`, RLS negando por padrão.
- Dois runs V2 persistidos com determinismo provado (`source_hash` idêntico, 0 divergências).
- Site com gate de confiança (RD ≤ 200): 399 breakdowns, 83 dossiês — **mas gerado antes das
  correções da tarde**.
- Core Glicko-2 em TypeScript com paridade provada, **desligado**, no App (`848efb4`).

## Redigido, não aplicado

- **`alembic/versions/0036_*.py`** — `athlete_node_rating_states_v2`, `athlete_constellations_v2`,
  `athlete_constellation_members_v2`. Modelos espelhados em `db/models.py`. Revisei o docstring, a
  idempotência e o `downgrade()`; falta rodar.
- **`analysis/rating_v2/persist.py`** ganhou `persist_node_states` / `persist_constellations`.
  ⚠️ `build_constellation_rows_from_detection` é um **stub que levanta `NotImplementedError`** — é a
  costura para a camada de constelações. Preencher quando `fingerprint`/`stability_p10` existirem.

## Em voo quando a sessão acabou (podem ter morrido pela metade)

- **Constelações completas** (doc 04 do bundle): `fingerprint`, Jaccard **p10**, linhagem entre
  snapshots, taxonomia de nó esparso, e — o item mais importante — **verificar se
  `analysis/transitions/build_graph.py` respeita a fronteira de `sequenceId`**. Se não respeitar,
  transições fantasma entre lutas diferentes entraram em tudo que foi medido até aqui.
- **App: engine V2 do usuário + reprocessamento de sessões** (ADR-12). Escopo: modelo de evidência
  (`round.outcome` NÃO é vitória/derrota), semente por faixa, rating global por eixo, replay das
  sessões quando a versão da engine muda.

## Não começado

- **Contrato de apresentação** — `rating + confiança` ("1420 · alta confiança", "1420 ± 90") no lugar
  de RD cru. **Precondição do cutover público**: sem isso, migrar consumidor expõe incerteza sem
  vocabulário para lê-la.
- **Migração dos consumidores** (dossiê → site `GA_ELO` → export do App), atrás de `engine_version`.
- **UI do App**: radar/insights/share ainda leem a V1.
- **Relatórios de scouting** em `reports/adcc-2026-categoria/` — gerados antes das correções da tarde.

## Cicatrizes desta sessão que valem lembrar

- **`pgrep -f <padrão>` casa com a própria linha do watcher.** Aconteceu 4× e travou um shell por
  horas. Watcher que espera processo tem de excluir a si mesmo ou observar um artefato.
- **Renomear evento invalida `data/rating_v2/disciplines.json`** — o mapa é indexado por nome
  (ADR-10). Regenerar na mesma passada.
- **`scripts.reprocess_all` re-importa os dumps** e sobrescreveria correções feitas direto no banco;
  pior, deduplica por `(participantes, ano)` e 103 anos mudaram. Para replay pós-correção use
  `replay_and_persist_athlete` por atleta.
- **Comparar partições exige o mesmo espaço de chave** — `"Closed Guard"` vs `"closed guard"` deu
  Jaccard 0,0 perfeito em 15 atletas. Zero perfeito é cheiro de artefato, não achado.
- **`Tammi Musumeci` existe** e é pessoa diferente de `Mikey Musumeci`. Mesclagem por sobrenome teria
  fundido as duas.
