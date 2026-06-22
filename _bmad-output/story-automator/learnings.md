# Story Automator — Learnings (orchestration-1, 2026-06-12 → 2026-06-14)

## Run summary
- 40/40 stories built (Epics 1–5), 5 epic retrospectives, ~44 commits.
- Uniform agent: claude (no fallback). max_parallel configured 3 but loop ran sequentially (heavy inter-story dependencies).

## What worked
- Sequential create→dev→automate→review→commit per story; sprint-status.yaml as single source of truth.
- Foreground watcher loops keyed on sprint-status transitions (review/done) were far more reliable & turn-efficient than monitor-session, whose idle heuristic mis-fired against the persistent task-tracker widget.
- Per-story secret guard before every commit caught .env files for inspection.

## Recurring patterns / issues
- Transient "API socket closed" errors mid-session (create 4.9, dev 5.1): recovered by nudging the idle session to continue (preserves research context) or fresh respawn.
- Claude session usage limit hit twice (dev 4.7 ~01:20 reset; review 5.7 ~11:20 reset): waited for reset / re-ran or nudged to finalize. No data lost.
- dev-story sometimes paused at interactive menus despite #YOLO when a decision touched a tracked file (1.1 .env masking) — orchestrator answered via tmux send-keys.
- Stale test-count in story docs recurred; code-review re-measured each time.
- Epic rollup flag (epic-N:) left "in-progress" by some retros though all stories + retro done — cosmetic; orchestrator never edits sprint-status.yaml directly.

## Repo hygiene decision (one-time)
- Working tree started with ~5055 uncommitted files (mostly .claude/.codex/.agents AI tooling). Per user choice: gitignored + untracked those dirs, baseline commit absorbed WIP + Story 1.1; stories 1.2→5.11 got clean per-story commits.
- deploy/env/*.env are tracked placeholder templates (refs only) — verified no plaintext secrets committed.

## Recommendations
- Install git-lfs (post-commit hook warned each commit; LFS-tracked binaries may need attention).
- Operator: fill dry-run baseline placeholders (docs/qa/dry-run-baseline-*) with a logged-in Chrome before Epic 3 cutover (retro action A4).
- Consider gitignoring _bmad-output/story-automator/ orchestration state so it stops appearing in story commits.
