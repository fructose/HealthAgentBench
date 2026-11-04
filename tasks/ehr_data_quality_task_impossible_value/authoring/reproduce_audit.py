"""Reproduce the take-home audit against a pinned baseline and this checkout.

Developer-only probes deliberately use gold labels to isolate scorer behavior.
They do not demonstrate access to labels inside an agent container. Only aggregate
metrics are saved; submissions, oracle output, and logs live in a temporary folder.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BASELINE = "6f070d7b7c3380cbdf8a2981febfc1ea2560797a"
TASK = Path(__file__).resolve().parents[1]
ROOT = TASK.parents[1]
SCORER_PATH = "tasks/ehr_data_quality_task_impossible_value/tests/scoring.py"
SAVED_RESULTS = Path(__file__).with_name("audit_results.json")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reproduce() -> dict:
    try:
        baseline_source = subprocess.check_output(
            ["git", "show", f"{BASELINE}:{SCORER_PATH}"], cwd=ROOT
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Baseline commit unavailable. Use a full Git clone or fetch {BASELINE}."
        ) from exc
    current_source = (ROOT / SCORER_PATH).read_bytes()
    with tempfile.TemporaryDirectory(prefix="ehr-audit-") as scratch:
        temp = Path(scratch)
        baseline_file = temp / "baseline_scoring.py"
        baseline_file.write_bytes(baseline_source)
        old = load_module("audit_baseline_scoring", baseline_file)
        new = load_module("audit_current_scoring", ROOT / SCORER_PATH)
        with (TASK / "tests/labels.csv").open() as handle:
            labels = list(
                csv.DictReader(line for line in handle if not line.startswith("#"))
            )
        required = [
            (r["table"], r["row_id"])
            for r in labels
            if r["error_family"] != "preexisting"
        ]
        labeled = {(r["table"], r["row_id"]) for r in labels}
        off_scope = [
            (r["table"], r["row_id"])
            for r in labels
            if r["error_family"] == "preexisting"
            and r["error_subtype"] in {"sex_specific_conflict", "impossible_timing"}
        ]
        unlabeled = []
        # This trusted verifier index contains all source rows, not just gold.
        with gzip.open(TASK / "tests/row_ids.csv.gz", "rt", newline="") as handle:
            for row in csv.DictReader(handle):
                pair = (row["table"], row["_row_id"])
                if pair[0] == "chartevents" and pair not in labeled:
                    unlabeled.append(pair)
                if len(unlabeled) == 333:
                    break
        if len(required) != 30 or len(off_scope) != 7 or len(unlabeled) != 333:
            raise AssertionError(
                "Dataset changed; review fixtures and expected results."
            )
        cases = {
            "replay_gold_ids": (required, (1, 1)),
            "noisy_existing_rows": (required + unlabeled[:270], (1, 1)),
            "nonexistent_row_ids": (
                required + [("chartevents", f"nonexistent-{i}") for i in range(270)],
                (1, 0),
            ),
            "unknown_table_names": (
                required + [("not_a_table", str(i)) for i in range(270)],
                (1, 0),
            ),
            "without_off_scope_padding": (required + unlabeled, (0, 0)),
            "with_off_scope_padding": (required + off_scope + unlabeled, (1, 1)),
            "off_scope_rows_alone": (off_scope, (0, 0)),
            "one_required_cluster_missing": (required[:-1], (0, 0)),
        }
        oracle_file = temp / "oracle.csv"
        subprocess.run(
            [
                sys.executable,
                str(TASK / "solution/oracle.py"),
                "--data-dir",
                str(TASK / "environment/data/csv"),
                "--output",
                str(oracle_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        results = []
        for name in [*cases, "oracle"]:
            submission = temp / f"{name}.csv"
            if name == "oracle":
                submission = oracle_file
                expected = (1, 1)
            else:
                rows, expected = cases[name]
                with submission.open("w", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["table", "_row_id"])
                    writer.writerows(rows)
            result = {"case": name}
            for i, (version, module) in enumerate((("before", old), ("after", new))):
                logs = temp / name / version
                reward = module.evaluate(submission, TASK / "tests/labels.csv", logs)
                metrics = json.loads((logs / "metrics.json").read_text())
                if reward != expected[i]:
                    raise AssertionError((name, version, reward, expected[i]))
                keys = (
                    "reward",
                    "recall",
                    "precision",
                    "n_flagged_rows",
                    "n_clusters_caught",
                    "n_useful_flagged_rows",
                    "n_preexisting_flagged_rows",
                )
                result[version] = {key: metrics[key] for key in keys}
                result[version]["n_invalid_row_references"] = metrics.get(
                    "n_invalid_row_references"
                )
            results.append(result)
        return {
            "method": "Direct scorer calls and source-only reference audit; not a Harbor run",
            "fixtures": "Developer-constructed using gold labels; no claim of agent label access",
            "baseline_commit": BASELINE,
            "baseline_scorer_sha256": hashlib.sha256(baseline_source).hexdigest(),
            "current_scorer_sha256": hashlib.sha256(current_source).hexdigest(),
            "current_verifier_version": new.VERIFIER_VERSION,
            "cases": results,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--output", type=Path, help="Write aggregate JSON to this path")
    group.add_argument(
        "--check", action="store_true", help="Compare with committed aggregate results"
    )
    args = parser.parse_args()
    results = reproduce()
    if args.check:
        if results != json.loads(SAVED_RESULTS.read_text()):
            raise SystemExit(
                "Audit differs from committed results; inspect before updating."
            )
        print("All nine cases match the committed before/after audit results.")
    elif args.output:
        args.output.write_text(json.dumps(results, indent=2) + "\n")
        print(f"Saved nine cases to {args.output}")
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
