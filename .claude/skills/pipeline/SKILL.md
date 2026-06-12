# Data Pipeline Skill

Standard ETL pattern for Kaggle datasets.

## When to Use

When implementing a new dataset pipeline or modifying an existing one.

## Steps

1. Add `DatasetSpec` to `pipelines/registry.py`
2. Create `pipelines/{key}.py` inheriting `Pipeline`:
   - `clean()` — handle NaN, fix types, normalize strings
   - `normalize()` — map raw columns to unified schema
3. Register in `pipelines/__init__.py` exports
4. Test: `pytest tests/test_pipelines.py`
5. Optional: add notebook in `notebooks/`

## Template

```python
from pipelines.etl import Pipeline
from pipelines.registry import DATASETS

class MyPipeline(Pipeline):
    spec = DATASETS["my_key"]

    def clean(self, df):
        df = df.dropna(subset=["required_col"])
        df["col"] = pd.to_numeric(df["col"], errors="coerce")
        df["text_col"] = df["text_col"].str.strip()
        return df

    def normalize(self, df):
        return df.rename(columns={"raw_name": "standard_name"})
```

Usage:
```python
from pipelines import Pipeline

df = Pipeline.registry["my_key"].run()
print(df.shape)
```
