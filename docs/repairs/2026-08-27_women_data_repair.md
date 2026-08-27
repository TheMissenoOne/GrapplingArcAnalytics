# Reparo de dados femininos ADCC — 2026-08-27 (owner-runnable)

Auditoria read-only confirmou 4 defeitos (db-prober, findings completos no scratchpad da
sessão). Os aliases já estão commitados (`0cb4afa`). O que falta são as mutações de prod,
bloqueadas pelo classifier para agentes — rodar na ordem, de `GrapplingArcAnalytics/`, com
`set -a; source .env; set +a`.

## 1. Dedupe (Ffion "Final" + Sula 4 grafias, inclui "Gita")

Dry-run já validado: 2 clusters, 4 dup rows, 11 match refs repointed, 2 replays.

```bash
uv run python -m scripts.dedupe_athletes --dry-run   # conferir de novo
uv run python -m scripts.dedupe_athletes             # executa (AA-011 coberto: repointa actor_ids)
```

**Depois do dedupe, checar a duplicata de MATCH que sobra**: a final Ffion×Ste-Marie 2022
existe em 2 rows — a pobre (`1fee2704…`, ~5 eventos, veio da row "Ffion Davies Final") e a
rica (~32 eventos). O dry-run reportou `0 dup-pairings deleted`, então a pobre provavelmente
sobrevive ao merge. Verificar e, se ainda houver 2 rows do mesmo pairing ADCC 2022:

```sql
-- localizar as duas
SELECT id, event, jsonb_array_length(sequence) AS ev, winner_id
FROM matches
WHERE athlete_a_id IN (SELECT id FROM athletes WHERE name = 'Ffion Davies')
   OR athlete_b_id IN (SELECT id FROM athletes WHERE name = 'Ffion Davies');
-- deletar SÓ a pobre (esperado: 1fee2704…, ~5 eventos)
DELETE FROM matches WHERE id = '1fee2704-<completar-uuid-conferido>';
```

## 2. winner_id NULL (2 bouts ADCC 2024 fem, vencedora pública: Bia Mesquita)

```sql
UPDATE matches SET winner_id = '79dc768f-0fbf-4c28-85c0-14f05b6bd62c'
WHERE id = '8e622a87-3d50-4717-bb2b-14c118f831fe';   -- Lowenthal ("Gita") × Mesquita

UPDATE matches SET winner_id = '79dc768f-0fbf-4c28-85c0-14f05b6bd62c'
WHERE id = '09185fab-7eff-4227-82b3-dd49769e87b6';   -- Mesquita × Ste-Marie (absoluto)
```

## 3. Fantasma "Hailey Gettys" (2 matches, ambos 0 eventos; não existe no bracket real)

```bash
uv run python - <<'PY'
from db.base import db_session
from db.models import Athlete
from db.repository import remove_athlete, AthleteRemovalReason
with db_session() as s:
    g = s.get(Athlete, 'ab3b61f5-005b-4be3-b72e-0540899a9f68')
    if g:
        remove_athlete(g, s, reason=AthleteRemovalReason.INVALID_DATA)
        print('removed', g.name)
PY
```

## 4. Órfã "Ana Carolina" (0 matches; a real é "Ana Carolina Vieira", 6 matches)

```bash
uv run python - <<'PY'
from db.base import db_session
from db.models import Athlete
from db.repository import remove_athlete, AthleteRemovalReason
with db_session() as s:
    o = s.get(Athlete, 'c617a2da-e2a7-4dbd-aba5-84e0cbc9e6b1')
    if o:
        remove_athlete(o, s, reason=AthleteRemovalReason.INVALID_DATA)
        print('removed', o.name)
PY
```

## 5. Depois de tudo

```bash
uv run python -m scripts.prune_orphan_athlete_graphs        # invariante de órfãos
uv run python -m scripts.build_bracket_inputs --manifest data/scouting/adcc_women_65_extended.json --out data/scouting/adcc_women_65_extended_sequences.json
uv run python -m scripts.bracket_export                     # re-materializa data.json
# commit data.json no repo BracketAnalysis + sequences aqui
```

Replay de rating NÃO é necessário agora: os 2 winner_id afetam o replay global futuro, que
já está agendado como parte da Fase 4 da migração de taxonomia (replay completo + re-pin).
