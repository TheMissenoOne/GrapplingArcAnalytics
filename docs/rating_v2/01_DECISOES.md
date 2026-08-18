# ADR — decisões da Rating Engine V2

Cada decisão: contexto, opções, escolha, consequência. Onde há recomendação, ela é explícita — não
"depende".

---

## ADR-01 — O `rank_target` morre como atrator, sobrevive como validação externa

**Contexto.** A V1 calcula `K = base(n) × gap_factor × 2.5 × decay`, e `gap_factor` cresce com
`|rank_target − graph_elo|`. `rank_target` é `athletes.rank_elo`, o alvo derivado do leaderboard
ADCC. Ou seja: hoje o rating é parcialmente *calibrado contra uma tabela externa* — quanto mais longe
do alvo, mais forte o empurrão. Isso garante ordenação plausível mesmo com pouca evidência, ao custo
de o número não ser mais uma medida do que aconteceu nas lutas.

**Opções.**
1. Manter a convergência na V2 (Glicko-2 com termo de atração ao alvo).
2. Matar a convergência; rating puramente de resultado.
3. Híbrido: `rank_elo` vira **prior de seed por atleta** (o atleta começa no alvo, com RD alto) em
   vez de atrator permanente.

**Escolha: (2) — convergência morta, seed uniforme em 1750.**

> **Revisão em 2026-08-17, por medição.** A escolha original deste ADR era a (3) — `rank_elo` como
> prior de seed. **O replay de sombra refutou.** `rank_elo` está na escala da V1 (preto = 800):
> Gordon Ryan 1343, Kade Ruotolo 1104, Felipe Pena 1138, Ffion Davies 1085, Adele Fornarino 978.
> Semear com esses valores enquanto todo o resto parte de 1750 **pune justamente os atletas com a
> melhor evidência externa** — eles começam 400 a 800 pontos abaixo de um desconhecido.
>
> Resultado medido: no modo `rank_elo`, Kade Ruotolo, Felipe Pena, Ffion Davies, Kaynan Duarte e
> Nick Rodriguez **saem** do top-12, substituídos por atletas de baixa evidência que receberam o
> seed de 1750 por não terem `rank_elo`. No modo flat, o top-12 é reconhecivelmente a elite do
> corpus (Gordon Ryan, Helena Crevar, Kade Ruotolo, Felipe Pena, Ffion Davies, Kaynan Duarte…).
>
> O erro não foi usar prior — foi misturar duas escalas incomparáveis. Para reabilitar a opção (3)
> seria preciso reescalar `rank_elo` para a escala V2 antes de usá-lo, e essa reescala teria de
> **provar** que melhora a predição out-of-sample pelo critério do ADR-03. Enquanto não provar, não
> existe: seed uniforme.

**Por quê.** A opção 1 destrói a propriedade que motiva a troca: com um termo de atração, o RD deixa
de significar "quanta evidência eu tenho" e passa a significar "quão perto do alvo eu fui empurrado".
A opção 2 pura joga fora informação legítima — o leaderboard ADCC é evidência externa real sobre
força relativa, e o Glicko tem um lugar canônico para evidência anterior à observação: o **prior**.
Usar `rank_elo` como seed com RD alto é exatamente isso, e é auto-corrigível: se as lutas
discordarem do leaderboard, as lutas vencem, porque o prior tem RD alto.

**Consequência.** A ordenação muda. `export/adcc_elo_table.py` passa a exportar uma escala diferente,
e o número que o App recebe em `@grapplingarch:elo_stats` deixa de ser comparável com o histórico.
Ver `04_CONTRATOS_E_RISCOS.md`. O leaderboard ADCC continua sendo insumo, mas vira **auditoria**: se
a V2 ordenar de forma muito diferente dele para atletas com muitas lutas, isso é sinal de defeito na
V2 — não de sucesso.

---

## ADR-02 — Duas escalas convivem durante o shadow, e nunca no mesmo widget

**Contexto.** A V1 usa `BASE_BLACKBELT_ELO=800` e bases por faixa. O bundle propõe branca 1000 → preta
1750, atleta competitivo 1750. Um preto na V1 começa em 800; na V2, em 1750. Durante o shadow as duas
existem ao mesmo tempo.

**Escolha.** Toda leitura de rating carrega `engine_version` obrigatoriamente, e nenhuma superfície
mistura as duas. Onde a V2 aparecer antes do cutover, aparece rotulada como shadow.

**Por quê.** O risco real não é confundir 800 com 1750 — é confundir *deltas*. "Subiu 40 pontos"
significa coisas diferentes nas duas escalas. E a regra de produto ajuda: como a apresentação é
sempre **relativa + %**, nunca rating bruto, o usuário final nunca vê a escala. O risco é interno,
de quem lê dashboard e CSV.

**Consequência.** `rating_engine_runs.engine_version` não é enfeite de auditoria: é chave de leitura.
Qualquer query de rating sem `run_id` explícito é defeito.

---

## ADR-03 — O peso da evidência de nó é decidido por log loss, não por spread

**Contexto.** O bundle propõe varrer o peso da observação de nó em 0.10 / 0.25 / 0.50 / 1.00 e diz,
corretamente, "o objetivo não é maximizar spread". Mas não define o critério de aceitação, e um sweep
sem critério pré-registrado vira escolha estética depois do fato.

**Escolha — critério definido ANTES do sweep, nesta ordem de desempate:**

1. **Log loss preditivo out-of-sample** sobre lutas futuras: split temporal (treina até o ano *t*,
   prediz *t+1*), predição feita a partir do rating global. O peso de nó vence se melhorar a
   predição ou, no mínimo, não piorar.
2. **Calibração da incerteza**: entre os nós com RD baixo, a taxa de acerto observada tem de bater
   com a prevista. Um peso que produz RD baixo sem acerto correspondente é excesso de confiança.
3. **Estabilidade de ranking** sob bootstrap: correlação de Spearman entre reamostragens.
4. **Fração de nós que continua essencialmente no prior** — se quase todo nó fica no prior, o peso é
   pequeno demais para o corpus atual.

Spread de rating **não** é critério, em nenhuma posição.

**Consequência.** Com mediana de 1 luta por atleta, existe uma chance real de o critério 1 dizer
"nenhum peso melhora a predição". Esse resultado é aceitável e deve ser publicado como tal: significa
que o rating por nó ainda não é sustentado pelo corpus, e a camada de nó fica em shadow por mais
tempo. Não invente evidência para justificar a feature.

> **Medido na wave 5, em 2026-08-17. Sweep 4×3×3 = 36 combinações, split temporal treina ≤2024 /
> prediz 2025 (191 lutas de holdout).**
>
> | Critério | Resultado |
> |---|---|
> | 1. Log loss OOS | baseline 0,6920 → **todas as 36 melhoram** (0,6780–0,6879, ~2% relativo). Melhor: `peso 0.50 / rd0 220` (0,6780), colado com `peso 1.00 / rd0 220` (0,6785) |
> | 2. Calibração da incerteza | **não testável em 34 das 36 células** — nenhum nó atinge RD < 150. A única com amostra tem n=6 |
> | 3. Estabilidade (Spearman, 20 bootstraps) | 0,71–0,86; `peso 1.00 / rd0 220` = 0,847 |
> | 4. Fração ainda no prior | 4–25%; **mediana de 1 luta observada por nó em TODAS as combinações** |
>
> **Não foi o "nenhum peso melhora" do cenário pessimista** — a melhora é real, pequena e consistente
> em toda a grade. Mas também não houve vencedor: a grade melhora quase igualmente, e o critério que
> deveria desempatar não tem dado. O candidato menos mal (`peso 1.00 / rd0 220`) repousa em **seis**
> observações de calibração, o que não é base para fixar parâmetro de produção.
>
> **`tau` é não-identificável neste corpus**: 0,3 / 0,5 / 0,8 dão resultado idêntico até a 4ª casa
> decimal nas 36 linhas. Com 1 luta por nó, a suavização de volatilidade nunca é exercitada.
> **Não varra tau de novo até o corpus crescer** — é computação sem informação.
>
> **Decisão: a camada de nó fica em sombra.** Reabrir quando a mediana de lutas por nó passar de 1 e
> houver nós com RD < 150 em número suficiente para testar calibração de verdade.

---

## ADR-11 — A combinação global+nó é uma escolha nossa, não do bundle

**Contexto.** Para medir o critério 1 do ADR-03 é preciso transformar (rating global + ratings de nó)
em uma única probabilidade de vitória. O bundle descreve o fluxo de evidência do nó, mas **não**
especifica essa fórmula de combinação. A wave 5 precisou formalizar uma.

**Escolha.** Combinação ponderada por precisão (`1/RD²`) entre o rating global do atleta e os ratings
dos seus nós conhecidos, todos na mesma escala — o que é coerente, já que um nó nasce do rating global
do atleta no momento em que aparece.

**Por quê.** É o mesmo idioma que o bundle já usa para agregar eixos do usuário (`1/RD²`), então não
introduz um segundo conceito de combinação no produto. E precisão inversa da variância é a
combinação ótima para estimativas independentes — a premissa de independência é frágil aqui (os nós
de um atleta são correlacionados), o que é mais um motivo para tratar o resultado como shadow.

**Consequência.** Documentado no docstring de `_blend()` como decisão própria. Trocar a fórmula é
decisão de produto, não correção de defeito — e exige re-rodar o sweep, porque muda o critério 1.

---

## ADR-04 — Período de rating = ano, com data por luta como dívida declarada

**Contexto.** O bundle recomenda período = dia de evento. Medido: não existe coluna de data em
`matches`; só `year` (894/894 preenchido) e `created_at` (data de importação).

**Escolha.** Período = **ano** na V2 inicial. Adicionar `matches.event_date DATE NULL` como dívida
explícita, preenchida daí em diante pelo pipeline de ingestão; o período migra para dia de evento
quando a cobertura de data for suficiente — e essa migração exige nova `engine_version` e replay
completo, porque muda a saída.

**Por quê.** Ano é grosseiro mas honesto e determinístico. A alternativa — inferir data do nome do
evento — introduz uma heurística silenciosa exatamente no eixo que define a ordem do replay, que é
onde erro é mais caro.

**Consequência.** Um ADCC inteiro é um período: todas as lutas do torneio usam o estado de início de
ano. Isso *subestima* a evolução dentro do torneio e é conservador — direção certa para errar.

---

## ADR-05 — Rating por disciplina, não rating único

**Contexto.** O replay de sombra colocou Georges St-Pierre e Khabib Nurmagomedov em 2º e 3º no rating
conservador do corpus de grappling. São atletas de MMA; o corpus tem inserts de UFC/MMA e existe um
`analysis/ufc_elo_engine.py` separado. O bundle propõe um rating global por atleta e não menciona
disciplina.

**Escolha.** O estado global do Glicko-2 é **por (atleta, disciplina)**, com disciplina derivada do
evento e persistida na luta — não inferida na hora da leitura. Disciplinas iniciais: `submission_grappling`
e `mma`. O rating de grappling só consome lutas de grappling.

**Por quê.** Glicko pressupõe que os resultados medem a mesma habilidade latente e que os adversários
são comparáveis. Vitória no MMA e vitória no grappling não são a mesma habilidade; misturá-las não
produz um rating "mais completo", produz um rating de nada. E a evidência já apareceu na primeira
medição — não é hipótese.

**Consequência.** Precisa de um campo de disciplina (ou um mapeamento evento→disciplina versionado)
antes da wave 2. As 34 lutas identificáveis por nome de evento são o piso, não o total: a
classificação tem de ser revisada por humano, e o que ficar ambíguo entra como `unknown` e fica fora
do replay de grappling.

---

## ADR-06 — Vencedor ausente é `unknown`, não empate

**Contexto.** 283 de 894 lutas (31,7%) têm `winner_id` NULL. Destas, **271 têm `win_type='DECISION'`**
e apenas 3 são `DRAW`. Ou seja: quase um terço do corpus é "a luta teve vencedor por decisão e nós
não gravamos quem".

**Escolha.** Três estados semânticos separados, e nenhum deles é inferido:
- **empate real** (`DRAW` explícito) → score 0.5, entra no replay;
- **desconhecido** (`DECISION` sem vencedor) → **fora do replay**, contabilizado como cobertura
  perdida;
- **no-contest** → fora do replay.

Enquanto o campo não distinguir os três, `winner_id IS NULL AND win_type='DECISION'` é tratado como
desconhecido.

**Por quê.** Tratar 271 decisões como empate injeta 542 observações falsas de 0.5 — é a explicação
mais provável para a mediana do replay ter caído exatamente sobre o seed. Empate fabricado não é
neutro: ele *ancora* o atleta no prior e fabrica confiança (reduz RD) sem evidência.

**Consequência.** Corrigir esses 271 registros é **P0 da wave 2** e vale mais para a qualidade do
rating do que qualquer ajuste de tau ou de peso de nó. É trabalho de dado, não de engine.

---

## ADR-07 — Leiden em vez de Louvain, ou Louvain com gate de conectividade

**Contexto.** O bundle propõe `nx.community.louvain_communities` com semente fixa, e o próprio doc de
validação lista como failure gate "um megacluster contém a maior parte do grafo de um atleta rico".
Traag, Waltman & van Eck (2019), *From Louvain to Leiden: guaranteeing well-connected communities*,
mostram que o Louvain pode produzir comunidades **internamente desconectadas** — o defeito exato que
o gate procura.

**Escolha.** Preferir **Leiden** quando houver implementação disponível sem dependência pesada; caso
contrário, manter Louvain com semente fixa **mais um teste explícito de conectividade** por
comunidade detectada (toda comunidade tem de ser conexa no subgrafo induzido), rejeitando ou
quebrando as que falharem.

**Por quê.** O bundle propõe checar o sintoma (tamanho do megacluster) sem citar a causa conhecida.
Adicionar Leiden é dependência nova; adicionar o teste de conectividade é ~10 linhas sobre o que já
existe. A escada manda tentar o barato primeiro: **implemente o teste de conectividade, meça quantas
comunidades falham, e só troque para Leiden se o número for material.**

**Consequência.** `analysis/constellations/detect.py` expõe o detector com o gate embutido, e
`stability.py` reporta a taxa de rejeição por conectividade como métrica de primeira classe.

> **Medido na wave 4, em 2026-08-17: taxa de rejeição = 0.**
> O gate rodou sobre os cinco atletas com mais lutas (Gordon Ryan 114, Craig Jones 31, Leandro Lo 25,
> Kade Ruotolo 18, Nick Rodriguez 15) e sobre as duas divisões do roster ADCC 2026, nas resoluções
> 0.8 / 1.0 / 1.2 / 1.4. **Nenhuma comunidade internamente desconexa foi produzida em nenhum caso.**
>
> Portanto **Leiden não se justifica hoje**, e a decisão é fechada: fica Louvain + gate. O corpus é
> denso, com vocabulário pequeno de posições e arestas muito sobrepostas — condição em que o defeito
> que Traag et al. descrevem simplesmente não se manifesta. O gate permanece no código como sentinela
> barata: se a taxa subir quando o corpus crescer ou ficar mais esparso, o número reabre a decisão
> sozinho.
>
> Na mesma varredura: **resolução 1.0 confirmada como default**, por modularidade praticamente plana
> entre 0.8 e 1.4 (variação ≤ 0.06 dentro de cada entidade) e Jaccard de bootstrap sem melhora
> monotônica. Nenhum failure gate do plano de validação disparou — sem megacluster (maior
> `top_share` por atleta = 0.57, num grafo de 14 nós) e sem inversão de partição sob bootstrap
> (pior Jaccard médio = 0.42).
>
> Constante ainda não calibrada: `classify_stability(stability_threshold=0.7)` é primeiro corte,
> documentado como tal no próprio docstring. Calibrar exige mais corpus que o de hoje.

---

## ADR-08 — Constelações substituem `athlete_systems.py` por migração medida, não por decreto

**Contexto.** `analysis/athlete_systems.py` (582 linhas) já detecta "sistemas" do atleta; os
arquétipos KMeans (`analysis/archetype.py`) agrupam atletas por vetor de grafo. A proposta de
constelação sobrepõe parcialmente o primeiro.

**Escolha.** Coexistência temporária com **critério de comparação definido**: rodar os dois
detectores sobre os mesmos atletas e reportar (a) Jaccard entre as partições, (b) estabilidade sob
bootstrap de cada um, (c) quantos atletas cada um deixa sem nenhuma estrutura detectada. O detector
compartilhado só substitui `athlete_systems` quando ganhar em (b) e não perder em (c).

**Por quê.** Substituir por decreto um módulo de 582 linhas que já alimenta produto é como o repo
aprendeu a não fazer. E arquétipo **não** compete com constelação: arquétipo agrupa *atletas*,
constelação agrupa *nós dentro do jogo de um atleta*. São eixos diferentes e ambos continuam.

**Consequência.** Uma wave dedicada só à comparação de detectores, com relatório publicado, antes de
qualquer remoção.

> **Medido na wave 6, em 2026-08-17: NÃO substituir. Coexistir.**
> Relatório completo em [`05_COMPARACAO_DETECTORES.md`](05_COMPARACAO_DETECTORES.md). Os dois
> detectores receberam o **mesmo** grafo derivado de sequências de luta, para isolar o algoritmo como
> única variável. Resultado contra o critério declarado:
>
> | Condição do ADR-08 | Medido |
> |---|---|
> | Ganhar em estabilidade | **Empate**: Jaccard médio 0,5848 (novo) × 0,5797 (antigo); 8 × 7 vitórias em 15 atletas |
> | Não perder cobertura | **Perde**: 86,6% dos nós em comunidade não-trivial × **96,3%** do antigo |
>
> Causa identificada: **Louvain com resolução 1.0 isola mais nós de baixo grau como singleton**
> (até 40% em alguns atletas); o greedy-modularity antigo os absorve. As partições concordam
> bastante (Jaccard agregado 0,773) — acham a mesma estrutura —, mas concordância não era o critério.
>
> **Consequência honesta para a wave 4:** a camada compartilhada continua justificada para o
> relatório de categoria, que precisa de agregação athlete-balanced que o `athlete_systems` não faz.
> Mas a meta de "uma definição de constelação no produto" **não se cumpriu por mérito técnico**. Os
> dois coexistem porque fazem trabalhos diferentes, não porque um venceu. Qualquer tentativa futura
> de unificar tem de passar pelo problema dos singletons primeiro.
>
> **Medição que ficou faltando:** a comparação usou grafo derivado de sequências, enquanto em produção
> o `athlete_systems` consome o `graphs`/`graph_edges` persistido (peso uniforme 1, sem proveniência
> por luta) via `export/ontology.py`. Comparar o detector antigo na entrada real dele é uma segunda
> medição, não feita.

> **Wave 6b, 2026-08-17 — a medição faltante foi feita, e ela REABRE este ADR.**
> Detalhe em [`05_COMPARACAO_DETECTORES.md`](05_COMPARACAO_DETECTORES.md), seção Wave 6b.
>
> | | entrada de sequências (wave 6) | entrada de produção (6b) |
> |---|---:|---:|
> | Cobertura, antigo | 96,3% | **100%** |
> | Cobertura, novo | 86,6% | **100%** |
> | Novo vence em estabilidade | 8/15 (Δ0,005) | **13/15 (Δ0,017)** |
>
> **A vantagem de cobertura que fechou a wave 6 era propriedade da ENTRADA, não do algoritmo.** Só o
> grafo de sequências pode gerar nós de grau zero; `graphs_for_clustering` deriva os nós dos extremos
> das arestas, então a entrada de produção não produz singleton por construção. Na entrada real os
> dois empatam em 100% e a estabilidade pende para o detector novo.
>
> **Status: REABERTO, não invertido.** A evidência nova é mais forte num eixo (entrada real) e mais
> fraca em outro: o bootstrap de 6b reamostra **arestas**, porque `graph_edges` não tem proveniência
> por luta — rigor menor que o teste controlado da wave 6, que reamostrava lutas. Trocar o detector
> de produção com base num bootstrap de aresta seria repetir o erro que 6b acabou de expor: decidir
> por um número que depende do formato da entrada.
>
> **Condições para fechar:** (a) dar proveniência por luta a `graph_edges`, para bootstrapar no mesmo
> rigor da wave 6; (b) estender além dos 15 atletas medidos. Até lá, coexistência **por precaução**,
> não por resultado — a diferença importa e está escrita aqui para ninguém citar a wave 6 como
> decisão encerrada.
>
> Achado colateral que vale por si: comparar partições exige que os dois lados estejam no mesmo espaço
> de chave. Os grafos usavam `"Closed Guard"` (rótulo de exibição) e `"closed guard"`
> (`canonicalize(_normalize_name(...))`), o que dava Jaccard 0,0 em todos os 15 atletas — artefato de
> medição que passaria por "os detectores não concordam em nada" se ninguém tivesse desconfiado de um
> zero perfeito.

---

## ADR-09 — RD alarga até o fim do dataset, não até a última luta do atleta

**Contexto.** Descoberto ao implementar a wave 3. O Glicko-2 alarga o RD em períodos sem observação.
Existem duas leituras: alargar só entre a primeira e a última luta do atleta, ou alargar até o último
período do dataset. A escolha muda o RD de todo mundo que parou de competir, e não estava fixada no
plano.

**Escolha: alargar até o último período do dataset.**

**Por quê.** As duas leituras respondem perguntas diferentes. Parar na última luta responde *"qual
era o rating dele na época"*; alargar até o fim responde *"quanta confiança temos hoje"*. A segunda é
a pergunta do leaderboard e do dossiê. Um atleta cuja última luta foi em 2010 **deve** aparecer com
incerteza enorme em 2026 — se não aparecer, o número mente por omissão.

**Consequência.** O `% de atletas com RD ≥ 200` sobe, e isso é correto, não regressão. Quem quiser o
rating histórico "na época" precisa de uma consulta por período, não de outra semântica de
alargamento. Fixado por teste em `periods.py`.

---

## ADR-10 — Disciplina: grappling ganha de MMA na precedência, e override manual é nomeado

**Contexto.** Também da wave 3. A classificação por palavra-chave marcou `UFC BJJ 4` como `mma`,
porque `ufc` foi testado antes de `bjj`. `UFC BJJ` é uma promoção de **grappling puro**. Além disso,
19 nomes de evento caíram em `unknown` e derrubaram **160 lutas** do replay — quase todos promoções
de grappling conhecidas (Metamoris, Kasai, F2W, SUG, Sub Stars, Combat Jiu-Jitsu Worlds, No Gi Pan Am).

**Escolha.**
1. **Precedência explícita:** `bjj` / `jiu-jitsu` / `grappling` / `no-gi` / `submission` no nome
   vencem `ufc` / `mma`.
2. **Override manual nomeado por evento**, no próprio JSON, separado do que veio de regra. Não
   estender a lista de palavras-chave para cobrir caso conhecido.
3. `unknown` fica reservado para `event` NULL/vazio — sem nome, classificar é inventar.

**Por quê.** Uma palavra-chave nova classifica silenciosamente eventos futuros que ninguém revisou;
um override nomeado é uma decisão auditável sobre um evento que existe. O mapa é artefato de revisão
humana, e a distinção entre "a regra deduziu" e "uma pessoa decidiu" é o que o torna revisável.

**Consequência.** `data/rating_v2/disciplines.json` carrega as duas origens distinguíveis. Evento
novo que não casar com regra nenhuma entra como `unknown` e **fica fora do replay** até alguém
decidir — falha para o lado de excluir, não de contaminar.

> **Armadilha medida em 2026-08-17.** O mapa é indexado por **nome de evento**. Ao aplicar as
> correções da pesquisa web, 89 eventos foram renomeados — e de imediato 46 lutas caíram para
> `unknown`, derrubando o corpus de grappling de 672 para 626 sem que nada de errado tivesse
> acontecido com os dados. Regenerar o mapa devolveu 709 (acima das 672 originais, porque os nomes
> corrigidos passaram a casar com regras que antes não casavam).
>
> **Regra operacional: toda renomeação de evento exige regenerar `disciplines.json` na mesma
> passada.** A alternativa estrutural — chavear a disciplina por `match_id` em vez de nome de evento
> — resolveria de vez, ao custo de perder a legibilidade que torna o mapa revisável por humano.
> Fica registrado como opção, não como pendência: 112 linhas revisáveis valem mais que imunidade a
> renomeação, enquanto renomear for raro.
