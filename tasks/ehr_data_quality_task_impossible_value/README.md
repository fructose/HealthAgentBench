# `ehr_data_quality_task_impossible_value`

Version **2.0** of this EHR data-quality audit uses fixed, deliberately modified
MIMIC-IV Clinical Database Demo v2.2 tables. The standard prompt names the target error categories.
Its standard/hard counterpart uses identical data and labels.

Passing requires recall **1.0** over **30 injected clusters** and
row precision **at least 0.1**. Any accepted evidence row finds its cluster.
The labels include 30 injected-cluster entries and
110 reviewed source-error entries. Source errors earn precision
credit but are never required for recall. Further unlabeled anomalies may exist.

The agent image contains the eight fixed tables and analysis tools. Authoring
keys, raw source, source-review notes, and labels are excluded. The verifier runs
separately without network access. Both prompts use the same full-audit oracle,
with no hidden family selector.

See the [shared authoring documentation](../../scripts/ehr_data_quality/README.md)
for data attribution, regeneration, shortcut defenses, validation, and limitations.
The [artifact manifest](authoring/manifest.json) pins data and label checksums;
[source-review notes](authoring/source-review.csv) retain the adjudicated evidence.
Version 1 scores should not be compared directly with version 2.

```bash
uv run harbor run --path tasks/ehr_data_quality_task_impossible_value --agent oracle
```

## Verifier improvement row validity v1

This task now requires every submitted `(table, _row_id)` pair to identify an
existing row in the supplied dataset. Previously, the 30 required findings plus
270 nonexistent IDs (or unknown table names) passed with recall 1.0 and precision
0.1. These submissions now receive reward 0. The thresholds, label interpretation,
and duplicate handling are otherwise unchanged; valid unlabeled findings still
participate in precision exactly as before. The hard counterpart is unchanged.

The verifier streams an identifier-only index containing all 800,827 source rows,
not just gold rows. It checks exact strings, including leading zeros, within the
named table. Unknown references invalidate the whole submission; they are never
silently dropped to improve its precision. `test_flagged_rows.py` checks this
invariant as well as `scoring.evaluate`, because `test.sh` writes the final reward
from pytest's result.

`tests/row_ids.csv.gz` and `tests/row_manifest.json` are copied into the separate
verifier image only. The metadata pins the index checksum, per-table counts,
source-file checksums, and labels checksum. The index is generated from the
dataset checksums already recorded in `authoring/manifest.json`, and generation
refuses unpinned source changes or empty/duplicate identifiers. No patient values
or error labels are added to the index, and the submission format is unchanged.
The scorer never trusts files modified in the agent's working environment.

Verify or regenerate the index from the repository root:

```bash
python tasks/ehr_data_quality_task_impossible_value/authoring/build_row_manifest.py --check
python tasks/ehr_data_quality_task_impossible_value/authoring/build_row_manifest.py
```

Run the local regressions with Python 3.12 and pandas installed (no model key,
Docker, or pytest required):

```bash
python -m unittest discover \
  -s tasks/ehr_data_quality_task_impossible_value/tests \
  -p test_scoring_regression.py -v
```

The tests cover fabricated IDs and tables at the old passing threshold, exact
table membership, legitimate unlabeled findings, duplicates, precision-only
labels, malformed submissions, stale or corrupt index assets, and generation
determinism. They also regenerate the committed index and exercise the actual
30-cluster dataset. No LLM judge is needed: record existence is an exact property
of the source tables.

This is a deliberately bounded improvement. It does not establish why a real row
is erroneous, prevent memorized valid answers, tighten the 10% precision floor,
or change category eligibility for pre-existing labels. It adds about 7 MiB to
the verifier image. A stale index, typo, or differently normalized identifier can
now cause failure. Index/label corruption fails closed with a diagnostic and
`n_invalid_row_references = null`; an actual invalid submission reports its count.
Infrastructure failures should be distinguished from agent mistakes when using
these results for training. Dataset updates must regenerate and check the index.
The `row-validity-v1` verifier revision is recorded in task metadata and metrics;
its rewards should not be pooled silently with earlier runs.

### Validation performed

Local validation used Python 3.12 with the verifier image's pinned pandas 2.2.2,
NumPy 1.26.4, and pytest 8.4.1 dependencies. All 25 regression tests passed. The
reference audit produced 293 flagged rows; both scorers awarded reward 1, with
recall 1.0 and precision 0.1228668942. All six production grader checks passed for
that output after redirecting only the container-specific submission/log paths
to local paths. Each fabricated-record fixture failed the new validity check as
expected while passing the other five checks.

| Submission | Previous reward | New reward |
| --- | --- | --- |
| Reference audit | 1 | 1 |
| 30 required rows plus 270 nonexistent row IDs | 1 | 0 |
| 30 required rows plus 270 unknown-table references | 1 | 0 |
| 30 required rows plus 270 existing unlabeled rows | 1 | 1 |

Docker was not installed on the development host, so a full Harbor container run
and runtime isolation check remain to be performed. No model API calls were used.
