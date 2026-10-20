# Decision Engine development checkpoint — 2026-10-19

## Latest evidence

v0.3.20 full development completed on owner's Windows PC:
- 12/12 Engine seat jobs completed, 228 Engine decisions
- 95 CONFIRMED_EDGE
- 80 PRACTICAL_TIE_MAX_BUDGET
- 53 UNCONFIRMED_EDGE_MAX_BUDGET (23.25%)
- Engine mean advantage over B2 +1.37 pp
- all four preregistered development performance conditions passed
- **15% unconfirmed-edge criterion failed**; no unseen data opened

Source: original `v0320-development-results-bundle.zip`, SHA-256
`b1764ab566bcbdd9751154c3b3f8352e2287f6322175e1f8e61b3d4aa739022e`.

## Independently audited v0.3.21 shadow (development only)

The owner completed a 1,024-rollout replay of the 53 source-unconfirmed decision states on Windows, preserving exact earlier 1→512 states.

Of the 53:
- 29 gained confirmed edges
- 3 became practical ties
- 21 remained unconfirmed
- 1 leading-player identity changed and became a tie

A hypothetical fixed-source reclassification implies 21/228 = 9.21% unconfirmed, **NOT** a valid full-draft gate pass. Changes to picks can change subsequent states.

The original shadow jobs all completed with return code zero, and source results/IDs/gaps/status transitions/checksums independently verified.

Audit: `docs/decision-engine/V0321-SHADOW-INDEPENDENT-AUDIT.md`

## v0.3.21 full development code branch

Branch: `dev/v0321-full-development`.

New **frozen** protocol: `historical/protocol-v0.3.21-development-freeze.json`.

Only the adaptive maximum budget changes to 1024 (with the appended doubling checkpoint); scoring M0.3, epsilon 0.10, two-checkpoint confirmation, B3 continuation, candidate broad-pool/pruning, baseline opponents and existing gate criteria stay unchanged.

The full 12-seat local research path now uses:
1. Phase A — all B1/B2/B3/Engine drafts and immutable commits **without loading realized outcomes**
2. Restart certificate — resume 2022-23 seat 1 from its 18th completed Engine pick, require byte-identical commitment
3. Phase B — only then load realized outcomes in separate processes to score immutable completed rosters
4. Aggregate full 12-seat development results and apply the unchanged gate
5. Bundle both phases, SHA-256 checksums, logs and gate report

Main owner launcher: `RUN-V0321-FULL.cmd`.

Expected output after user runs: `historical/local_runs/v0321-full-development/v0321-full-development-results-bundle.zip`.

Large compute must remain **local** on the owner's PC. Do not dispatch a GitHub-hosted Monte Carlo matrix.

## Important current limitation

**The full v0.3.21 development run has NOT been executed yet.**

The new launcher performs local syntax/contract checks and frozen v0.3.19 calibration certification before heavy work. Real restart-equivalence certification is enforced at the phase boundary after Phase A and before historical scoring.

A successful results bundle still requires independent post-run audit and owner confirmation of integrity before 2024–25 validation may be opened.

2024-25 validation: UNTOUCHED.
2025-26 holdout: UNTOUCHED.

## Next input required

Owner downloads the `dev/v0321-full-development` branch as a ZIP, extracts it, double-clicks `RUN-V0321-FULL.cmd`, then uploads the resulting `v0321-full-development-results-bundle.zip` in this chat.

If the launcher stops, share the error/logs. Rerunning the same folder resumes valid completed commits/checkpoints; replacing the repo while work is in progress invalidates provenance.
