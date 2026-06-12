# Hermes AlphaHunt Project Update Research

Hermes supports two AlphaHunt research callback modes beyond initial project
creation:

- `project_update_research`
- `post_mortem_research`

Both modes are routed to Codex Spark through the existing AlphaHunt analysis
endpoint. They are read-only from Hermes: no secrets are read, no live
AlphaHunt APIs are called by Hermes, no notifications are sent, and no trade,
order, wager, signing, or funds-management action is performed.

## Project Update Research

AlphaHunt should send `analysis_mode: project_update_research` with a payload
that includes the available update context:

- previous thesis and assumptions
- watchpoints and thresholds
- latest source data
- explicit `data_gap`
- `breach` / `recheck_due` state
- any `hermes_update_research` task or artifact fields

Hermes must perform a semantic review. A watchpoint breach is evidence to
interpret, not an automatic thesis change. If the supplied data is insufficient,
Hermes records that under `data_gap` instead of fabricating a value.

The callback `stage_output` must include:

```yaml
status: ok
update_markdown: |
  <human-readable update note>
update_yaml:
  changed_evidence: []
  thesis_delta: "<semantic thesis change or no material change>"
  risk_delta: []
  invalidation_delta: []
  observables_update: []
  next_check_at: "2026-06-19T00:00:00+00:00"
  confidence: 0.5
  action_suggestion: "manual_review"
  data_gap: []
```

Allowed `action_suggestion` values are `no_change`, `observe`, `research`,
`manual_review`, `deprioritize`, and `archive`.

## Post-Mortem Research

AlphaHunt should send `analysis_mode: post_mortem_research` for closed
opportunities/projects. The payload should include the original thesis, final
outcome, triggered and missed watchpoints, source history, data gaps, and any
existing post-mortem notes.

The callback `stage_output` must include:

```yaml
status: ok
post_mortem_markdown: |
  <closed-item error review>
evolution_proposal:
  source_evolution: []
  rule_evolution: []
  threshold_evolution: []
  data_gap: []
  confidence: 0.5
  enforced: false
```

`enforced` must be `false`. These are source/rule/threshold evolution proposals
only; applying them is a separate operator-reviewed AlphaHunt process.

## Apply Boundary

`scripts/apply_project_update_notes.py` in AlphaHunt remains the apply gate:

- dry-run is the default
- live apply requires `--live` and `ALPHAHUNT_WRITE_API_KEY`
- writes are append-only update notes through the existing notes API
- low-risk batches are capped by `--limit`
- idempotency is based on the update-note fingerprint

Hermes produces research callbacks that can feed that path, but Hermes does not
perform the live apply step automatically.
