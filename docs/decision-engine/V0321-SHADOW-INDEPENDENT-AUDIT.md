# v0.3.21 1024-rollout shadow audit (development only)

Original v0.3.20 full development: 228 decisions, 95 CONFIRMED_EDGE, 80 PRACTICAL_TIE_MAX_BUDGET, 53 UNCONFIRMED_EDGE_MAX_BUDGET.

v0.3.21 shadow: all 53 original unconfirmed decision states re-executed with the original 1→512 stages required to reproduce first, then extended to 1024 using the same frozen 0.10 threshold and the two-consecutive-separated-same-leader requirement.

| Original state at 512 | Confirmed at 1024 | Practical tie at 1024 | Still unconfirmed at 1024 |
|---|---:|---:|---:|
| Separated (30) | 29 | 0 | 1 |
| Unseparated (23) | 0 | 3 | 20 |
| **Total (53)** | **29** | **3** | **21** |

One leader switched: 2022-23 seat 1 at pick index 72 (round 13). The 1024 state was a practical tie. One previously separated 512 case lost separation at 1024: 2022-23 seat 6 at index 30 (round 6).

The hypothetical fixed-draft aggregate after reclassifying only these 53 states would be 124 confirmed, 83 ties, and 21 unconfirmed (9.21%). This is **NOT** a full development result or a development-gate pass.

Integrity findings: all 12 seat-level shadow jobs exited successfully. All 53 original unconfirmed identities matched once, and all original source leaders and terminal gaps matched the 512 results. The original v0.3.20 source bundle SHA-256 was `b1764ab566bcbdd9751154c3b3f8352e2287f6322175e1f8e61b3d4aa739022e`. The original 29 SHA-256 bundle checksums verified. The 1024 classifications were independently recomputed from the saved 512 and 1024 stage evidence. A fully independent re-execution of all numerical stages was not performed as part of this audit.

The new full v0.3.21 development study must recompute **all 228 draft positions**, including previously confirmed and tied decisions, because changed picks can change later draft states. It retains the old 15% unconfirmed ceiling and all v0.3.20 performance gates, but uses a new frozen 1024 maximum.

**No 2024-25 validation or 2025-26 holdout data was opened.**
