# Analysis Skill

Statistical analysis patterns for BJJ competition data.

## When to Use

When implementing ELO calibration, technique frequency analysis, benchmarking, or similarity matching.

## Patterns

### ELO Calibration
```python
def compute_adcc_elo(matches: pd.DataFrame) -> pd.DataFrame:
    """Compute ELO ratings for ADCC fighters.
    
    Uses felixgnwn K-factor weighting: BASE_K=40, stage/win_type multipliers.
    """
```

### Technique Frequency
```python
def position_frequency(parquet_path: str) -> pd.DataFrame:
    """Heatmap of position frequency by year/weight/sex."""

def submission_rates(parquet_path: str) -> pd.DataFrame:
    """Finish rate by position (e.g., armbar from mount)."""

def transition_probability(parquet_path: str) -> pd.DataFrame:
    """Markov transition matrix between positions."""
```

### Benchmarking
```python
def benchmark_user(user_bundle: UserBundle, adcc_stats: pd.DataFrame) -> dict:
    """Compare user's signatures/position time/subs vs ADCC averages."""
```

### Similarity
```python
def find_similar_fighters(user_vector, fighter_vectors, top_n=5):
    """Cosine similarity to find most similar ADCC fighter."""
```

## Data Sources

- ADCC matches: `data/processed/adcc_historical.parquet`
- Fighter stats: `data/processed/adcc_fighters.parquet`
- User: `UserBundle.from_json(path_to_export)`
