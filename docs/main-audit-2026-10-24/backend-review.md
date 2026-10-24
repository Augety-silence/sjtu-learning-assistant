# Backend calendar merge candidate: rejected

The initial candidate to merge calendar API reads was withdrawn after a counterexample showed it can drop an event. Production `academic_features.py` and its tests are restored to baseline.

The reproduction uses September 2026 with `now=2026-09-15T00:00:00Z`. Two mock Canvas records share `_event_identity == ("assignment", "77", "")`: one dated Sep 10 and one dated Oct 10. Production baseline filters each date window before deduplicating, returning one record in each view. The overlap-only merge candidate deduplicates the union before splitting; upcoming then loses the Oct 10 record. The full fields and outputs are in `backend-results.json` under `counterexample` (`outputs_identical: false`).

The experiment compares the current production `calendar_overview` directly against the candidate. The candidate retains the original overlap/touch guard and delegates distant intervals to production baseline. Results for unique synthetic assignment IDs preserve outputs: overlapping intervals use one candidate call, and distant intervals use two. These ordinary cases do not override the counterexample. The JSON contains raw timing, memory, call counts, cache labels, and method details; local mock measurements do not represent live Canvas performance.

Baseline revision: `3aa54d1a76fd8603dcc43eef5629eb5ae3cc37dd`.

Run from the repository root with:

```sh
SJTU_APP_SUPPORT_DIR=/tmp/sjtu-main-audit-20261024/backend-runtime \
SJTU_ARCHIVE_ROOT=/tmp/sjtu-main-audit-20261024/archive-private \
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
python \
scripts/experiments/backend_ablation.py \
> docs/main-audit-2026-10-24/backend-results.json
```
