"""Build the verifier-only row index from the pinned, immutable task inputs.

Run this script after intentionally updating the dataset and its authoring
manifest. ``--check`` verifies the committed index without modifying files.
The index contains identifiers only, never values or error labels.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

TABLES = (
    "admissions",
    "chartevents",
    "d_items",
    "d_labitems",
    "icustays",
    "labevents",
    "patients",
    "prescriptions",
)
TASK_DIR = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def build_manifest(task_dir: Path, *, check: bool = False) -> dict:
    pinned = json.loads((task_dir / "authoring/manifest.json").read_text())
    data_dir = task_dir / "environment/data/csv"
    data_hashes = {}
    row_counts = {}
    index_buffer = io.BytesIO()
    with (
        gzip.GzipFile(filename="", fileobj=index_buffer, mode="wb", mtime=0) as zipped,
        io.TextIOWrapper(zipped, encoding="utf-8", newline="") as text,
    ):
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow(["table", "_row_id"])
        for table in TABLES:
            filename = f"{table}.csv.gz"
            path = data_dir / filename
            actual_hash = sha256(path)
            if actual_hash != pinned["data_sha256"][filename]:
                raise ValueError(
                    f"Dataset checksum differs from authoring manifest: {table}"
                )
            data_hashes[filename] = actual_hash
            ids: set[str] = set()
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                if "_row_id" not in (reader.fieldnames or []):
                    raise ValueError(f"Missing _row_id column: {table}")
                for row in reader:
                    row_id = row["_row_id"]
                    if not row_id or row_id in ids:
                        raise ValueError(f"Empty or duplicate _row_id: {table}")
                    ids.add(row_id)
            row_counts[table] = len(ids)
            writer.writerows((table, row_id) for row_id in sorted(ids))

    labels_hash = sha256(task_dir / "tests/labels.csv")
    if labels_hash != pinned["labels_sha256"]:
        raise ValueError("Labels checksum differs from authoring manifest")
    index_bytes = index_buffer.getvalue()
    metadata = {
        "schema_version": 1,
        "dataset_version": pinned["dataset_version"],
        "data_sha256": data_hashes,
        "labels_sha256": labels_hash,
        "row_counts": row_counts,
        "row_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
    }
    metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    index_path = task_dir / "tests/row_ids.csv.gz"
    metadata_path = task_dir / "tests/row_manifest.json"
    if check:
        if (
            index_path.read_bytes() != index_bytes
            or metadata_path.read_bytes() != metadata_bytes
        ):
            raise ValueError(
                "Row index is stale; regenerate it from the pinned task inputs"
            )
    else:
        index_path.write_bytes(index_bytes)
        metadata_path.write_bytes(metadata_bytes)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    metadata = build_manifest(TASK_DIR, check=args.check)
    print(
        f"{'Verified' if args.check else 'Generated'} {sum(metadata['row_counts'].values())} row references"
    )


if __name__ == "__main__":
    main()
