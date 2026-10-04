# Golden set (P11, methodology §10, D-066, D-067)

`golden.json`: addresses whose truth is known, used to measure the classifier and the score.

| Field | Meaning |
|---|---|
| `chain`, `address` | The address (normalised) |
| `expect` | What it is known to be: `verdict` (e.g. BLOCK), `type` (HUB, DEPOSIT, COLLECTOR, …), `clean` (must not score high or severe) |
| `source` | Where that knowledge comes from: our synced OFAC / Tether lists, an exchange's published proof-of-reserves list (URL), the owner, or an on-chain derivation |
| `note` | Free text (e.g. the OFAC entry's name) |
| `recorded` | Once recorded live: the classifier's inputs (`profile`, `context`) and the outcome (`types`, `verdict`, `score`, `band`, versions, `check_id`) |

- Expectations are test data only; nothing here is imported as a label (D-066).
- `scripts/golden.py` proposes, adds, imports, records and reports. `record` runs live checks: point
  `AMLCHECK_HOME` at a scratch copy of the database.
- CI replays the recorded classifier inputs through today's classifier and checks the P11 targets
  (`tests/unit/test_calibration.py::test_at58_golden_set_offline`). Score and verdict changes need a
  live re-record.
