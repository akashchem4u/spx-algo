# Codex Review Channel

Use this folder as the shared handoff channel for agent reviews and follow-up fixes for `spx-algo`.

Conventions:
- Put dated review notes here as Markdown files.
- Keep one rolling summary per active issue when possible.
- Reference exact code paths and line numbers for anything actionable.
- Separate findings from recommendations.

Current active notes:
- `spx-algo-feedback.md`
- `next-agent-action-plan.md`
- `news-taxonomy-implementation-note.md`
- `status-audit-20260331.md`

Generated channels:
- `validation-artifacts/`
- `session-reviews/`

Workflow:
- run `python3 scripts/run_validation_review.py --profile local`
- for behavior-changing work, attach evidence notes:
  - `python3 scripts/run_validation_review.py --profile behavior --evidence-note "3-year walk-forward rerun completed manually" --write-session-review`
- session reviews should point to the generated validation artifact instead of relying on chat-only summaries
