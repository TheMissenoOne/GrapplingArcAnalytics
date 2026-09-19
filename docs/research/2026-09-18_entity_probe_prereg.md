# PoC-E17 — entity probe, braço B (pair-crop DINOv2 vs. frame inteiro)

**STATUS: PRE-REGISTRATION ONLY.** Nenhum número held-out E17 existia quando este arquivo foi
escrito. `scripts/research/e17_entity_probe.py` (a escrever) deve re-emitir este arquivo inteiro
acima dos resultados, entre marcadores `<!-- E17:RESULTS -->`, mesma convenção de `e0_ksweep_whr.py`
e `e10_terminal_hazard.py` — o critério viaja com o número que ele julga.

Avalia uma proposta externa (dono) de reconstruir o pipeline de leitura visual em torno de
entidades (caixa por atleta) em vez de frame inteiro ou pose. Este documento decide **um** braço
minimal antes de qualquer outro trabalho de anotação ou arquitetura.

---

## 1. Proposta avaliada (resumo)

Pixels → entidades A/B via bboxes do detector já existente → pair crop (união dos dois + 10–20%
de padding) → embedding DINO congelado → classificador leve em cima. Quatro braços possíveis:
**A** pose apenas, **B** pair crop apenas, **C** A+B, **D** A+B+pair. Identidade oráculo (top vs.
bottom, A vs. B) precede qualquer tracking automático. Cinco famílias de posição. Split por luta.
Unidade de amostra = episódio/âncora. Permutação de atores como controle de leakage. Cabeças
hierárquicas (família → subtipo) só depois de um braço passar. Temporal (Viterbi sobre a timeline)
só depois de identidade+braço estarem resolvidos. SAM2 como candidato a tracker, também depois.

## 2. O que o repo confirma (paths verificados)

- `bjj-harmony4d-grapplemap-mvp-v0.3/bjj-harmony4d-mvp/scripts/diag_seed_probe.py` **já é** o probe
  descrito na proposta, só que sobre frame inteiro: backbone congelado (ConvNeXt-Tiny, ImageNet,
  `model.classifier` truncado antes do fc) → embeddings cacheados em `.npz` → `GroupKFold` por
  `match_id` (a "honest split") → `LogisticRegression` (`StandardScaler` + `C=1.0`) → OOF
  predictions + tabela de leakage (`grouped by VIDEO/MATCH/ANCHOR/frame-level`, mostrando a
  inflação de cada split mais fraco). Produz `reference_agreement` contra `WEAK_ANALYTICS`, nunca
  "accuracy" — a label de referência é ela mesma não verificada (linhas 1-16, 125-157).
- `bjj-harmony4d-grapplemap-mvp-v0.3/bjj-harmony4d-mvp/src/bjj_harmony/dataset_builder/crops.py`
  **já faz** exatamente o passo de pair crop da proposta: `detect_boxes` (YOLO classe `0`=pessoa,
  `conf=0.18`, `imgsz=960`, ordenado por área desc) → top-2 caixas → `union_with_pad` (`PAD=0.15`,
  dentro da faixa 10–20% pedida) → `add_pair_crops` grava `pair_crop` + `pair_meta` no
  `samples.jsonl` (`pair_verified=false` sempre — identidade NUNCA é assumida por esse código).
  Roda só sobre amostras retidas, depois do dedupe.
- Baseline de pose existente: `GrapplingArcAnalytics/poc/decision_vision/train_temporal.py` — LOGO
  (`LeaveOneGroupOut` por `match_id`) sobre 67 eventos / 6 lutas, `StandardScaler` +
  `LogisticRegression(class_weight="balanced")`, três blocos de feature (`pose`/`position`/`fused`).
  Resultado citado no inventário de vídeo (`docs/video-analysis-modules-2026-09-18.md` §5): pose
  category macro-F1 **0,1609**, posição 0,0508, fusão 0,1375 — pose isolada é o melhor braço já
  medido, mas ainda muito abaixo de útil. Frames não são persistidos por esse experimento.
- Probe congelado anterior sobre **frame inteiro** (ConvNeXt, o mesmo `diag_seed_probe.py`) ficou
  **abaixo da maioria** no split por luta — inventário confirma (§5, "RGB estático... held-out
  macro-F1 0"; `diag_seed_probe.py` documenta a mesma lição no docstring: frame inteiro sem crop de
  pessoa é a hipótese fraca sendo testada, não a vencedora).

## 3. O que o repo NÃO tem (os fatos que reordenam a proposta)

- **Zero bboxes por atleta em qualquer manifesto de treino.** O finetune público
  (`vision_dataset.py`, 481 rotulados) não carrega bbox nenhum. O Harmony v3 (4.425 amostras)
  grava `candidate_person_boxes: []` por padrão — `crops.py` só popula esse campo quando chamado
  explicitamente sobre amostras retidas, e nada no inventário indica que essa chamada já rodou
  sobre o corpus atual de forma persistida e auditada.
- **Nenhum vínculo ator↔caixa.** As caixas de `detect_boxes` não carregam identidade (A/B, top/
  bottom) — só bbox + confiança + área. "Identidade oráculo antes de tracking" da proposta, então,
  não é um dado que já existe: exige **anotação humana**, a mesma anotação que também destrava o
  gate de tracking ≥70% (inventário §6, "gate de role-resolved ≥70% não foi atingido").
- **Rótulos 100% `WEAK_ANALYTICS`.** `diag_seed_probe.py` já assume isso na própria nomenclatura
  (`reference_agreement`, não `accuracy`). AA-004 mede ~50% das âncoras de estado persistente fora
  de tempo; a auditoria do Harmony encontrou 77/96 anchors errados em `north_south`/`open_guard`
  (inventário §6). Qualquer ganho de macro-F1 num braço pode ser lido contra rótulo errado, não
  contra sinal real — a leitura tem teto abaixo de 100% por essa razão sozinha (mesmo raciocínio do
  E10 prereg §7.1 para o "threat" documentado antes do número).
- **`split` é nulo no Harmony hoje.** `dataset_builder/frame_sampler.py:422` grava
  `"split": None` — o campo existe no schema, o código para popular por luta ainda não roda. Um
  experimento aqui não pode ler um split pronto; precisa construir o próprio `GroupKFold` por
  `match_id`, como `diag_seed_probe.py` já faz.
- **Ambiente CPU-only.** Ryzen 7 5800XT, AMD (sem CUDA), 3,8 GB livres medidos. DINOv3 está gated
  no Hugging Face (requer aceite de licença + token) — inviável sem esse passo manual. DINOv2
  ViT-B/14 é **ungated**, carregável via `torch.hub.load('facebookresearch/dinov2', 'dinov2_vitb14')`
  ou `transformers` (ambos já instalados no venv do Harmony); `timm` **não** está instalado — não
  usar como caminho de carga.
- **YOLO geral funde corpos emaranhados.** Medido 2026-09-16 em `dictionary_seed.py` — o detector
  de pessoa genérico (o mesmo usado por `crops.py`) erra quando os dois atletas estão muito
  próximos/emaranhados, produzindo uma caixa única ou caixas mal separadas. O pair crop herda esse
  erro; braço B não corrige identidade, só empacota o que o detector vê.

## 4. Experimento pré-registrado E17 — "entity probe, braço B"

### Hipótese

**H1.** No split por luta (`GroupKFold(n_splits=5, groups=match_id)`) sobre as 5 famílias do
Harmony v3 já supervisionadas (`mount`, `back_control`, `half_guard`, `closed_guard`,
`side_control`; 2.945 amostras supervisionadas), o embedding **DINOv2 ViT-B/14** do **pair crop**
supera, em macro-F1 OOF:

1. a maioria (`34,4%` — baseline citada, a família mais frequente do Harmony v3), e
2. o probe de frame inteiro existente (ConvNeXt via `diag_seed_probe.py`, re-rodado sobre o
   mesmo split e o mesmo subconjunto de 5 famílias para ser comparável),

por margem pré-registrada **≥ +0,05** de macro-F1, com significância **p ≤ 0,05** no nulo por
permutação (rótulos permutados em train+eval, modelo re-fitado, 200 permutações — mesma forma da
seção 5 do E10, "joint permutation with re-fit", não permutação só do eval).

### Braços

- **A0 — maioria.** Classe mais frequente prevista sempre. Não é um modelo, é o piso.
- **A1 — frame inteiro.** ConvNeXt-Tiny congelado (`diag_seed_probe.py`, sem crop — `CROP=1.0`),
  re-rodado sobre o mesmo split e as mesmas 5 famílias (o script original roda sobre todas as
  famílias presentes; aqui filtra-se para as 5 comparáveis).
- **B — pair crop DINOv2.** `crops.py:add_pair_crops` (YOLO top-2 + `union_with_pad(0.15)`) →
  DINOv2 ViT-B/14, resolução 224 px (padrão) → `LogisticRegression` + `StandardScaler`, mesma
  forma de `diag_seed_probe.py:oof`. Um sub-braço em 448 px é reportado só se couber no orçamento
  de tempo (ver custo abaixo) — não é parte do critério PASS/FAIL, é uma leitura.

Braços C/D (A+B, A+B+pair) **não** entram neste cell — dependem de identidade oráculo por
anotação (§5), que ainda não existe. Cabeças hierárquicas, Viterbi temporal e SAM2 ficam fora pela
mesma razão: cada um pressupõe que um braço single-frame já bateu o piso.

### Unidade, rótulo, split

- **Unidade = âncora** (`match_id`, `event_timestamp`), não frame. Uma âncora do Harmony produz
  até 7 offsets (frame_sampler); todos os offsets da mesma âncora ficam no mesmo fold — o grupo do
  `GroupKFold` é `match_id`, que já contém a âncora inteira, então isso é automático, mas fica
  registrado para que ninguém troque o `groups=` por `anchor_id` achando que é mais fino.
- **Rótulo = família** (as 5 acima), lido de `samples.jsonl["family"]`, mesma fonte que
  `diag_seed_probe.py` usa hoje.
- **Split = `GroupKFold(n_splits=5, groups=match_id)`**, honesto por construção — não usa o
  `split=None` do manifesto (§3).

### Death rules (na ordem em que são checadas)

1. **B não bate A0** → representação (pair crop + DINOv2) não é o gargalo; o problema é rótulo/
   dado, não modelo. Parar, não tentar C/D nem features maiores.
2. **B bate A0 mas não A1** (frame inteiro) → o ganho, se algum, é do recorte geométrico
   (removeu fundo/plateia), não do DINOv2 em si. Registrar como achado, mas não licencia
   "DINO resolve" — testaria isso separadamente crop geométrico simples vs. crop com DINOv2 seria
   o próximo cell, não este.
3. **B bate ambos** (A0 e A1, com a margem e o p-valor pré-registrados) → prossegue para a tarefa
   de anotação caixa+papel (§5), pré-requisito real de C/D e de qualquer identidade oráculo.

### Custo estimado (CPU-only, orçamento antes de rodar)

- YOLOv8n/v11n person boxes sobre ~3k frames: 5–8 min.
- DINOv2 ViT-B/14 @ 224 px, CPU, ≈ 0,1–0,2 s/imagem × ~3.000 imagens ≈ 10–15 min.
- Pesos do DINOv2 ViT-B/14: ~350 MB de download — **liberar disco antes** (3,8 GB livres medidos;
  ver `disk-space-hygiene` na memória do workspace) e cachear como `diag_seed_probe.py` já faz
  para o ConvNeXt (`.npz` por `sample_id`, reusa se IDs baterem).

### Onde vive

`scripts/research/e17_entity_probe.py` (a escrever), reusando `diag_seed_probe.oof`/`embed`
(adaptado para DINOv2 + pair crop) e `crops.detect_boxes`/`union_with_pad` diretamente — sem
reimplementar nenhum dos dois. Resultados gerados re-emitidos entre marcadores
`<!-- E17:RESULTS -->` acima deste texto, convenção idêntica a `e0_ksweep_whr.py`/
`e10_terminal_hazard.py`. `ponytail:` o script é um cell de pesquisa read-only sobre artefatos
locais do Harmony — nenhuma escrita em `matches`/`graphs`/corpus público, nenhuma chamada de rede
além do download único dos pesos DINOv2.

## 5. Pré-requisito real para C/D e para produto

Nenhum braço além de B roda sem uma tarefa de **anotação caixa+papel**: ~300–500 frames cobrindo
as 5 famílias, anotados no admin (reusar a tela de Auditoria já existente — uma caixa por atleta +
um chip de papel top/bottom). Esse dataset é ao mesmo tempo:

- o gabarito de identidade oráculo que a proposta pede antes de qualquer tracking, e
- o mesmo gabarito que já falta para o gate de role-resolved ≥70% (inventário §6, Harmony4D).

Sem essa anotação, "identidade oráculo" na proposta é ficção — não existe hoje em nenhum
manifesto (§3). Este prereg não a inclui; é o próximo cell condicional a E17 passar (death rule 3).

## 6. O que fica explicitamente fora (YAGNI)

- Treinar as 211 classes finas do vocabulário completo — a pergunta de E17 é família (5), não
  técnica.
- SAM2 como tracker — candidato razoável *depois* que um braço single-frame bate o piso e a
  anotação de identidade existir; sem isso não há o que rastrear com garantia de quem é quem.
- Transformer temporal / Viterbi sobre a timeline — pressupõe uma classificação por frame
  confiável primeiro; E17 testa exatamente se essa confiabilidade existe.
- Pipeline end-to-end ou nova stack de treino — este cell é um probe congelado + classificador
  linear, mesma forma de todo experimento neste repo (research-methodology §1, "pure-function-
  first").

**Regra de privacidade reafirmada:** `data/video/owner/` (rounds do dono, reference-owner) nunca
entra em treino ou neste probe — só serve como benchmark do leitor automático **para o próprio
dono** (CLAUDE.md raiz, "Public vs Private Data"; `CLAUDE.md` deste repo, bloco "Reference-owner
accounts"). Este cell usa exclusivamente o corpus público Harmony/finetune, já auditado como
`WEAK_ANALYTICS` público, nunca dado privado.

## 7. Literatura

- **DINOv2** — Oquab et al., 2023, arXiv:2304.07193 ("DINOv2: Learning Robust Visual Features
  without Supervision"). Por que importa aqui: é exatamente a peça que a proposta pede —
  features densas congeladas, treinadas sem rótulo, historicamente lineares o bastante para uma
  cabeça leve (LogReg/kNN) separar classes downstream sem fine-tune. `diag_seed_probe.py` já prova
  a forma do experimento com um backbone congelado mais fraco (ConvNeXt ImageNet); E17 troca só o
  backbone e adiciona o pair crop.
- **DINOv3** — Siméoni et al., 2025, arXiv:2508.10104 *(verificar id — não confirmado por fetch
  neste pass; citado pela proposta, não usável aqui por estar gated no HF, §3)*. Por que importaria:
  seria o upgrade natural de DINOv2 se/quando o gate de licença for resolvido — não bloqueia E17,
  que já é decidível com DINOv2 ungated.
- **SAM 2** — Ravi et al., 2024, arXiv:2408.00714 ("SAM 2: Segment Anything in Images and
  Videos"). Por que importa (mas não para este cell): candidato natural a tracker por propagação
  de máscara pixel-a-pixel entre frames, o que resolveria a fusão de corpos emaranhados que o
  detector de pessoa genérico erra hoje (§3) — mas só é um trabalho com sentido depois que braço
  single-frame + identidade oráculo existirem; ver §6.

---

## Resumo pronto para o dashboard

- **Proposta do dono avaliada e reduzida a um cell decidível**: dos 4 braços possíveis (pose/pair/
  ambos/ambos+pair) e da identidade oráculo pedida, só o braço B (pair crop + DINOv2) tem dado
  suficiente para rodar hoje — os outros dependem de anotação caixa+papel que não existe em
  nenhum manifesto.
- **E17 pré-registrado, não executado**: DINOv2 ViT-B/14 sobre pair crop (YOLO top-2 + union+15%)
  vs. maioria (34,4%) e vs. frame inteiro (ConvNeXt), GroupKFold por luta, 5 famílias do Harmony
  v3, margem ≥+0,05 macro-F1 e p≤0,05 por permutação; 3 death rules já escritas antes do número.
- **Bloqueio real fica exposto**: sem anotação caixa+papel (~300-500 frames), não há identidade
  oráculo nem braços C/D nem destrava o gate de tracking ≥70% do Harmony4D — é o mesmo gargalo de
  dado, não de arquitetura, que o inventário de 2026-09-18 já apontava.
