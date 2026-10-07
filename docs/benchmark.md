# amlcheck — Benchmark against MistTrack

> P15, methodology §14, D-093, D-092. Generated from `tests/benchmark/benchmark.json`
> with `uv run python scripts/benchmark.py report --write`. MistTrack's side is
> MistTrack Light, the free wallet risk assessment, looked up by Claude: its level
> (`low` or `risky`) and its risk rows. Checks never read them.

## Agreement

11 wallet(s): same level 9 (82%), 2 mismatched, 2 with gaps.

**AT-70: passed** (≥ 80% same level, every mismatch and gap explained; D-093).

| MistTrack ↓ · amlcheck → | low | moderate | high | severe |
|---|---|---|---|---|
| low | 2 | 0 | 0 | 0 |
| risky | 2 | 3 | 1 | 3 |

## Wallets

| Wallet | MistTrack | amlcheck | Same | MistTrack's risks (≥ 5%) | Ours | Gaps | Reason |
|---|---|---|---|---|---|---|---|
| BSC `0x0c1e52…ee1576` | low | 15 · low (NO_HITS) | yes | — | — | — | — |
| TRON `TA3941uF…vX86mz` | risky | 100 · severe (BLOCK) | yes | Illicit Activity direct 100% | Tether-frozen address: direct received 59.6% | — | — |
| TRON `TB5UPBTt…1CDXUY` | risky | 100 · severe (BLOCK) | yes | Illicit Activity direct 100% | Sanctioned entity: direct received 0.4%, indirect received 10.7%, direct sent 19.1%; Tether-frozen address: indirect received 3.5%, indirect sent 54.9%; Illicit activity: direct sent 7.9% (inferred) | — | — |
| TRON `TDpbgW7H…ZPBdNA` | risky | 47 · moderate (REVIEW) | yes | Sanctioned Entity indirect 14.86%; Illicit Activity indirect 9.51% | Sanctioned entity: direct received 7.8%; Illicit activity: indirect received 1.6% (inferred) | — | — |
| TRON `TJcS48zP…Pkcpn6` | risky | 0 · low (NO_HITS) | **no** | Sanctioned Entity indirect 11.11% | — | Sanctioned Entity indirect 11.11% | method: A 26 USDT wallet. Before 2.0.1 the 100 USDT floor left its trace empty (D-096, fixed). Now its money is traced to two busy services, where our traces stop because funds mix: one has no risk data on MistTrack Light, the other is a large service MistTrack itself rates Low (0.35% direct sanctioned). MistTrack follows money through them and shows 11.11% indirect sanctioned. |
| TRON `TKHuVq1o…6fQgFs` | risky | 0 · low (REVIEW) | **no** | Sanctioned Entity indirect 5.76% | Tether-frozen address: direct sent 0.0% | Sanctioned Entity indirect 5.76% | method: Tether's treasury issues to and redeems from exchanges. Our traces stop at exchanges, where funds mix, so none of its money reaches a sanctioned address in our trace; MistTrack Light follows money through them (5.76% indirect sanctioned) and calls any risky funds Risky. We still give REVIEW: one transfer with a Tether-frozen wallet, 0.0001% of its volume, so the score is 0. |
| TRON `TMUS8vwp…x9D3zi` | risky | 100 · severe (BLOCK) | yes | Illicit Activity direct 22.27% | Sanctioned entity: direct received 37.1%, direct sent 10.8%; Illicit activity: direct sent 34.7% (inferred) | — | — |
| TRON `TSArbmMU…VF2Rku` | risky | 41 · moderate (REVIEW) | yes | Illicit Activity direct 44.09%; Illicit Activity indirect 12.67%; Sanctioned Entity indirect 8.6% | Sanctioned entity: direct received 5.6%, indirect received 5.7%; Illicit activity: indirect received 2.2% (inferred) | — | — |
| TRON `TU4vEruv…r7Pvaa` | low | ≥ 15 · low (INCOMPLETE) | yes | — | — | — | — |
| TRON `TVvWhZyL…LeSsWP` | risky | 53 · moderate (REVIEW) | yes | Illicit Activity direct 52.07%; Illicit Activity indirect 26.66%; Sanctioned Entity indirect 15.91% | Sanctioned entity: indirect received 47.6%; Illicit activity: indirect received 13.5% (inferred) | — | — |
| TRON `TWkaZR9D…TfRUv6` | risky | 75 · high (REVIEW) | yes | Sanctioned Entity indirect 11.01%; Illicit Activity indirect 10.89%; Illicit Activity direct 5% | Sanctioned entity: direct received 6.4%, indirect received 16.2%; Tether-frozen address: direct received 0.4%, indirect received 8.8%, indirect sent 14.9%; Illicit activity: indirect received 2.5% (inferred) | — | — |
