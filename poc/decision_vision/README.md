
# Decision Vision POC — GrapplingArc

POC separada. Não altera `cv/`, `analysis/` nem o Decision Space de produção.

Objetivo: testar se frames de uma luta conseguem reconhecer o **critério visual
do parceiro** em uma janela observada:

```text
USUÁRIO A -> PARCEIRO C -> USUÁRIO B
```

A POC usa a estrutura do GrapplingArcAnalytics e os dados já existentes:

- `db.models.Match.timeline` / `Match.sequence`
- `Match.athlete_a_id`, `athlete_b_id`, `video_url`
- `TechniqueNode.node_key`, `taxonomy_id`
- `docs/taxonomy.json`
- `DATABASE_URL` por `db.base`

Ela é **read-only no banco**. Só grava em `data/cv_decision_poc/`.

## Ideia central

O mesmo critério é treinado em múltiplos níveis:

```text
Ashi Barai    leaf / específico
    ↓
Ashi Waza     family / taxonomy_id
    ↓
Takedown      category / raiz
```

O modelo tem um backbone visual compartilhado e três heads. Isso permite que o
sistema seja específico quando há evidência e recue para uma abstração mais
ampla quando a imagem não suporta o detalhe.

A CV responde:

> o que o parceiro provavelmente fez?

O Decision Space responde depois:

> essa observação C realmente altera a escolha B do usuário, comparada ao acaso,
> e em qual nível de abstração isso é estável?

---

## 1. Instalação dentro do Analytics

Copie a pasta para:

```text
GrapplingArcAnalytics/
  poc/
    decision_vision/
```

Da raiz do repo:

```bash
uv sync --extra cv --extra postgres
```

O repo já tem OpenCV no core e PyTorch/TorchVision no extra `cv`; o extra
`postgres` fornece SQLAlchemy/psycopg.

Use o mesmo `.env` / `DATABASE_URL` do Analytics.

---

## 2. Ver quais lutas do banco servem

```bash
uv run --extra cv --extra postgres \
  python poc/decision_vision/inspect_db.py
```

Prefira lutas `final` com `video_url` e muitos eventos com `ts`.

A POC **não baixa vídeo**. Você associa o `match_id` a um arquivo local da luta.

---

## 3. Extrair frames

Uma luta:

```bash
uv run --extra cv --extra postgres \
  python poc/decision_vision/extract_frames.py \
  --match-id <MATCH_UUID> \
  --video /caminho/luta.mp4 \
  --focus-athlete-id <ATHLETE_UUID>
```

Saída:

```text
data/cv_decision_poc/
  images/
  manifest.csv
```

Por padrão, cada critério recebe 5 frames numa janela de 1,2 s:

```text
-0.60
-0.30
 0.00
+0.30
+0.60
```

Pode aumentar:

```bash
--frames-per-event 7 --window-seconds 1.8
```

Batch:

```bash
uv run --extra cv --extra postgres \
  python poc/decision_vision/extract_frames.py \
  --video-map poc/decision_vision/video-map.csv
```

Formato do CSV:

```csv
match_id,video_path,focus_athlete_id
<uuid>,/videos/luta1.mp4,<athlete uuid>
<uuid>,/videos/luta2.mp4,<athlete uuid>
```

---

## 4. Como a janela é criada

A timeline é normalizada para `you | partner | neutral`.

Exemplo:

```text
YOU      Single Leg
PARTNER  Whizzer
PARTNER  Sprawl
YOU      Rear Body Lock
```

Gera dois candidatos visuais no mesmo `bundle_id`:

```text
Single Leg -> Whizzer -> Rear Body Lock
Single Leg -> Sprawl  -> Rear Body Lock
```

O manifest guarda:

```text
match_id
focus_athlete_id
partner_athlete_id

source_key
criterion_label
criterion_ts
response_key

leaf_label
family_label
category_label
taxonomy_path

bundle_id
bundle_index
bundle_size

frame_ts
frame_offset
```

Isso preserva o contexto necessário para a análise estatística posterior.

---

## 5. Treinar

```bash
uv run --extra cv \
  python poc/decision_vision/train.py \
  --manifest data/cv_decision_poc/manifest.csv \
  --epochs 12 \
  --batch-size 32
```

Modelo:

```text
frame
  ↓
ImageNet ResNet18
  ├─ leaf
  ├─ family
  └─ category
```

Pesos da loss:

```text
leaf      1.00
family    0.65
category  0.35
```

Classes raras no nível específico são ignoradas nesse head, mas continuam
treinando os heads mais abstratos.

Defaults:

```text
--min-samples 8
--min-matches 2
```

### Split correto

Treino/validação é agrupado por `match_id`.

Frames próximos da mesma luta nunca devem estar nos dois lados; isso geraria
leakage enorme.

Com apenas uma luta, o treino falha de propósito. Para testar apenas o pipeline:

```bash
--allow-frame-split --min-matches 1
```

Não use as métricas desse modo como evidência de generalização.

Artifacts:

```text
data/cv_decision_poc/model/
  criterion_resnet18.pt
  training_report.json
```

---

## 6. Inferência em múltiplos frames

Use vários frames do mesmo critério:

```bash
uv run --extra cv \
  python poc/decision_vision/predict.py \
  --model data/cv_decision_poc/model/criterion_resnet18.pt \
  --images \
    frame_1.jpg \
    frame_2.jpg \
    frame_3.jpg
```

A probabilidade é média entre os frames:

```json
{
  "heads": {
    "leaf": [
      {"label": "ashi-barai", "probability": 0.62}
    ],
    "family": [
      {"label": "ashi-waza", "probability": 0.86}
    ],
    "category": [
      {"label": "takedown", "probability": 0.97}
    ]
  }
}
```

Isso é exatamente o que o Decision Space precisa: a evidência pode ser fraca
no nível específico e forte no abstrato.

---

## 7. Integração futura com Decision Space

Não converta top-1 da CV diretamente em regra.

Para cada janela:

```text
A = nó anterior do usuário
C = distribuição visual do parceiro
B = nó seguinte do usuário
```

Agregue por nível:

```text
P(B | A)
P(B | A, C_leaf)
P(B | A, C_family)
P(B | A, C_category)
```

Depois calcule:

```text
lift = P(B | A,C) / P(B | A)
```

e valide com:

- ocorrência
- `match_count`
- `opponent_count`
- permutation test dentro de A
- bootstrap por luta
- ganho preditivo filho vs pai

Regra final:

```text
escolher o C mais específico que:
  tem suporte independente suficiente
  é estável
  supera o acaso
  acrescenta informação em relação ao pai

senão:
  recuar para o pai
```

Assim:

```text
Ashi Barai -> B   pouco suporte
Ashi Waza  -> B   estável
Takedown   -> B   estável mas genérico

=> critério = Ashi Waza
```

Ou:

```text
Ashi Barai -> B   estável e muito mais preditivo que Ashi Waza

=> critério = Ashi Barai
```

---

## 8. Relação com o CV já existente no Analytics

O CV existente é pose/posição:

```text
frame
 -> YOLOv8-pose
 -> COCO-17
 -> position classifier
 -> position timeline
```

Esta POC é outra hipótese:

```text
timestamp DB
 -> RGB frames
 -> criterion classifier
 -> leaf/family/category probabilities
```

Não substitui `cv/pose_estimate.py`, `cv/baseline_classifier.py`,
`cv/segmenter.py` etc.

Se funcionar, a versão futura pode combinar:

```text
RGB
+ pose
+ movimento temporal
+ priors do atleta
```

Mas a POC deve responder só:

> Frames em torno de um evento rotulado pelo banco recuperam o critério do
> parceiro em lutas não vistas?

---

## 9. Métrica de sucesso

Olhe separadamente:

```text
leaf macro-F1
family macro-F1
category macro-F1
```

Exemplo perfeitamente útil:

```text
leaf       0.48
family     0.70
category   0.84
```

Isso não significa falha. Significa que a realidade visual suporta melhor a
abstração `family/category` do que o detalhe exato.

É uma resposta diretamente útil para o Decision Space.

---

## 10. Limitação proposital

É um classificador de **frames**, não de ação temporal.

Pode funcionar bem para:

- estado/posição
- configuração de guarda
- controle
- família ampla de queda

Pode falhar no leaf para:

- direção do desequilíbrio
- foot sweep vs passo normal
- snapdown
- transição muito rápida

Se `category/family` funcionar e `leaf` não, o próximo POC natural é clip
temporal de 8–32 frames. Não force especificidade artificialmente.

---

## 11. Banco

READ:

```text
matches
technique_nodes
docs/taxonomy.json
```

WRITE:

```text
data/cv_decision_poc/*
```

Nenhum update/insert no Supabase/Postgres.
