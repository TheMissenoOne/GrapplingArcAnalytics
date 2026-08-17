# Auditoria do bundle Glicko-2 contra o repo real

Data: 2026-08-17. Baseline do bundle: `3d02020` = HEAD atual de GrapplingArcAnalytics.

## Veredito

**O bundle está estruturalmente correto e deve ser seguido no essencial.** A matemática confere, o
rollout shadow-first é a decisão certa, a separação rating / nó / constelação é a arquitetura certa,
e o autor claramente leu o código antes de escrever. A auditoria não precisa achar defeito para
justificar existir — e o defeito principal que ela achou **não está na engine proposta, está no
corpus** que a engine vai consumir.

Dois achados bloqueiam a wave 2 e nenhum dos dois é mencionado no bundle: **vencedor ausente em 30%
das lutas** e **contaminação de MMA no corpus de grappling**. Ambos detalhados abaixo.

## 1. Gate matemático — PASSA

`scripts/glicko2_reference_core.py` reproduz o exemplo publicado do Glicko-2:

| | obtido | esperado |
|---|---|---|
| rating | 1464.05 | 1464.06 |
| RD | 151.52 | 151.52 |
| volatilidade | 0.059996 | ~0.059996 |

O core do bundle pode ser usado como referência de implementação. Diferença de 0.01 no rating é
arredondamento do exemplo publicado, não erro.

## 2. Replay de sombra sobre o corpus real — medido

Executado read-only: 894 lutas `status='final'`, seed 1750 / RD 250 / vol 0.06 / tau 0.5, períodos
anuais, estados pré-período (ordem de importação não afeta resultado).

| Medida | Valor |
|---|---|
| Atletas que recebem estado | 821 (de 1.328 na tabela — 507 não têm nenhuma luta final) |
| Lutas por atleta | mediana **1**, média 2.18, máx 114 |
| Atletas com exatamente 1 luta | **530 (64,6%)** |
| Atletas com ≥6 lutas | 48 (5,8%) |
| RD final ≥ 200 | **542 (66,0%)** |
| Rating a ±25 do seed | 234 (28,5%) |
| Faixa de rating | min 1474 · p25 1643 · mediana 1750 · p75 1801 · máx 2200 |
| Atletas ≥ 2100 (referência "elite tail" do bundle) | **3** |

Top por rating conservador (`rating − RD`):

| Atleta | rating | RD | cons. | lutas |
|---|---:|---:|---:|---:|
| Gordon Ryan | 2199.6 | 56.8 | 2142.8 | 114 |
| Georges St-Pierre | 2122.7 | 136.4 | 1986.3 | 10 |
| Khabib Nurmagomedov | 2111.0 | 140.9 | 1970.1 | 9 |
| Helena Crevar | 2026.0 | 113.1 | 1913.0 | 13 |
| Ffion Davies | 2032.3 | 161.3 | 1871.0 | 5 |
| Sarah Galvao | 2010.2 | 153.3 | 1857.0 | 5 |
| Craig Jones | 1927.5 | 76.0 | 1851.6 | 31 |

Leitura: **a engine funciona** — Gordon Ryan emerge no topo com RD baixo, por resultado, sem campo
de convergência. O bundle estava certo ao dizer que 2100 deve ser consequência, não alvo. Mas o
mesmo replay expõe os dois problemas abaixo.

## 3. ACHADO BLOQUEANTE — vencedor ausente em 30% do corpus

`matches.winner_id` é NULL em **283 das 894 lutas (31,7%)**. A decomposição por `win_type`:

| `win_type` com `winner_id` NULL | lutas |
|---|---:|
| `DECISION` | **271** |
| `DRAW` | 3 |
| NULL | 9 |

Só **3** são empate de verdade. As outras 271 terminaram por decisão e o vencedor simplesmente não
foi gravado. O comentário do modelo (`db/models.py`) já avisa: `NULL = draw / no-contest / unknown` —
três coisas semanticamente diferentes colapsadas em um valor.

Consequência para a V2: tratar NULL como 0.5 **fabrica empate em 30% do corpus** e puxa a massa dos
atletas para o seed — o que explica boa parte da mediana ficar exatamente em 1750. Tratar como
"ignorar" descarta um terço da evidência. Nenhuma das duas é aceitável em silêncio.

Este é o mesmo tipo de defeito que o bug de identidade de atleta corrigido em 2026-08-16: um dado
"quase" certo que envenena tudo a jusante. **É pré-condição P0 da wave 2**, não trabalho da engine.

## 4. ACHADO — contaminação de MMA no corpus de grappling

Georges St-Pierre e Khabib Nurmagomedov aparecem em 2º e 3º no rating conservador. São lutadores de
MMA. O corpus tem `scripts/insert_mma_matches.py`, `insert_ufc_card.py`, `insert_ufc_matches.py`,
`insert_khabib.py`, e há `analysis/ufc_elo_engine.py` separado.

Por nome de evento, **34 lutas** casam com `ufc|mma|bellator|one champ|boxing` — provavelmente
subestimado, porque parte dos inserts não carimba o evento com essas palavras.

O bundle propõe **um** rating global por atleta e não menciona disciplina. Um Glicko-2 único
misturando MMA e submission grappling produz uma escala sem significado: vitória de MMA e vitória de
grappling não medem a mesma habilidade, e a comparabilidade entre adversários — que é a premissa do
Glicko — deixa de valer.

## 5. Períodos de rating — o corpus não sustenta o que o bundle pede

O bundle recomenda período = **dia de evento**. Medido: `matches` **não tem coluna de data**. Só
`year` (preenchido em 894/894) e `created_at` (data de importação, não da luta).

Portanto o período mais fino disponível hoje é o **ano**: 20 períodos, 2002–2026. Dentro de um ano,
um ADCC inteiro vira um único período — o que é aceitável (é exatamente o que "todos usam estado
pré-período" resolve), mas significa que um atleta que evolui dentro do ano não é capturado.

## 6. Conferência ponto-a-ponto do bundle

| Afirmação do bundle | Situação |
|---|---|
| Baseline `3d02020` | **Confere** — é o HEAD atual |
| `analysis/athlete_elo.py` é a engine atual | **Confere** (334 linhas) |
| V1 tem belt floors, `rank_target`, K por gap, mult. competitivo, decay temporal manual | **Confere**: `BELT_BASE_ELO`, `BASE_BLACKBELT_ELO=800`, `K = base(n) × gap_factor × 2.5 × 2^(−meses/meia-vida)` |
| `athlete_graph.py`, `athlete_systems.py`, `network_metrics.py` existem | **Confere** (121 / 582 / 397 linhas) |
| "inspecione o alembic head antes de escolher a revisão" | **Confere** — head é `0034` |
| Louvain do networkx para comunidades | Disponível, mas ver ponto 7 |
| `1/RD²` para agregação por eixo do usuário | Matemática correta (precisão inversa da variância) |
| Corpus produz "elite tail" perto de 2100 | **Parcialmente** — 3 atletas, e 2 deles são de MMA |

## 7. Omissões do bundle que este repo torna obrigatórias

1. **Vencedor ausente / semântica de NULL** — ponto 3. Não citado.
2. **Disciplina (MMA × grappling)** — ponto 4. Não citado.
3. **Regra público × privado (LGPD)** — o bundle acerta ao dizer que estado de usuário não vai para
   tabelas públicas de atleta, mas não menciona a regra do repo: *toda query que constrói artefato
   público/competitivo filtra `owner_kind` explicitamente*. As tabelas V2 propostas são todas
   `athlete_*`, então o risco é baixo — mas o filtro precisa estar escrito no código de replay.
4. **`node_key` char-for-char** — o bundle diz "identidade canônica primeiro", correto, mas não
   menciona que a normalização tem de casar exatamente com `normalizeLabel()` de
   `GrapplingArcApp/src/services/graphSync.ts`. Uma engine que invente a sua normalização quebra o
   sync do App.
5. **"Grappling ELO sempre relativo + %"** — regra de apresentação do produto: nunca exibir rating
   bruto. A V2 introduz RD, o que na verdade *ajuda* (dá como expressar confiança), mas o contrato
   de apresentação precisa ser reescrito, não herdado.
6. **Replay completo obrigatório quando muda config** — o repo já opera assim para K; o bundle diz o
   equivalente ("nova versão + shadow replay"), então aqui há concordância.
7. **Louvain vs Leiden** — o bundle propõe Louvain sem mencionar o defeito conhecido de comunidades
   mal conectadas (Traag et al., 2019, *From Louvain to Leiden*), que é justamente o risco de
   "megacluster" que o próprio doc 06 lista como failure gate. Ver `01_DECISOES.md`.

## 8. O que reproduzi dos relatórios do bundle

Os PNG/CSV em `reports/global_projection/` e `reports/node_constellations/` foram gerados pelo autor
do bundle com dados próprios do estudo. **Não reproduzi esses números** — o replay acima é
independente, feito direto do banco de produção, e é o que deve ser tratado como medida de
referência daqui em diante. Onde os dois discordarem, vale o replay direto do banco.
