"""Mtime-keyed parquet cache for parsed JSONL rows.

For each source `*.jsonl` file we store one parquet shard named with a
hash of the source path. A small `manifest.json` records each shard's
source path, its mtime when last parsed, and the resulting row count.
On a re-run we re-parse only those source files whose mtime has changed
since the last cached parse.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from . import parser

# Canonical pyarrow schema applied to every shard so they concatenate
# cleanly even when individual shards have all-null columns.
_SCHEMA = pa.schema(
    [
        ("ts", pa.string()),
        ("session_id", pa.string()),
        ("message_id", pa.string()),
        ("request_id", pa.string()),
        ("project_cwd", pa.string()),
        ("project_name", pa.string()),
        ("git_branch", pa.string()),
        ("model", pa.string()),
        ("service_tier", pa.string()),
        ("input_tokens", pa.int64()),
        ("cache_creation_5m", pa.int64()),
        ("cache_creation_1h", pa.int64()),
        ("cache_read", pa.int64()),
        ("output_tokens", pa.int64()),
        ("web_search_count", pa.int64()),
        ("web_fetch_count", pa.int64()),
        ("tool_calls", pa.list_(pa.string())),
        ("uuid", pa.string()),
        ("version", pa.string()),
        ("source_file", pa.string()),
        ("session_type", pa.string()),
        ("hook_parent_name", pa.string()),
    ]
)
SCHEMA_COLUMNS: list[str] = [f.name for f in _SCHEMA]


log = logging.getLogger(__name__)

MANIFEST_VERSION = 2


@dataclass
class _ShardInfo:
    source_path: str
    mtime: float
    shard: str
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "mtime": self.mtime,
            "shard": self.shard,
            "row_count": self.row_count,
        }


class Cache:
    """An on-disk parquet cache keyed by `(source_path, mtime)`."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / "manifest.json"
        self.combined_path = self.cache_dir / "combined.parquet"
        self._manifest: dict[str, _ShardInfo] = self._load_manifest()

    def _load_manifest(self) -> dict[str, _ShardInfo]:
        if not self.manifest_path.exists():
            return {}
        data = json.loads(self.manifest_path.read_text())
        if data.get("version") != MANIFEST_VERSION:
            log.warning("cache manifest version mismatch - ignoring cache")
            return {}
        return {
            entry["source_path"]: _ShardInfo(**entry)
            for entry in data.get("shards", [])
        }

    def _save_manifest(self) -> None:
        payload = {
            "version": MANIFEST_VERSION,
            "shards": [info.to_dict() for info in self._manifest.values()],
        }
        self.manifest_path.write_text(json.dumps(payload, indent=2))

    def _shard_path(self, source_path: str) -> Path:
        digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.parquet"

    def load_or_parse(self, root: str | Path) -> pd.DataFrame:
        """Return a DataFrame of all rows parsed from JSONLs under `root`.

        Re-parses only those files whose mtime has changed since the
        last cached parse, then rebuilds a single `combined.parquet`
        for fast hot-path reads.
        """
        root = Path(root)
        seen: set[str] = set()
        any_change = False
        for source in sorted(root.rglob("*.jsonl")):
            source_str = str(source)
            seen.add(source_str)
            mtime = source.stat().st_mtime
            existing = self._manifest.get(source_str)
            shard_path = self._shard_path(source_str)
            if (
                existing is not None
                and existing.mtime == mtime
                and shard_path.exists()
            ):
                continue
            any_change = True
            rows = list(parser.parse_file(source))
            if rows:
                table = _rows_to_table(rows)
                import pyarrow.parquet as pq
                pq.write_table(table, shard_path)
                row_count = table.num_rows
            else:
                if shard_path.exists():
                    shard_path.unlink()
                row_count = 0
            self._manifest[source_str] = _ShardInfo(
                source_path=source_str,
                mtime=mtime,
                shard=shard_path.name,
                row_count=row_count,
            )
        # Drop manifest entries for files that no longer exist.
        for stale in [p for p in self._manifest if p not in seen]:
            stale_shard = self._shard_path(stale)
            if stale_shard.exists():
                stale_shard.unlink()
            del self._manifest[stale]
            any_change = True
        self._save_manifest()
        if any_change or not self.combined_path.exists():
            self._rebuild_combined()
        if not self.combined_path.exists():
            return pd.DataFrame()
        return pd.read_parquet(self.combined_path)

    def _rebuild_combined(self) -> None:
        shards = [
            str(self.cache_dir / info.shard)
            for info in self._manifest.values()
            if info.row_count > 0 and (self.cache_dir / info.shard).exists()
        ]
        if not shards:
            if self.combined_path.exists():
                self.combined_path.unlink()
            return
        # pyarrow.dataset is much faster than read_parquet-in-a-loop for
        # thousands of small shards because it shares schema/metadata
        # work across files. We enforce the canonical schema so shards
        # with all-null columns don't break the concatenation.
        import pyarrow.dataset as ds
        import pyarrow.parquet as pq

        table = ds.dataset(shards, format="parquet", schema=_SCHEMA).to_table()
        # Dedup on message_id: the same assistant message can appear in
        # multiple project folders (e.g. when Claude Code is invoked from
        # different cwds in the same session). ccusage does the same.
        df = table.to_pandas()
        before = len(df)
        df = df.drop_duplicates(subset="message_id", keep="first")
        log.info("dedup combined frame: %d -> %d rows", before, len(df))
        pq.write_table(pa.Table.from_pandas(df, schema=_SCHEMA, preserve_index=False), self.combined_path)


def _rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    """Convert a list of parsed rows into a pyarrow Table with the canonical schema."""
    columns: dict[str, list[Any]] = {name: [] for name in SCHEMA_COLUMNS}
    for row in rows:
        for name in SCHEMA_COLUMNS:
            columns[name].append(row.get(name))
    return pa.table(columns, schema=_SCHEMA)
