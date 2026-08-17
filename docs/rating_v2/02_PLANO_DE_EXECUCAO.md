# Plano de execução — waves adaptadas ao repo

Regra do workspace: **nunca um PR cruzando módulos**. Waves 0–7 são PRs em GrapplingArcAnalytics;
wave 9 é PR em GrapplingArcApp, referenciando o PR do Analytics.

Cada wave tem critério de aceitação **mensurável**. Wave sem número medido não fecha.

---

## Wave 0 — congelar contratos (só documentação)

**Entra:** `docs/rating_v2/` (este conjunto) + `analysis/rating_v2/config.py` com os parâmetros
versionados como dados, não como constantes espalhadas.

**Não entra:** nenhuma linha de matemática, nenhuma tabela.

**Aceitação:** `engine_version` presente no config; todo parâmetro do
`engine_config_candidate.json` tem correspondente ou justificativa escrita para a divergência.

---

## Wave 1 — P0 de dados: vencedor ausente e disciplina

**Esta wave vem antes da engine.** É a que mais melhora o rating por unidade de esforço.

**Entra:**
1. Distinguir os três estados de `winner_id IS NULL` (empate real / desconhecido / no-contest). Hoje
   são 283 lutas, das quais **271 são `DECISION` sem vencedor** e só 3 são `DRAW`.
2. Classificação de disciplina por luta (`submission_grappling` / `mma` / `unknown`), revisada por
   humano onde o nome do evento for ambíguo. Piso medido: 34 lutas casam `ufc|mma|bellator|one champ|boxing`.
3. Dívida declarada: `matches.event_date DATE NULL`, preenchida daí em diante (ADR-04).

**Aceitação mensurável:**
- lutas com `winner_id IS NULL AND win_type='DECISION'` cai de 271 para **0** (resolvidas ou
  marcadas explicitamente como desconhecidas);
- toda luta tem disciplina não-nula ou está explicitamente em `unknown`;
- nenhum número de rating muda nesta wave (a V1 não lê esses campos novos).

---

## Wave 2 — core Glicko-2 puro

**Entra:** `analysis/rating_v2/{glicko2,models,periods}.py`. Zero dependência de DB ou arquivo.

**Aceitação:** reproduz o exemplo publicado — rating 1464.06 / RD 151.52 / vol 0.059996 dentro de
tolerância. **Já verificado que o core do bundle passa** (obtido: 1464.05 / 151.52 / 0.059996), então
o risco aqui é de porte, não de matemática.

---

## Wave 3 — replay global de sombra

**Entra:** `analysis/rating_v2/replay.py`, saída para artefato (JSON/Parquet), **sem escrever no
banco**.

**Regras:** todos começam em 1750 (ou no prior de `rank_elo` quando existir — ADR-01); períodos
anuais; estados pré-período; só disciplina `submission_grappling`; decisões sem vencedor fora.

**Aceitação:**
- rodar duas vezes com a mesma entrada produz hash idêntico;
- embaralhar a ordem de importação dentro do período não muda a saída;
- comparar com o replay de referência já medido (Gordon Ryan no topo com RD ~57 em 114 lutas) e
  explicar qualquer divergência;
- publicar a distribuição: hoje, **64,6% dos atletas têm 1 luta** e **66% terminam com RD ≥ 200**.
  Se esses números não melhorarem depois da wave 1, isso vai escrito no relatório em vez de escondido.

---

## Wave 4 — camada topológica compartilhada

**Entra:** `analysis/transitions/{build_graph,normalize}.py` e
`analysis/constellations/{detect,stability,compare}.py`.

`detect.py` implementa o gate de conectividade do ADR-07. Rating **não** entra em nenhuma assinatura
desta camada — se `detect()` aceitar rating como argumento, o PR está errado.

**Aceitação:**
- partição idêntica entre execuções (semente fixa, sem depender de ordem de dict);
- alterar apenas ratings não muda nenhuma partição (teste explícito);
- taxa de comunidades rejeitadas por desconexão reportada;
- varredura de resolução 0.8 / 1.0 / 1.2 / 1.4 com share do maior cluster e Jaccard de bootstrap.

---

## Wave 5 — replay de nó em sombra + sweep de peso

**Entra:** observação de nó (uma por nó único por atleta por luta), sweep 0.10 / 0.25 / 0.50 / 1.00 e
tau 0.3 / 0.5 / 0.8.

**Aceitação:** critério do ADR-03 aplicado **na ordem declarada**, com o resultado publicado mesmo se
for "nenhum peso melhora a predição" — que é um desfecho plausível com mediana de 1 luta por atleta.

---

## Wave 6 — comparação de detectores

**Entra:** relatório comparando `athlete_systems.py` com o detector compartilhado (ADR-08): Jaccard
entre partições, estabilidade sob bootstrap, cobertura de atletas.

**Aceitação:** o detector compartilhado só é declarado sucessor se ganhar em estabilidade sem perder
cobertura. Caso contrário, coexistem e o motivo fica escrito.

---

## Wave 7 — persistência V2 em paralelo

**Entra:** migration alembic a partir do head `0034`, criando as tabelas do bundle
(`rating_engine_runs`, `athlete_rating_states_v2`, `athlete_node_rating_states_v2`,
`athlete_constellations_v2`, `athlete_constellation_members_v2`). **Nenhuma coluna existente muda.**

**Aceitação:** replay a seco → persiste um run marcado → rerodar a mesma entrada dá resultado
idêntico → relatório V1 × V2 lado a lado. RLS conforme o padrão do repo; tabelas são de atleta
(público), e nada de estado derivado de usuário entra nelas.

---

## Wave 8 — consumidores, um de cada vez

Ranking, dossiê, exports de grafo, arquétipos e comparação de sistemas **não** mudam no mesmo commit.
Cada consumidor migra atrás de `engine_version` explícita.

---

## Wave 9 — porte TypeScript (PR no App)

Só depois do Analytics estável. Exporta fixtures douradas (entrada → rating/RD/volatilidade/partição)
e assere paridade Python↔TypeScript antes de qualquer replay de usuário.

**Atenção de contrato:** a normalização de `node_key` tem de casar char-for-char com
`normalizeLabel()` em `GrapplingArcApp/src/services/graphSync.ts` ↔ `analysis/names.py:_normalize_name`.
Uma engine com normalização própria quebra o sync do App.

---

## O que NÃO entra em nenhuma wave

- Rating dentro do relatório de metagame/categoria (invariante de arquitetura, ver `README.md`).
- Sobrescrever `athletes.elo`, `graphs.user_elo` ou `AthleteNode.computed_elo` antes do cutover.
- Estado de rating derivado de dados de usuário em tabela pública de atleta.
- Frequência de treino como multiplicador de rating — frequência gera evidência, não pontos.
