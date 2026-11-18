"""Export normalized tables to Parquet/CSV."""

from pathlib import Path
from typing import Literal

import polars as pl
from loguru import logger

from .store import EXPORT_TABLES, Store

_DTYPES = {"INTEGER": pl.Int64, "TEXT": pl.String, "REAL": pl.Float64}


def export(store: Store, out: Path, fmt: Literal["parquet", "csv"] = "parquet") -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    existing = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    written = []
    for table in EXPORT_TABLES:
        if table not in existing:
            logger.warning(f"{table} missing; run `metallum parse` first")
            continue
        schema = {
            name: _DTYPES.get(decl.upper(), pl.String)
            for _, name, decl, *_ in store.db.execute(f"PRAGMA table_info({table})")
        }
        rows = store.db.execute(f"SELECT {', '.join(schema)} FROM {table}").fetchall()
        df = pl.DataFrame(rows, schema=schema, orient="row")
        path = out / f"{table}.{fmt}"
        if fmt == "parquet":
            df.write_parquet(path)
        else:
            df.write_csv(path)
        logger.info(f"{table}: {df.height} rows -> {path}")
        written.append(path)
    return written
