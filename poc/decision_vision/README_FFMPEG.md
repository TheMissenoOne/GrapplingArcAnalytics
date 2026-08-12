# Decision Vision POC v2 — FFmpeg, sem persistir frames

Esta revisão remove a necessidade de baixar a luta ou criar `images/*.jpg`.

## Novo fluxo

```text
Postgres / Match.timeline
       ↓
extract_frames.py
       ↓
manifest.csv
(URL + timestamp + labels, sem pixels)
       ↓
train.py
       ↓
yt-dlp resolve URL (metadata only)
       ↓
FFmpeg -ss <ts> ... -f image2pipe pipe:1
       ↓
JPEG bytes em RAM
       ↓
PIL / augmentation
       ↓
ResNet18
       ↓
bytes liberados quando o processo termina
```

Persistem apenas:

```text
manifest.csv
criterion_resnet18.pt
training_report.json
```

Não persistem:

```text
vídeo
frames
thumbnails
cache de imagens
```

## Por que yt-dlp ainda aparece?

FFmpeg consegue abrir uma URL direta de mídia, mas uma URL de página do YouTube
não é o stream de vídeo. Para `youtube.com/watch?...`, `FrameStream` chama
`yt-dlp --skip-download --dump-single-json` apenas para obter a URL de mídia e
os headers atuais. O frame em si é decodificado pelo FFmpeg e sai em stdout.

Para uma URL direta `.mp4`, `.m3u8`, `.webm`, etc., yt-dlp nem é chamado.

## 1. Dependências

Além dos extras que você já instalou:

```bash
uv sync --extra cv --extra postgres --extra dev
```

garanta os CLIs:

```bash
ffmpeg -version
yt-dlp --version
```

Se `yt-dlp` estiver apenas no ambiente Python, use/adicione a dependência do CLI
ao ambiente do projeto. O código chama o executável `yt-dlp` pelo PATH.

## 2. Copiar os arquivos

Substituir:

```text
poc/decision_vision/extract_frames.py
poc/decision_vision/train.py
poc/decision_vision/predict.py
```

Adicionar:

```text
poc/decision_vision/frame_stream.py
```

O `common.py`, `inspect_db.py` e testes existentes podem permanecer.

Os imports entre módulos usam o estilo de pacote (`from decision_vision.common import ...`),
então os comandos rodam da raiz do Analytics com `PYTHONPATH=poc`. Alternativa equivalente:
`uv run python poc/decision_vision/train.py ...` (script mode, sem `PYTHONPATH`).

## 3. Gerar manifest de uma luta

Não precisa mais de `--video`.

```bash
PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.extract_frames \
  --match-id <MATCH_UUID> \
  --focus-athlete-id <ATHLETE_UUID>
```

Ele lê `Match.video_url` do banco.

Saída:

```text
data/cv_decision_poc/manifest.csv
```

Exemplo de linha:

```text
sample_id
match_id
source_url=https://www.youtube.com/watch?v=...
frame_ts=123.45
source_key=single-leg
leaf_label=whizzer
family_label=arm-control
category_label=control
response_key=rear-body-lock
```

Nenhum frame é buscado nessa etapa; o arquivo é só o plano de amostragem.

### Várias lutas

`match-map.csv`:

```csv
match_id,focus_athlete_id,source_url
<match 1>,<athlete>,
<match 2>,<athlete>,
```

`source_url` vazio = usar `Match.video_url`.

```bash
PYTHONPATH=poc uv run --extra cv --extra postgres \
  python -m decision_vision.extract_frames \
  --match-map poc/decision_vision/match-map.csv
```

## 4. Treinar sem salvar frames

```bash
PYTHONPATH=poc uv run --extra cv \
  python -m decision_vision.train \
  --manifest data/cv_decision_poc/manifest.csv \
  --epochs 12 \
  --batch-size 32
```

Antes da epoch 1:

1. resolve cada `source_url` uma vez;
2. cada row chama FFmpeg no `frame_ts`;
3. FFmpeg produz 1 JPEG 320×320 em stdout;
4. os JPEGs ficam em `dict[str, bytes]` na RAM;
5. todas as epochs reutilizam esses bytes;
6. ao fim do processo, a RAM é liberada.

Isso evita:

```text
epoch 1 → buscar de novo
epoch 2 → buscar de novo
...
```

e ainda mantém zero frame persistente.

### Concorrência

Default:

```text
--fetch-workers 4
```

Reduza se a origem começar a falhar:

```bash
--fetch-workers 1
```

### Cookies, se realmente necessários

Opcional:

```bash
--cookies-from-browser firefox
```

Isso é repassado ao resolver yt-dlp; FFmpeg recebe apenas os headers/URL
resolvidos.

## 5. Inferência sem arquivo de imagem

Por `bundle_id`:

```bash
PYTHONPATH=poc uv run --extra cv \
  python -m decision_vision.predict \
  --model data/cv_decision_poc/model/criterion_resnet18.pt \
  --manifest data/cv_decision_poc/manifest.csv \
  --bundle-id '<match>:<source_index>:<response_index>'
```

Ou por sample IDs:

```bash
PYTHONPATH=poc uv run --extra cv \
  python -m decision_vision.predict \
  --model data/cv_decision_poc/model/criterion_resnet18.pt \
  --manifest data/cv_decision_poc/manifest.csv \
  --sample-id sample_a sample_b sample_c
```

O `predict.py` também chama FFmpeg e não cria arquivo de imagem.

## 6. FFmpeg usado por frame

Conceitualmente:

```bash
ffmpeg \
  -ss 123.45 \
  -i '<direct-media-url>' \
  -map 0:v:0 \
  -an \
  -frames:v 1 \
  -vf 'scale=320:320:force_original_aspect_ratio=decrease,pad=320:320:(ow-iw)/2:(oh-ih)/2:black' \
  -f image2pipe \
  -vcodec mjpeg \
  pipe:1
```

Portanto o filesystem não participa da aquisição do frame.

## 7. O que muda no significado de `extract_frames.py`

O nome foi mantido para não quebrar a POC, mas agora ele faz duas coisas:

```text
DB → decisão U/P/U
decisão → timestamps de frames planejados
```

A extração física dos pixels foi movida para `frame_stream.py` e ocorre dentro
do treino/inferência.

Uma evolução posterior pode renomear para `build_manifest.py`, mas eu não faria
isso durante a POC.

## 8. Segurança estatística continua igual

Nada muda no split:

```text
GroupShuffleSplit(match_id)
```

Frames da mesma luta não entram em treino e validação.

Também continuam os três heads:

```text
leaf
family
category
```

e os critérios de suporte por número de lutas.

## 9. Limite importante

"Sem download" aqui significa:

```text
nenhum arquivo de vídeo baixado
nenhum frame persistido
```

Existe transferência de bytes pela rede suficiente para o FFmpeg alcançar e
decodificar o frame solicitado. Não existe forma de produzir uma imagem remota
sem transferir os bytes necessários para decodificá-la.

## 10. POC / plataforma

Use esse fluxo apenas para vídeos que você tenha direito/permissão de processar.
Ele é uma POC de aquisição efêmera, não um mecanismo de cache ou redistribuição
de conteúdo.
