# Relatório de categoria ADCC 2026 — análise de tendências e metagame

**Data:** 2026-08-16
**Módulo:** GrapplingArcAnalytics
**Substitui:** o relatório de categoria puramente tabular gerado por `scripts/scouting_division_report.py`
(as tabelas de scout viram anexo, não são removidas)

## Problema

O relatório de categoria atual entrega os cross-tabs da planilha de scout (luta em pé, efetividade,
tempo) agregados por divisão. Isso responde "o que foi registrado", não "o que essa categoria é".
O pedido: análise de tendências — distribuições de técnicas, perfil médio da categoria, leitura do
metagame, e comparação contra o corpus global.

Duas divisões: `ADCC-2026-65kg` e `ADCC-2026-mais-65kg`. Três escopos: no-gi, gi, unificado.
Sem relatório por atleta.

## O que os dados sustentam (medido, não estimado)

| | valor |
|---|---|
| Corpus global (`matches.status='final'`) | 894 lutas · 9.979 eventos · 1.328 atletas |
| Baseline escolhido: no-gi de elite (ADCC/CJI/WNO/Polaris) | 488 lutas · 7.854 eventos |
| Categoria 65 kg (no-gi) | 22 lutas · 282 eventos |
| Categoria +65 kg (no-gi) | 29 lutas · 323 eventos |
| Categoria, escopo gi | 1 luta (65 kg) · 3 lutas (+65 kg) |

Restrições duras da base:
- **Não existe campo de sexo** em `athletes`; `weight_class` é NULL em 764 das 894 lutas. Baseline
  feminino ou por faixa de peso **não é derivável** — o relatório declara que o baseline mistura
  gênero, peso e (fora do recorte no-gi de elite) uniforme.
- **Concentração de amostra.** Em +65 kg no-gi, uma única atleta responde por ~95% dos eventos
  próprios da categoria. Qualquer média ponderada por evento descreve essa atleta, não a categoria.
- **Efetividade não apurada em volume.** Boa parte dos eventos (especialmente costas e quedas) não
  tem `successful` resolvido. Taxa calculada sobre essa base é ficção.
- **Tempo ausente.** 65 kg está integralmente em `SEM TEMPO`; lutas vindas do banco não têm `ts`
  nem `duration_s`.

## Decisões

| Decisão | Escolha | Por quê |
|---|---|---|
| Baseline | **elite no-gi corpus baseline** (ADCC/CJI/WNO/Polaris), 488 lutas | Metagame comparável: mesmo jogo, mesma regra. Robusto o bastante. |
| Nome do baseline | Sempre "corpus de elite no-gi", **nunca "metagame global"** | Ele mistura sexo, peso, eras e tem cobertura desigual. Não é global em sentido estatístico. A pergunta correta é "como estas divisões diferem do corpus observado de submission grappling de elite". |
| "Tendência" | Desvio vs baseline, não série temporal | ~50 lutas espalhadas por vários anos não sustentam curva por categoria. |
| Perfil médio principal | **Peso igual por atleta** | Neutraliza a dominância de uma atleta. As outras duas leituras aparecem ao lado como conferência. |
| Simetria de ponderação | Categoria e baseline sempre comparados **com a mesma ponderação** | Ver abaixo. Comparar média equal-weight da categoria contra baseline event-weighted mistura dois estimandos e produz um lift que não significa nada limpo. |
| Glicko-2 / rating no relatório | **Fora, por princípio** — inclusive depois da Rating Engine V2 entrar | Rating, metagame e estrutura de jogo são três camadas separadas. Misturá-las produz "Back Control tem rating alto, logo a categoria é caracterizada por Back Control" — o acoplamento que a V2 existe para eliminar. |

### Simetria de ponderação (regra explícita)

```text
PRIMARY        categoria athlete-balanced   vs   baseline athlete-balanced
SECONDARY      categoria event-weighted     vs   baseline event-weighted
ROBUSTEZ       leave-one-out da dominante, aplicado ao PRIMARY
```

Os dois pares respondem perguntas diferentes e o relatório rotula qual está respondendo:

- *"Como é o jogo médio de uma atleta desta divisão?"* → athlete-balanced
- *"O que de fato aparece com maior frequência no corpus?"* → event-weighted

Nunca cruzar os pares.
| Fila/vídeos pendentes | Fora de escopo | O inventário (xlsx) vive fora do repo. O relatório entrega só priorização por lacuna medida. |
| Relatório consolidado externo | Orientação, não fonte | Seus avisos de método são incorporados; seus números não são importados nem reconciliados. |
| Arquétipos/embeddings | Cortado | Dependem de grafos de atleta publicados; essas 16 atletas quase não têm grafo. |

## Arquitetura

Reuso, em ordem da escada:

- `analysis/scouting_report.py:collect_bouts` — corpus dos dumps (já existe)
- `scripts/scouting_division_report.py` — corpus do banco, dedup, escopos, cobertura (já existe)
- `analysis/network_metrics.py:network_from_sequences` — **função pura**, lista de sequências
  actor-tagged → `nx.DiGraph`; PageRank, comunidades, reward−risk de Markov (Lamas et al. 2024)
- `analysis/scouting_tables.py:build_tables` — cross-tabs, viram anexo
- `analysis/ocean.py` — o idioma "métrica relativa à população (percentil + ratio)", já usado no site

Módulos novos, um propósito cada:

```
analysis/category_profile.py   # PURO: (eventos da categoria, eventos do baseline) → perfil + desvio
                               #   distribuição por tipo/label, três leituras do perfil médio,
                               #   lift com gate de amostra + bootstrap, concentração (top-1 + HHI),
                               #   valor marginal de cobertura
analysis/report_charts.py      # PURO: números → SVG inline (sem dependência nova)
scripts/scouting_division_report.py   # orquestra: corpus → category_profile → charts → html/pdf/csv
```

### Camada topológica compartilhada (decisão de arquitetura)

Constelação tem **uma** definição no produto, não uma por consumidor. O relatório de categoria e a
Rating Engine V2 usam o mesmo detector:

```
analysis/
  transitions/      build_graph.py, normalize.py
  constellations/   detect.py, stability.py, compare.py
  rating_v2/        glicko2.py, replay.py
```

```text
Athlete Rating Engine  ──> constellations/detect.py
Category Trend Report  ──> constellations/detect.py
Baseline de elite      ──> transitions/build_graph.py
```

Nenhum consumidor implementa a sua própria versão. `analysis/athlete_systems.py` e
`analysis/network_metrics.py` migram para essa camada em vez de divergir dela — o caminho de
migração é documentado em `docs/rating_v2/`. O relatório de categoria **consome** o detector; não
consome nada de `rating_v2/`.

`category_profile.py` não abre sessão de banco e não lê arquivo: recebe listas de eventos. Isso o
torna testável sem fixture e sem rede — mesma regra que `network_from_sequences` já segue.

## Seções do relatório (por divisão, dentro de cada escopo)

1. **Concentração da amostra** — share da atleta top-1 e HHI. Vem antes de tudo: é a licença (ou a
   recusa de licença) para ler o resto como "categoria".
2. **Perfil médio da categoria** — composição por tipo de evento (guarda / passagem / queda /
   finalização / controle / raspagem), em três leituras lado a lado: peso igual por atleta
   (principal), ponderado por evento, leave-one-out da dominante. Barra da categoria com a marca do
   baseline sobreposta. Quando as três divergem, o texto diz que divergem.
3. **Distribuição de técnicas** — top-15 posições/técnicas por frequência, `n` sempre visível.
4. **Metagame: o que desvia** — lollipop divergente, log2(p_categoria / p_baseline), no par PRIMARY
   (athlete-balanced dos dois lados) e, ao lado, no par SECONDARY (event-weighted dos dois lados).
   Gate: label com n < 3 na categoria fica fora do ranking e vai para uma lista "amostra
   insuficiente" — sem gate o lift explode em cima de uma ocorrência única.
   **Cada divergência carrega estabilidade por bootstrap**, não só magnitude: reamostra
   atletas/lutas, recalcula frequência e lift, e rotula a estabilidade. Sem isso, uma técnica com
   três ocorrências parece mais importante que uma tendência repetida em várias atletas:
   ```text
   Técnica X   +1.8× baseline   estabilidade: alta
   Técnica Y   +2.4× baseline   estabilidade: baixa
   ```
5. **Constelações e estrutura do metagame**
   - **5.1 Construção** — rede de transições por atleta → normalização por atleta → agregação
     athlete-balanced → detector compartilhado (`analysis/constellations/detect.py`, o mesmo da
     Rating Engine V2). **Rating não participa da membership.**
   - **5.2 Comparação com o baseline** — por constelação: prevalência na categoria, prevalência no
     baseline, transition lift, nós centrais, transições características.
   - **5.3 Robustez** — bootstrap, leave-one-out, estabilidade de Jaccard, concentração de
     contribuição por atleta. Classificação:
     ```text
     STABLE             comunidade sobrevive
     PARTIALLY STABLE   núcleo sobrevive, membership muda
     ATHLETE-DRIVEN     comunidade desaparece sem a atleta dominante
     ```
   - **5.4 Gate de publicação** — uma constelação só recebe interpretação de metagame se tiver
     suporte suficiente, **mais de uma atleta contribuindo** e estabilidade mínima. Caso contrário o
     texto sai como "padrão observado, evidência insuficiente para tendência de categoria".
     É o que permite escrever *"este padrão aparece no corpus da divisão, mas hoje é sustentado
     principalmente por uma atleta"* em vez de chamar aquilo de "meta +65 kg".
6. **Rede de transições** — top transições da categoria com cor por lift vs baseline; scatter
   reward−risk das posições da categoria contra a linha do baseline.
7. **Efetividade** — só sobre eventos **resolvidos**, com `resolvido X% da base` impresso ao lado de
   cada linha. Nenhuma taxa sobre base não apurada.
8. **Tempo** — só renderiza se a fatia com tempo ≥ 30% dos eventos; senão imprime "sem base" e
   nenhum gráfico.
9. **Valor marginal de cobertura** — quais atletas, se analisadas, mais reduzem a concentração.
   Derivado do próprio corpus (quem está zerado ou sub-representado), não do inventário externo.
10. **Cobertura e limitações** — a seção que já existe, mantida.
11. **Anexo** — cross-tabs de scout (luta em pé, efetividade, tempo) como estão hoje.

Escopo **gi** (1 e 3 lutas): seções 2–9 são substituídas por um bloco "amostra insuficiente para
leitura de categoria"; cobertura e anexo continuam saindo.

**Pré-condição P0 (satisfeita em 2026-08-16):** a correção do bug de identidade de atleta tinha de
aterrissar antes de qualquer cálculo desta spec — ela contaminava atribuição de eventos e
deduplicação, logo contaminaria distribuição, lift, PageRank e constelações, não apenas W/L.
Fechada: 982 testes passando.

## Visualizações

SVG inline gerado em Python — sem dependência nova, imprime nítido no Chrome headless que já gera o
PDF. Herda os tokens de cor/tipografia do `REPORT_CSS`. Charts pela skill `dataviz`, layout pela
`impeccable`.

| Seção | Forma | Por quê essa forma |
|---|---|---|
| Perfil médio | barras horizontais agrupadas + marca do baseline | comparação de partes de um todo entre 3 leituras |
| Distribuição | barras horizontais ordenadas, `n` na ponta | ranking com magnitude |
| Desvio/metagame | lollipop divergente centrado em 0 | sinal (mais/menos que o mundo) é a mensagem |
| Reward−risk | scatter com linha do baseline | duas dimensões, comparação contra referência |
| Transições | tabela com barra embutida (não grafo) | 12 linhas legíveis > hairball ilegível em papel |
| Concentração | barra empilhada única por divisão | mostra dominância em uma olhada |

Regra herdada do produto: **nunca valor bruto de rating** — sempre relativo + %.

## Saídas

Sem mudança de contrato: 3 escopos × `{html, pdf, csv}` em
`reports/adcc-2026-categoria/adcc-2026-categoria-{nogi,gi,unificado}.{html,pdf,csv}`.

CSV ganha as tabelas novas no mesmo formato tidy já existente
(`divisao,escopo,tabela,agente,linha,coluna,valor`), com `tabela` ∈ {`perfil_medio`, `distribuicao`,
`desvio`, `rede`, `concentracao`, `valor_marginal`, + as atuais}. Nenhuma coluna nova — o CSV
continua pivotável.

## Erros e casos de borda

- Categoria sem nenhum evento no escopo → seção "amostra insuficiente", nunca divisão por zero.
- Label presente na categoria e ausente no baseline → lift infinito: reportado como "inédito no
  baseline" com `n`, fora do ranking numérico.
- Chrome indisponível → HTML preservado, aviso no stderr, os outros escopos continuam (comportamento
  atual, mantido).
- Baseline que se sobrepõe à categoria: as lutas das próprias atletas do roster **saem** do baseline
  antes do cálculo, senão a categoria se compara parcialmente consigo mesma.

## Testes

`tests/test_category_profile.py` — puro, em memória, sem banco:
- perfil com peso igual por atleta ≠ ponderado por evento quando uma atleta domina (o caso Helena,
  em miniatura)
- HHI e top-1 share em corpus concentrado vs distribuído
- gate de n < 3 mantém o label fora do ranking e dentro da lista de amostra insuficiente
- label inédito no baseline não vira lift infinito
- exclusão das lutas do roster do baseline
- efetividade calculada só sobre resolvidos; base 100% não apurada → sem taxa
- simetria de ponderação: o par PRIMARY nunca compara athlete-balanced contra event-weighted
- bootstrap: corpus com uma técnica repetida por várias atletas sai com estabilidade maior que
  corpus com a mesma frequência concentrada em uma atleta
- constelação que só existe por causa da atleta dominante é classificada `ATHLETE-DRIVEN` e não
  passa pelo gate de publicação
- SVG: cada gerador devolve string com `<svg` e sem `NaN`

`uv run pytest -q` inteiro precisa passar — os módulos tocados são compartilhados.

## Fora de escopo

Dossiê por atleta; série temporal; arquétipos/embeddings; import da fila de vídeos; qualquer
reconciliação automática com o PDF consolidado externo.
