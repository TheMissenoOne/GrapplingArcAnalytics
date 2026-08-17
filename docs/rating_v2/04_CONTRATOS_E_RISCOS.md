# Contratos cross-module e registro de riscos

## 1. O que quebra

| Contrato | Onde | O que a V2 faz | Gravidade |
|---|---|---|---|
| Tabela ELO exportada | `export/adcc_elo_table.py` → `@grapplingarch:elo_stats` no App | escala nova (seed 1750 vs base 800) e ordenação nova (sem convergência a `rank_target`) | **Alta** — o App consome como dado pronto |
| Matemática de ELO espelhada | `GrapplingArcApp/src/services/eloService.ts` + `sequenceScorer.ts` ↔ `analysis/elo_calibration.py` | a V2 é outro algoritmo (RD + volatilidade), não uma reparametrização | **Alta** — porte, não ajuste |
| `GA_ELO` no bundle do site | `export/site_data.py` → `*-data.js` | números mudam; site regenera inteiro | Média — regen já é procedimento padrão |
| `node_key` char-for-char | `graphSync.ts:normalizeLabel()` ↔ `analysis/names.py:_normalize_name` | a V2 chaveia estado por nó; se inventar normalização, quebra o sync | **Alta** e silenciosa |
| Apresentação "sempre relativo + %" | dossiê, site, App | RD e volatilidade são conceitos novos a expor | Média — oportunidade, ver abaixo |
| `athletes.elo`, `graphs.user_elo`, `AthleteNode.computed_elo` | banco | **não são tocados** até o cutover | Baixa, por desenho |

### Ordem de migração

1. Analytics em sombra, sem consumidor (waves 1–6).
2. Persistência V2 em paralelo, V1 intacta (wave 7).
3. Um consumidor por vez atrás de `engine_version` (wave 8): dossiê interno → site → export do App.
4. Só então o porte TypeScript, com fixtures douradas (wave 9), em **PR separado no repo do App**.

O bundle acerta essa ordem. Onde ele é ingênuo é no doc 07: "porte a matemática exata" subestima que
o App hoje calcula ELO **on-device a partir das sessões do usuário**, e o modelo de usuário da V2
(evidência só de tentativa própria com `successful` conhecido, agregação por eixo com `1/RD²`) é uma
mudança de produto, não só de fórmula. Isso precisa do seu próprio ciclo de brainstorming antes do
porte.

### Onde a V2 melhora a apresentação

A regra "nunca rating bruto, sempre relativo + %" existe porque um número absoluto sem contexto
engana. O Glicko-2 traz RD, que é exatamente a linguagem que faltava: passa a ser possível dizer
*"acima da média da população, com evidência limitada"* em vez de esconder a incerteza. O contrato de
apresentação deve ser **reescrito** para incluir confiança, não herdado como está.

---

## 2. Riscos

### R1 — O corpus é raso demais para rating por atleta (ALTO, medido)

**64,6% dos atletas têm exatamente 1 luta.** Mediana 1, média 2.18. **66% terminam com RD ≥ 200**,
partindo de 250 — ou seja, dois terços do corpus sai do replay praticamente sem informação, com
rating perto do seed.

Consequência: qualquer ranking geral derivado da V2 é, para a maioria dos atletas, uma reordenação de
ruído em torno do prior. Só ~48 atletas (5,8%) têm ≥6 lutas.

**Mitigação:** publicar sempre `rating − RD` (conservador) e nunca ranking sem filtro de evidência
mínima. Aceitar que a V2 é útil para a cauda densa e honesta-por-omissão para o resto — que é
melhor que a V1, onde a convergência ao `rank_target` *dava aparência de ordenação* a quem não tem
evidência nenhuma.

### R2 — Um terço do corpus não tem vencedor (ALTO, medido, corrigível)

283 lutas sem `winner_id`, das quais 271 são `DECISION`. Ver ADR-06. É o maior ganho de qualidade
disponível e é trabalho de dado, não de engine.

### R3 — Contaminação de disciplina (ALTO, medido, corrigível)

MMA no top-3 do rating de grappling. Ver ADR-05.

### R4 — Shadow eterno (MÉDIO)

V1 e V2 coexistindo para sempre é o desfecho mais provável de rollouts assim: o shadow nunca é
promovido porque nunca há um momento óbvio. **Mitigação:** a wave 7 define os critérios de promoção
*antes* de rodar, e a coexistência tem prazo escrito.

### R5 — Rating por nó sem sustentação (MÉDIO)

Com mediana de 1 luta por atleta, a evidência por nó é ainda mais rala que a global. O sweep pode
concluir que nenhum peso melhora a predição. **Isso é um resultado válido** e deve ser publicado;
o risco é escolher um peso por estética depois de ver os gráficos.

### R6 — Louvain com comunidades desconexas (MÉDIO)

Ver ADR-07 (Traag et al. 2019). Mitigação barata: gate de conectividade antes de considerar Leiden.

### R7 — Custo de replay (BAIXO)

O replay global sobre 894 lutas roda em segundos. Mesmo com nós e sweep de parâmetros, é ordem de
minutos. Não é gargalo — a exportação do site (~10–12 min) continua sendo o processo caro do repo.

### R8 — Regressão silenciosa de `node_key` (BAIXO, catastrófico se acontecer)

Se a V2 normalizar rótulo por conta própria, o sync do App quebra sem erro visível: os nós
simplesmente deixam de casar. **Mitigação:** teste de contrato que compara a normalização das duas
implementações sobre uma lista de rótulos reais.

---

## 3. Caminho crítico até o primeiro número confiável

```text
wave 1 (vencedor + disciplina)  →  wave 2 (core)  →  wave 3 (replay global)
```

Só isso já entrega um rating de grappling defensável para a cauda densa de atletas. Nós,
constelações, persistência e porte vêm depois e não bloqueiam o primeiro número.
