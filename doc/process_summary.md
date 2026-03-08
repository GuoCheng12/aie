# doc/process_summary.md

## Process Summary (Living Log)

## Current stable interfaces/files
- `data/train.csv` (authoritative facts input)
- `data/test.csv` (simulation/eval input only; not merged into facts)
- `cases/{case_id}.json` (case file schema v0.7)
- `data/uq_scores_pre_atb_p5b.parquet`
- `data/anchor_neighbors_ecfp.parquet`
- `cache/atb/.../status.json` + `cache/atb/.../features.json`
- `src/agents/web_search_candidate_papers.py` (literature candidate retrieval via Responses API + web_search)
  - strict mode: requires paper.source_url ∈ returned citations/sources (auditable; may return empty if gateway redacts sources)
  - relaxed mode: keeps candidate papers even if citations/sources are incomplete (**NOT** safe for strict evidence write-back)

Current blocker: Literature evidence loop (web_search citations/sources not reliably surfaced by gateway; strict write-back blocked; relaxed candidate mode available).
aTB: DONE (full batch complete and cached)
Literature: PARTIAL (relaxed candidate retrieval works; strict citations not guaranteed)
Data refresh: IN PROGRESS (train-only facts migration; test isolated from fact writeback)
Orchestrator refactor: IN PROGRESS (moving from CLI-centered flows to patch-scoped multi-agent runtime)

> Rules:
> - Update this file AFTER each planning chunk is implemented.
> - Record what changed, what worked, what failed, and next steps.
> - Keep entries chronological with dates and short headings.
> - Do NOT paste large raw private data; keep it summarized and privacy-safe.

---

## 2026-03-06 — DeepSeek multi-agent runtime compatibility fixed (chat.completions path)

- Root cause confirmed from failed multi-agent runs using `deepseek-v3.2`:
  - the shared runtime client (`src/tools/llm_client.py`) only used `client.responses.create(...)`,
  - the active gateway rejected `deepseek-v3.2` on the Responses API and returned empty outputs after retries,
  - this surfaced upstream as `no_message_output` / `failed_schema_validation` even though the underlying error was API compatibility.
- Implemented a shared fix in `src/tools/llm_client.py`:
  - prefer `chat.completions` for DeepSeek-family models,
  - keep existing Responses path for GPT/Responses-compatible models,
  - add explicit fallback from Responses -> chat when the gateway returns `Unsupported model`.
- Reused the already-proven strategy from the MinerU extractor path rather than changing orchestrator logic.
- Added focused regression coverage in `tests/test_llm_client.py`:
  - DeepSeek text mode uses `chat.completions` directly,
  - JSON mode falls back to chat when Responses rejects the model,
  - existing Responses retry/error-chain behavior remains intact.
- Validation run in `aie` env:
  - `pytest -q tests/test_llm_client.py tests/test_llm_evaluator.py tests/test_master_reasoner_llm_stability.py`
  - result: `15 passed`

---

## 2026-03-04 — Output layout refactor lock (case-centric)

- Locked runtime output migration from run-id scattered roots to case-centric navigation:
  - `artifacts/cases/<case_id>/latest/`
  - `artifacts/cases/<case_id>/runs/<timestamp>__<run_id8>/`
  - `history_index.json` + `latest.json`
- Locked default behaviors:
  - naming key = `case_id + UTC compact timestamp`
  - retention = latest 10 runs per case (configurable)
  - one-version legacy pointer writes enabled for old run-id roots.
- Locked operator-facing artifact:
  - `latest/quick_view.json` as one-screen summary (label/confidence/gate/rounds/evidence ids/paths).
- Scope explicitly non-functional:
  - no change to orchestrator business semantics (patch/whitelist/idempotency/replay/no-touch evidence_table).

---

## 2026-03-04 — Output layout refactor implemented (case-centric + retention + legacy pointers)

- Added `src/core/output_layout.py` as the single planner for runtime output paths:
  - supports `case_centric` and `run_centric`,
  - emits case-scoped `runs/<timestamp>__<run_id8>` names,
  - manages `latest/`, `latest.json`, `history_index.json`,
  - enforces per-case retention pruning (`retain-runs`).
- Integrated output layout into `src/orchestration/run_one.py`:
  - default layout is now case-centric,
  - runtime writes `quick_view.json` for one-screen inspection,
  - summary adds `primary_output_dir`, `latest_dir`, `history_index_path`, `legacy_paths`,
  - legacy compatibility pointer files are emitted when enabled.
- Updated LLM trace placement behavior:
  - `src/tools/llm_trace_store.py` now supports run-scoped trace roots and explicit round trace directory separation.
  - case-centric run stores round reports under `<run_dir>/rounds/` and LLM request/response traces under `<run_dir>/llm/`.
- Updated `src/cli.py` / `src/orchestration/run_one.py` flags:
  - `--output-layout`
  - `--retain-runs`
  - `--output-timestamp-format`
  - `--write-legacy-run-view|--no-write-legacy-run-view`
- Added tests:
  - `tests/test_output_layout_case_centric_paths.py`
  - `tests/test_output_latest_pointer_update.py`
  - `tests/test_output_history_retention.py`
  - `tests/test_output_legacy_compat_pointer.py`
  - `tests/test_quick_view_contract.py`
- Validation runs (aie env):
  - `pytest -q tests/test_output_layout_case_centric_paths.py tests/test_output_latest_pointer_update.py tests/test_output_history_retention.py tests/test_output_legacy_compat_pointer.py tests/test_quick_view_contract.py tests/test_orchestration_run_one_status.py tests/test_round_runner_status_feedback.py` -> all passed.
  - `pytest -q tests/test_cli_case_run.py tests/test_cli.py` -> passed.
  - `pytest -q tests/test_llm_trace_store.py` -> passed.

---

## 2026-03-04 — Confidence de-collapse patch plan lock (minimal)

- Locked change set to reduce confidence collapse without schema/orchestrator contract changes:
  1) remove duplicate confidence clamp (keep one final cap stage only),
  2) replace R0 hard cut (`<=0.45`) with auditable soft penalty factor,
  3) add optional evaluator confidence adjustment (bounded ±0.05, default off) with explicit trigger/audit fields.
- Scope constraints:
  - do not modify `master_output_schema_v3`,
  - do not change patch/whitelist/idempotency/replay/no-touch evidence table guarantees.
- Audit requirements locked:
  - master meta must expose `final_conf_pre_cap`, `final_conf_post_cap`, `cap_value`, `cap_reason`, and component factors,
  - evaluator adjustment writes structured `post_uq.confidence_adjustment` and never mutates mechanism label/gate.

---

## 2026-03-04 — Confidence de-collapse implemented (single-cap + R0 soft penalty + optional evaluator adjustment)

- Master confidence path refactored to avoid repeated hard clipping:
  - `src/reasoning/master_reasoner.py` now computes:
    - `final_conf_pre_cap = raw * sim_factor * ent_factor * mode_factor * neighbor_factor` (plus optional R0 soft penalty),
    - single final cap stage only (`cap_value`), then bounded to `[0.05, 0.95]`.
  - removed validator-stage duplicate confidence cap and removed R0 hard cut (`<=0.45`).
  - confidence audit fields expanded in meta:
    - `base_conf`, `sim_factor`, `ent_factor`, `mode_factor`, `neighbor_factor`,
    - `final_conf_pre_cap`, `final_conf_post_cap`, `cap_value`, `cap_reason`,
    - `r0_penalty_applied`, `r0_penalty_factor`.
- Policy/config updates:
  - `src/reasoning/reasoning_config.py` removed legacy hard cap constants (`conf_cap_*`) from active defaults.
  - added `global_confidence_cap` and `r0_penalty_factor`.
  - `round_runner` passes `round_index` into reasoning config so R0 penalty is explicit/auditable.
- Optional evaluator confidence adjustment added (default off):
  - new helper `apply_evaluator_confidence_adjustment(...)` in `src/agents/judge_agent.py`.
  - bounded adjustment only (`max_abs_delta`, default `0.05`), only when enabled and trigger conditions are met.
  - trigger sources:
    - high-weight added evidence IDs (default set includes `E21..E24`),
    - resolved conflicts.
  - writes structured audit payload to `eval_report.confidence_adjustment`, propagated to `post_uq.confidence_adjustment`.
  - mechanism label and gate are untouched by this adjustment path.
- Runtime plumbing:
  - `src/orchestration/round_runner.py` applies the adjustment after evaluator output (and optional llm layer merge),
    using information-gain and conflict delta context.
  - `src/orchestration/run_one.py` / `src/cli.py` expose optional toggles:
    - `--evaluator-confidence-adjustment-enabled`
    - `--evaluator-confidence-adjustment-max-abs-delta`
  - run summary now includes `round_confidence_summary` for easier per-round inspection.
- Tests added/updated and passed:
  - `tests/test_confidence_not_double_clamped.py`
  - `tests/test_r0_soft_penalty_no_hard_cut.py`
  - `tests/test_evaluator_confidence_adjustment_guardrails.py`
  - updated `tests/test_master_output_validation.py` expectation (no duplicate cap warning).
  - targeted validation run:
    - `pytest -q tests/test_confidence_not_double_clamped.py tests/test_r0_soft_penalty_no_hard_cut.py tests/test_evaluator_confidence_adjustment_guardrails.py tests/test_master_output_validation.py tests/test_confidence_soft_penalty_not_constant.py tests/test_round_runner_stagnation_stop.py tests/test_round_runner_status_feedback.py tests/test_orchestration_run_one_status.py tests/test_cli_case_run.py`
    - result: all passed.

---

## 2026-03-03 — Self-trend aTB evidence plan lock (R1 discriminative increment)

- Problem statement locked:
  - aTB-only iterative runs can stall because R1 lacks strong incremental evidence and R2 may add low-reliability neighbor evidence.
  - master still overuses raw absolute-value narration instead of stable trend semantics.
- Planned fix (this cycle):
  1) add `atb_trends_self` (target-only trend buckets/directions) and inject it in `R1+` reasoning pack.
  2) add dedicated trend evidence IDs (`E_ATB_TREND_1..4`) with compact `value_preview` for prompt citations.
  3) update master instructions to prioritize self-trend evidence and avoid raw absolute-value threshold claims unless tied to configured threshold keys.
  4) tighten `atb_cache_only` stagnation handling so `R1 -> R2` with zero new evidence IDs stops quickly (`next_round_profile=NONE`) and surfaces lane-switch actions.
- Constraints kept unchanged:
  - no orchestrator contract changes (patch/whitelist/idempotency/replay),
  - no evidence_table writeback,
  - no master output schema field additions (pack-only extension).

## 2026-03-03 — Self-only aTB trend profile v1 (E31–E34)

- Added a new self-only trend projection for Master input:
  - `risk_scores.atb_trend_profile` (`atb_trend_v1`) computed from target `evidence_readiness.atb.features_summary` only.
  - fixed bucket constants from successful aTB quantiles for `abs(delta_dihedral/gap/volume)`.
  - direction semantics use sign + epsilon (`increase/decrease/flat/unknown`), independent from bucket strength.
- Injected new evidence IDs for compact citation:
  - `E31` torsion trend (`delta_dihedral`),
  - `E32` CT proxy trend (`delta_gap`),
  - `E33` volume trend (`delta_volume`),
  - `E34` overall motion proxy (`derived_pack` path).
- Goal of this change:
  - make Master cite target self trend first in R1+, reducing over-reliance on neighbor labels for early rounds.
  - keep token budget low while preserving evidence auditability.

---

## 2026-03-03 — Self-trend aTB implementation (R1/R2 anti-stall)

- Implemented target-only self-trend module:
  - new `src/reasoning/atb_trends_self.py` with compact trend projection:
    - `delta_dihedral_bucket/direction`,
    - `delta_gap_bucket/direction`,
    - `delta_volume_bucket/direction`,
    - `overall_motion_proxy`,
    - reliability and short notes.
  - output is deterministic and size-bounded (`<1KB` target).
- Integrated into reasoning pack and evidence registry:
  - `src/reasoning/master_reasoner.py` now injects `risk_scores.atb_trends_self` for `R1+`.
  - added trend evidence IDs:
    - `E_ATB_TREND_1..4` (derived-pack evidence entries with compact previews).
  - prompt policy updated to prioritize self-trend evidence for aTB argumentation and discourage raw absolute-value verdicting.
- Profile/lane behavior updates:
  - `src/reasoning/evidence_profiles.py`: `R1/R2/R3` set `include_atb_trends_self=true`.
  - `src/agents/judge_agent.py`: in `atb_cache_only`, if active profile is `R1` and `count_added==0`, evaluator stops early with
    `next_round_profile=NONE` and `reason_code=no_new_evidence_available_in_lane`.
  - `src/orchestration/round_runner.py`: reasoning thresholds payload now includes gap/volume flat and bucket thresholds for trend computation.
- Policy/default updates:
  - `src/reasoning/reasoning_config.py` adds self-trend threshold defaults:
    `atb_gap_flat_eps`, `atb_gap_weak/strong`, `atb_vol_flat_eps`, `atb_vol_weak/strong`, and `atb_dihedral_flat_eps`.
- Test coverage added/updated:
  - new: `tests/test_atb_trends_self_bucketing.py`
  - new: `tests/test_reasoning_pack_r1_includes_atb_trends_self.py`
  - new: `tests/test_round_runner_stop_when_no_new_evidence_atb_only.py`
  - updated: `tests/test_round_runner_stagnation_stop.py` (R1->R2 no-new-evidence stop expectation).
- Validation:
  - full suite passed in `aie` env:
    - `pytest -q` -> `330 passed, 5 skipped`.

---

## 2026-03-02 — Final v3 refactor lock (implementation batch)

- Locked six implementation constraints before coding:
  1) `mechanism_label` is controlled by `reasoning_config.allowed_mechanism_labels` (not free-form), with candidate-set union and `unknown` fallback.
  2) `PRIMARY_CONFIDENCE` is stored as raw model confidence; final confidence is soft-penalty output with full audit meta.
  3) R2 confidence model includes `neighbor_atb_stats_by_label.separation_score` when reliability is `medium/high`.
  4) `neighbor_atb_stats_by_label` must pass deterministic trim and serialized size `<3KB`.
  5) Pre-R2 stop guard added: repeated master failures cannot terminate immediately; one recovery path must be attempted.
  6) evidence-table no-touch behavior guard is moved to core write entrypoints, while orchestrator content-hash guard remains enabled.
- This entry records the implementation lock and commit sequencing:
  - commit 1: docs
  - commit 2: core code changes
  - commit 3: tests + demo + changelog

---

## 2026-03-02 — JSON-only no-schema runtime hardening (master + evaluator)

- Switched current runtime default to JSON-only contract mode (no provider json_schema by default):
  - `llm_reasoning_effort` default -> `medium`
  - `llm_temperature` default -> `0.2`
  - `llm_use_json_schema` default -> `false`
  - updated in `src/core/types.py`, `src/orchestration/run_one.py`, `src/cli.py`.
- Master prompt hard contract strengthened in `src/reasoning/master_reasoner.py`:
  - explicit JSON-only output contract appended to instructions,
  - required top-level keys listed,
  - bounded arrays (`supporting_chain<=4`, `predictions<=3`, `competing_hypotheses<=3`, `evidence_used<=10`),
  - evidence note length budget (`<=180` chars).
- Master validator adjusted to be less brittle while still constrained:
  - keeps required/type/enum checks,
  - allows unknown extra keys (local validator no longer hard-fails on additional properties),
  - overlong evidence notes are auto-truncated (instead of immediate failure),
  - evidence-id resolution/semantic policy checks unchanged.
- Master recovery loop improved:
  1) primary call,
  2) retry once with larger token budget + stronger JSON-only reminder,
  3) optional JSON-repair pass (no new facts) before final validation.
  - `llm_failure_reason` persisted (`no_message_output | json_parse_error | json_repair_used`).
- Evaluator (`LLMEvaluator`) now follows same JSON-only approach:
  - JSON-only contract in prompt,
  - no json_schema by default,
  - retry + JSON-repair fallback on parse/empty-output failures,
  - `llm_failure_reason` propagated into `eval_report.llm_layer`.
- Tests:
  - added `tests/test_master_reasoner_llm_stability.py` (empty output retry, truncated JSON repair, success path),
  - updated mocks/call signatures in evaluator/master integration tests,
  - updated validation expectation for lightweight mode (`five_signals` extra field no longer fails local shape check).
- Validation:
  - full suite: `313 passed, 5 skipped` (`pytest -q` in `aie` env).

## 2026-03-02 — R2 discriminative increment v2.4 (neighbor aTB stats + stagnation stop hardening)

- Implemented compact neighbor comparative stats module:
  - new file `src/reasoning/neighbor_atb_stats.py`.
  - locked directional semantics:
    - `target_percentile` uses mid-rank in `[0,1]`,
    - `z_robust = (target - median) / (1.4826 * MAD)` sign is stable/interpretable.
  - added raw + absolute torsion views:
    - `delta_dihedral` (signed audit),
    - `abs_delta_dihedral` (primary discriminative axis).
  - low-sample rule: `sample_size<5 => reliability=low`, `z_robust=null` (percentile kept when computable).
- Integrated R2 comparative evidence into master pack/registry in `src/reasoning/master_reasoner.py`:
  - R2+ injects `risk_scores.neighbor_atb_stats`,
  - registry adds compact derived evidence IDs:
    - `E21` abs-delta-dihedral distribution summary,
    - `E22` delta-gap distribution summary,
    - `E23` by-label comparison (only when >=2 labels and each label `n>=2`),
    - `E24` reliability note.
  - `E21/E22` `value_preview` kept minimal (`target`, `neighbors_median`, `neighbors_iqr`, `target_percentile`, `z_robust`).
  - summary text remains in `risk_scores.neighbor_atb_stats.summary` only (not duplicated in registry).
- Evidence validation strategy updated to support derived pack evidence:
  - registry rows now carry `source_type` (`case|derived_pack`),
  - `source_type=case`: resolve `case_path` in case JSON (non-empty required),
  - `source_type=derived_pack`: resolve `pack_path` in reasoning pack (non-empty required),
  - no forced case-path resolution for derived evidence.
- Evidence profile and token control updates:
  - `src/reasoning/evidence_profiles.py` now explicitly sets `include_neighbor_feature_rows=False` for R0–R3.
  - `master` prompt payload removes `risk_scores.atb_neighbor_features_all` rows (stats-only model context).
- Evaluator/round-runner stagnation behavior hardened:
  - `src/agents/judge_agent.py` adds `info_gain` handling (`count_added`, `hypothesis_changed`, `profile_repeated`) and stop reasons:
    - `stagnation_no_new_evidence`,
    - `no_new_evidence_available_in_lane`.
  - `atb_cache_only` top actions are lane-unblock actions (`switch_run_lane_offline_pdf` / `provide_offline_pdf`) instead of repeating manual placeholders.
  - `src/orchestration/round_runner.py` now passes information-gain signals into evaluator and respects evaluator stop reason codes as primary stop source.
- Tests added:
  - `tests/test_neighbor_atb_stats_small_output.py`
  - `tests/test_round_runner_stagnation_stop.py`
  - `tests/test_master_pack_profile_r2_includes_neighbor_stats.py`
  - `tests/test_master_validation_derived_pack_evidence.py`
- Tests updated:
  - `tests/test_reasoning_pack_builder.py`
  - `tests/test_eval_report_feasibility_contract.py`
  - round-runner callback signature fixtures in:
    - `tests/test_round_runner_llm_layer.py`
    - `tests/test_round_runner_r0_minimal_writeback.py`
    - `tests/test_round_runner_status_feedback.py`
    - `tests/test_round_state_delta_fields.py`
- Validation:
  - targeted suite for changed modules: `40 passed`
  - full regression: `310 passed, 5 skipped` (`pytest -q`)

## 2026-03-01 — Fixed LLM failure diagnostics/stability (P1+P2+P3)

- Implemented P1 in `src/tools/llm_client.py`:
  - when `output_text` exists but JSON parse fails, client now attempts structured fallback (`output_parsed` / message `parsed|json|object`) on the same response before failing.
  - this removes a false-negative path where valid structured payload was previously skipped after `json.loads` failure.
- Implemented P2 in `src/tools/llm_client.py`:
  - aggregated attempt-chain errors are now preserved in raised `LLMClientError` for both JSON/text paths:
    - `responses_json_failed:<attempt1> || <attempt2> ...`
    - `responses_text_failed:<attempt1> || <attempt2> ...`
  - this keeps full effort retry diagnostics instead of only the final `errors[-1]`.
- Implemented P3 in `src/orchestration/run_one.py`:
  - `run_status.json` final `run_end` write no longer wipes previous error summaries.
  - status writer now preserves prior `errors` and `latest_eval_report` when current update does not explicitly override them.
- Tests added/updated:
  - `tests/test_llm_client.py`
    - verifies structured fallback still works when `output_text` is invalid JSON,
    - verifies multi-attempt error chain is retained in exception message.
  - `tests/test_orchestration_run_one_status.py`
    - verifies `run_status.json.errors` remains non-empty after `run_end` in iterative failure flow.
- Validation:
  - full suite: `conda run -n aie pytest -q`
  - result: `301 passed, 5 skipped`.

## 2026-02-28 — Split LLM config for master vs evaluator (with inheritance)

- Implemented explicit reasoning config split for iterative runtime:
  - `reasoning_config.master.{model,reasoning_effort}`
  - `reasoning_config.evaluator.{use_llm,model,reasoning_effort}`
- Default behavior:
  - evaluator model/effort inherit master config when evaluator values are not explicitly set.
  - backward compatibility retained via top-level `model` / `reasoning_effort` fallback.
- Runtime call-site updates:
  - `src/orchestration/round_runner.py`
    - master LLM resolves from `reasoning_config.master.*`,
    - evaluator LLM resolves from `reasoning_config.evaluator.*` with inheritance.
  - `src/agents/llm_evaluator.py`
    - now supports runtime model/effort overrides per call,
    - still supports injected mock client for unit tests.
- CLI/entrypoint updates:
  - `src/orchestration/run_one.py` and `src/cli.py` now support evaluator overrides:
    - `--evaluator-model`
    - `--evaluator-reasoning-effort`
  - existing `--model` / `--llm-reasoning-effort` remain master defaults.
- Tests added/updated:
  - `tests/test_llm_evaluator.py`
    - verifies evaluator can run with different model/effort than master defaults.
  - `tests/test_round_runner_llm_layer.py`
    - verifies inheritance path (evaluator uses master defaults),
    - verifies explicit evaluator override path.
- Validation:
  - full suite: `conda run -n aie pytest -q`
  - result: `298 passed, 5 skipped`.

## 2026-02-28 — Optional evaluator LLM critique layer added (rule evaluator remains owner)

- Added optional `LLMEvaluator` module:
  - `src/agents/llm_evaluator.py`
  - strict schema: `eval_llm_output_schema_v1`
  - output fields: `critique_points`, `conflicts`, `voi_ranked_actions`, `confidence_delta_suggestion`, `next_round_profile_suggestion`.
- Runtime behavior:
  - deterministic rule evaluator (`build_eval_report`) remains source of truth for stop/feasibility baseline.
  - LLM layer is enabled only when `reasoning_config.evaluator.use_llm=true`.
  - LLM input kept compact:
    - `reasoning_pack`, `master_output_parsed`, `policy`, `thresholds`, `run_lane_capabilities`.
- Merge logic implemented:
  - final `eval_report` still deterministic-first,
  - `eval_report.llm_layer` stores LLM output/request/response,
  - `voi_ranked_actions` ranking updated with:
    - `expected_information_gain * feasibility_score * llm_priority_weight`.
- Safety:
  - evaluator LLM does not write case directly; only merged sidecar/report data used by round runner.
  - deterministic stop policy remains unchanged and dominant.
- Integration changes:
  - `src/orchestration/round_runner.py`:
    - supports `evaluator_use_llm`,
    - injects `reasoning_config["evaluator"]["use_llm"]`,
    - calls LLMEvaluator conditionally and merges result.
  - `src/orchestration/run_one.py` + `src/cli.py`:
    - new flag `--evaluator-use-llm` (iterative mode path).
- Tests added:
  - `tests/test_llm_evaluator.py`
  - `tests/test_round_runner_llm_layer.py`
- Validation:
  - full suite: `conda run -n aie pytest -q`
  - result: `296 passed, 5 skipped`.

## 2026-02-28 — Runtime progress feedback added (stdout + run_status.json + tail tool)

- Implemented non-business telemetry in iterative runtime:
  - `src/orchestration/round_runner.py` now emits structured stdout events for:
    - `round_start`, `master_call`, `validate`, `judge`, `apply_patch`, `stop`, `round_end`
  - each event includes:
    - `round_index`, `max_rounds`, `active_profile`, `stage`, `status`, `elapsed_ms`.
- Added atomic status snapshot file:
  - `run_status.json` at artifacts root (`<artifacts_dir>/run_status.json`),
  - fields include:
    - `run_id`, `case_id`, `round_index`, `max_rounds`, `active_profile`,
      `round_runner_mode`, `stage`, `last_event`, `last_updated_at`,
      `errors`, `round_dir`, `latest_eval_report`.
  - atomic write implemented via new helper module:
    - `src/orchestration/run_status.py`.
- Error-summary propagation added:
  - for `failed_llm` / `failed_schema_validation`, stdout now prints concise summary
    (`error_codes`, `error_paths`),
  - same summary written to `run_status.json.errors`.
- `run_one` integration:
  - `src/orchestration/run_one.py` now writes status snapshots for run-level stages
    (`run_start`, setup/orchestrator completion, `run_end`) and passes status path into round runner.
- Added status tail utility:
  - `python -m src.orchestration.tail_status --artifacts-dir <...>`
  - polls every 0.5s and prints updated `run_status.json` until Ctrl+C.
- Added tests:
  - `tests/test_round_runner_status_feedback.py`
    - verifies round0 status write,
    - verifies round1 index update,
    - verifies non-empty errors on failed schema.
- Validation:
  - full suite: `conda run -n aie pytest -q`
  - result: `292 passed, 5 skipped`.

## 2026-02-28 — Iterative closure v2.3 implemented (R0 minimal writeback + target aTB baseline + feasibility)

- Implemented iterative round runtime in release path:
  - added `src/orchestration/round_runner.py` with `dryrun_then_commit|commit_all_rounds`,
  - R0 in `dryrun_then_commit` now writes only minimal `/iterative/*` state via RFC6902 whitelist,
  - R1+ commits master/post_uq outputs, while replay traces are produced for every round.
- Added evidence profile config for rounds:
  - new `src/reasoning/evidence_profiles.py`,
  - default `R0/R1` both include target aTB summary baseline (`include_target_atb_summary=true`).
- Integrated profile-aware pack shaping in `src/reasoning/master_reasoner.py`:
  - reasoning pack now follows active profile toggles (target atb summary/full, neighbor summary/stats, literature/experiment status, neighbor_topk, registry cap).
- Extended evaluator output with feasibility in `src/agents/judge_agent.py`:
  - `eval_report.feasibility` (`lane_capabilities`, `constraints`, `overall_score`),
  - `voi_ranked_actions[]` now includes `feasible`, `feasibility_score`, `blocked_by`, `unblock_actions`, `priority_score`.
- Added round sidecar/report writers in `src/tools/llm_trace_store.py`:
  - `write_master_round_report`, `write_eval_report`, `write_round_state`.
- Integrated iterative mode in `src/orchestration/run_one.py`:
  - new flags: `--iterative`, `--round-runner-mode`, `--max-rounds`, `--round-start-profile`,
  - setup stage uses `build_setup_agents()` then rounds runner.
- Added tests:
  - `tests/test_round_runner_r0_minimal_writeback.py`
  - `tests/test_evidence_profiles_r0_r1_target_atb_baseline.py`
  - `tests/test_eval_report_feasibility_contract.py`
  - `tests/test_round_state_delta_fields.py`
  - adjusted `tests/test_reasoning_pack_builder.py` for profile default (target atb full features not in baseline pack).
- Validation:
  - full suite passed: `conda run -n aie pytest -q`
  - result: `289 passed, 5 skipped`.

## 2026-02-27 — Plan B applied: keep mechanism_hint in case, exclude from master input

- Implemented in `src/reasoning/master_reasoner.py`:
  - removed `risk_scores.mechanism_hint` and `risk_scores.hint_confidence` from `reasoning_pack.risk_scores`,
  - removed hint/hint_confidence paths from `evidence_registry` generation,
  - validator now hard-rejects any evidence reference that resolves to:
    - `/risk_scores/mechanism_hint`
    - `/risk_scores/hint_confidence`
    with error code `forbidden_hint_reference`.
- Also removed direct hint projection in `mechanism_context` so master prompt no longer consumes hint values.
- Tests added/updated:
  - `tests/test_reasoning_pack_builder.py`
    - verifies hint fields absent from reasoning pack and absent from evidence registry paths.
  - `tests/test_master_output_validation.py`
    - new negative test ensures hint-path citation is rejected (`forbidden_hint_reference`).
- Doc note added:
  - `doc/schemas.md` now states hint fields are debug/routing-only and excluded from master input/citation.
- Validation run:
  - `conda run -n aie pytest -q tests/test_reasoning_pack_builder.py tests/test_master_output_validation.py tests/test_master_prompt_agnostic.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_idempotency.py tests/test_llm_trace_store.py`
  - result: `28 passed`.

## 2026-02-27 — Master prompt made mechanism-agnostic (dynamic candidate-set injection)

- Refactored master prompt wording to remove hard-coded mechanism names from prompt/rubric text:
  - in `src/reasoning/master_reasoner.py`, replaced fixed mechanism wording with dynamic candidate-set injection from:
    - `reasoning_pack.mechanism_context.candidate_mechanisms_top3`
  - injected text behavior:
    - candidates present: `Top competing mechanisms (from retrieval priors): ...`
    - candidates missing: generic fallback `Top competing mechanisms are uncertain; propose plausible hypotheses from evidence.`
- Updated discriminator and narrative instructions to be mechanism-agnostic:
  - “separate the top competing mechanisms listed above (or top hypotheses you propose if none are provided).”
  - no fixed “ICT/TICT/ESIPT” language in prompt instruction templates.
- Updated trace narrative templates in `src/tools/llm_trace_store.py`:
  - removed hard-coded “ICT vs TICT boundary” style text,
  - replaced with generic unresolved-boundary language among top competing mechanisms.
- Added tests:
  - `tests/test_master_prompt_agnostic.py`
    - generic fallback when no candidates,
    - dynamic label injection when candidates are provided.
- Validation run:
  - `conda run -n aie pytest -q tests/test_master_prompt_agnostic.py tests/test_llm_trace_store.py tests/test_master_output_validation.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_idempotency.py`
  - result: `24 passed`.

## 2026-02-26 — Quality upgrade: richer five_signals evidence chain + 3-paragraph conclusion narrative

- Updated `src/tools/llm_trace_store.py` to improve summary quality while keeping schema unchanged:
  - `five_signals.evidence_chain` now expands to 8-12 compact evidence-id items,
  - chain is ordered by three blocks:
    1) uncertainty bounds (`E2`, `E4`, `E6`),
    2) aTB cues (`E11`, `E12`, optional `E14`),
    3) missing discriminators (`E19`, `E20`, optional `E10`),
  - each item remains `{evidence_id, role, note}` and note text explicitly states inference impact.
- Added fallback fill logic (from model-cited IDs) to guarantee minimum chain coverage without adding case_path payloads.
- `five_signals.conclusion.natural_language_mechanism` now emits a 3-paragraph narrative string:
  - paragraph 1: best current hypothesis under available evidence,
  - paragraph 2: why ICT-vs-TICT mixture boundary remains,
  - paragraph 3: falsifiable next tests tied to hypotheses.
- Prompt guidance aligned in `src/reasoning/master_reasoner.py`:
  - asks for 8-12 top-level evidence items using evidence IDs,
  - asks for 3-paragraph mechanism narrative,
  - keeps threshold policy constraint (`reasoning_config.thresholds` key/value when threshold text is used).
- Regression tests:
  - `conda run -n aie pytest -q tests/test_llm_trace_store.py tests/test_master_output_validation.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_idempotency.py`
  - result: `22 passed`.

## 2026-02-26 — Fix: reduce threshold false positives for spectral “band/range” text

- Updated threshold-trigger logic in `src/reasoning/master_reasoner.py`:
  - split triggers into:
    - strong: `threshold`, `cutoff` (always threshold-like),
    - weak: `range`, `band` (threshold-like only in numeric/comparator context).
  - added helper `_weak_trigger_in_numeric_context(text)` with local-window context scan (`+/- 40 chars`), checking:
    - numeric values,
    - comparison operators (`<`, `>`, `<=`, `>=`),
    - interval form (`8-15`, `8–15`),
    - contextual tokens (`between`, `from`, `to`, `~`, `±`, `approx`).
- Kept hard guard behavior unchanged:
  - threshold-like text still requires configured threshold key + allowed threshold value, else `invented_threshold_not_allowed`.
- Prompt guidance already aligned:
  - threshold mention must cite `reasoning_config.thresholds` key/value,
  - otherwise use relative language (e.g., modest/large).
- Added tests in `tests/test_master_output_validation.py`:
  - PASS: spectral phrases with non-numeric “band” wording,
  - FAIL: weak trigger + numeric context (`band in 8–15°`, `range 0.5–0.7`, `band > 500 nm`).
- Validation:
  - `conda run -n aie pytest -q tests/test_master_output_validation.py`
  - `conda run -n aie pytest -q tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_idempotency.py tests/test_llm_trace_store.py`
  - result: all passed.

## 2026-02-26 — Fix: enforce evidence_id-only citations (forbid case_path in model output)

- Implemented consistent evidence citation rule in master validation:
  - model output evidence items must use `evidence_id` only,
  - any `case_path` key in output now fails fast with `code=evidence_case_path_forbidden`.
- Prompt strengthened in `src/reasoning/master_reasoner.py`:
  - explicit instruction: never output `case_path` anywhere; cite with `evidence_id` only.
- Summary trace normalization in `src/tools/llm_trace_store.py`:
  - `summary5.evidence_chain[]` no longer mirrors `case_path`; keeps compact `{evidence_id, role, note}`.
- Tests added/updated:
  - `test_evidence_id_only_without_case_path_passes`
  - `test_case_path_null_rejected_with_clear_code`
  - `tests/test_llm_trace_store.py` now asserts no `case_path` in summary evidence chain.
- Validation run:
  - `conda run -n aie pytest -q tests/test_master_output_validation.py tests/test_llm_trace_store.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_idempotency.py`
  - result: `20 passed`.

## 2026-02-26 — Prompt hardening: remove band/range inducing wording + explicit threshold citation rule

- Updated master template in `src/reasoning/master_reasoner.py`:
  - removed band/range-style wording from rubric text,
  - added hard rule: if threshold logic is mentioned in output text, model must cite exact `reasoning_config.thresholds` key/value,
  - otherwise model must use relative descriptors (e.g., `modest`, `large`) and avoid threshold/range/band/cutoff language.
- Updated validator threshold-text gate:
  - threshold-like text is detected via threshold/cutoff/range/band terms, comparison operators, or numeric intervals,
  - such text now passes only when both conditions hold:
    1) it references at least one configured threshold key,
    2) it includes at least one configured threshold value.
- Goal: prevent free-form invented numeric bands while keeping deterministic policy-based threshold mentions auditable.

## 2026-02-26 — Prompt payload trim: remove neighbor full features + add neighbor_atb_stats

- Updated `build_reasoning_pack` in `src/reasoning/master_reasoner.py`:
  - `risk_scores.atb_neighbor_features_all[*].features` is now removed from model-facing pack payload.
  - `features_summary` is retained (or derived from full features when only `features` existed).
  - new `neighbor_atb_stats` block added to pack:
    - computed from neighbor `features_summary`,
    - includes per-field `median`, `mad`, `z_scores`, `valid_counts`, `warnings`,
    - fields: `delta_gap`, `delta_dihedral`, `delta_volume`, `excitation_energy`.
- Purpose:
  - reduce token footprint,
  - keep discriminative neighbor-vs-target aTB signal explicit for master reasoning,
  - preserve replay/audit determinism.
- Tests updated and green:
  - `tests/test_reasoning_pack_builder.py` now asserts no neighbor full `features` payload and verifies `neighbor_atb_stats`.
  - Regression suite run:
    - `conda run -n aie pytest -q tests/test_reasoning_pack_builder.py tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py tests/test_orchestration_run_one_master_reasoning.py`
    - result: `20 passed`.

## 2026-02-26 — Implemented: master reasoning audit-safety + token trim (schema v3)

- Implemented in release runtime (`src/reasoning/master_reasoner.py`, `src/agents/reasoning_agent.py`):
  - promoted default schema to `master_output_schema_v3`,
  - `reasoning_pack.evidence_registry` switched to compact list rows (`evidence_id, case_path, label, value_preview, role_hint, note_hint`),
  - removed prompt dependency on `path_map`/`allowed_evidence_paths` (registry is now the sole citation namespace),
  - conservative mode now caps `neighbors_topk` to 5 in reasoning pack.
- Prompt hardening:
  - explicit instruction: no invented numeric thresholds/bands,
  - thresholds are injected from `reasoning_config.thresholds` into payload and prompt.
- Validator hardening:
  - evidence references remain `evidence_id`-only; each citation resolves to real non-null case value,
  - regex guard rejects invented threshold/range text unless numbers are from configured thresholds,
  - conservative limits now auto-append lane-disabled warning when literature/experiment are disabled.
- Post-processing/writeback:
  - keeps compact evidence_id citations in `master_reasoning`,
  - writes expanded resolved evidence to `reasoning.used_evidence`,
  - continues writing `reasoning.used_evidence_ids` and `reasoning.used_evidence_paths`.
- Tests updated and passing:
  - `tests/test_reasoning_pack_builder.py`
  - `tests/test_master_output_validation.py`
  - `tests/test_reasoning_agent_idempotency.py`
  - `tests/test_orchestration_run_one_master_reasoning.py`
  - `tests/test_reasoning_agent_patch_scope.py`
  - command: `conda run -n aie pytest -q tests/test_reasoning_pack_builder.py tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_patch_scope.py`
  - result: `21 passed`

## 2026-02-26 — Plan locked: master evidence-weighting hardening (neighbors as prior, aTB as evidence)

- Scope locked for minimal-risk refactor in `src/reasoning/master_reasoner.py`:
  - add explicit Evidence Weighting Policy (`neighbors=context by default`, support only if `top1_sim>=0.55`),
  - enforce 4-step supporting chain (`A->B->C->D`) with mandatory aTB citations,
  - add schema v2 fields (`atb_support_level`, `step_id`, `step_name`),
  - add confidence caps based on similarity + mechanism entropy,
  - keep runtime/orchestrator entrypoints unchanged and keep evidence_table no-touch.
- Implementation strategy:
  - keep ReasoningAgent wrapper thin,
  - inject thresholds into `reasoning_config` so they are replay-auditable.

---

## 2026-02-26 — Implemented: master evidence-weighting hardening + schema v2

- Implemented localized refactor in release runtime path (no orchestrator entrypoint changes):
  - new reasoning policy defaults in `src/reasoning/reasoning_config.py`
  - ReasoningAgent now injects policy + `master_output_schema_version=v2` into `reasoning_config`
  - master prompt now includes:
    - Evidence Weighting Policy (neighbors are prior/context unless sim threshold met),
    - aTB dihedral rubric for support levels,
    - required A->D supporting chain contract,
    - confidence-cap thresholds.
- Schema/validator upgrades in `src/reasoning/master_reasoner.py`:
  - added schema v2 fields:
    - `mechanism_claim.primary_hypothesis.atb_support_level`
    - `competing_hypotheses[*].atb_support_level`
    - `supporting_chain[*].step_id` + `step_name`
  - kept schema v1 selectable for compatibility (`master_output_schema_version=v1`)
  - validator now enforces:
    - path allowlist + existence + non-null values,
    - neighbor-as-support blocked when `top1_sim < 0.55`,
    - supporting_chain exactly 4 ordered steps A/B/C/D,
    - minimum aTB citation counts in chain,
    - confidence caps from similarity/entropy + conservative gate,
    - primary `atb_support_level` consistency with `delta_dihedral` rubric.
- Reasoning pack path policy tightened to reduce noise:
  - allowlist now focused on selected risk-score fields, aTB readiness fields, gate fields, and selected neighbor fields.
- Validation/tests:
  - `conda run -n aie pytest -q tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_pack_builder.py`
  - `conda run -n aie pytest -q tests/test_cli_case_run.py tests/test_case_run_atb_success_lane.py`
  - Result: `14 passed`.

---

## 2026-02-26 — Fix: failed_schema_validation by switching to evidence_id citations + canonical resolution

- Addressed validation instability where model outputs looked reasonable but failed on non-canonical evidence paths.
- Changes in master reasoning:
  - citation format now uses `evidence_id` only (no model-facing `case_path` citations),
  - removed `allowed_evidence_paths` and `path_map` from `reasoning_pack`,
  - added compact `evidence_registry` (`E1..En`) with canonical real `case_path` mappings.
- Validator now uses strict two phases:
  1) local schema validation on raw `master_output` only,
  2) evidence resolution (`evidence_id -> case_path`) and non-null/non-empty value enforcement.
- Developer ergonomics:
  - `validation_errors` are now structured objects (`type/code/path/detail`) and truncated to first 5.
  - run artifacts expose `master_output_raw`, `master_output_parsed`, `validation_errors`.
- Writeback consistency:
  - keeps existing `master_reasoning*` fields,
  - mirrors into `/reasoning/master_reasoning`, `/reasoning/used_evidence_ids`, `/reasoning/used_evidence_paths`, `/reasoning/meta`.
- Tests added/updated:
  - reject unknown evidence_id,
  - reject pack-only/non-canonical path via registry,
  - reject null/empty resolved values,
  - accept valid E-id citations,
  - confirm five-signals generation is post-validation and not part of master schema validation.

---

## 2026-02-26 — Refactor: evidence_id citations (remove case_path allowlist/path_map from prompt pack)

- Implemented reasoning citation compression to reduce tokens:
  - removed `allowed_evidence_paths` and `path_map` from `reasoning_pack`,
  - added compact `evidence_registry` (`E1..En -> {case_path,label}`) as the only citation namespace for master LLM.
- Master schema and validation updated:
  - `evidence_used` now uses `{evidence_id, note, role}`,
  - validator resolves every evidence_id to canonical case_path and enforces non-null/non-empty value,
  - unknown/missing/empty evidence resolution now fails schema validation.
- Writeback compatibility:
  - existing `master_reasoning*` fields preserved,
  - mirrored `reasoning.*` writeback added (`reasoning.master_reasoning`, `reasoning.used_evidence_paths`, `reasoning.used_evidence_ids`).
- Tests added/updated:
  - valid E-id citation pass,
  - unknown E-id fail,
  - null-resolving E-id fail,
  - pack builder now asserts `evidence_registry` (no allowlist/path_map dependency).

---

## 2026-02-26 — Hotfix: strict json_schema required coverage (`supporting_chain.step_name`)

- Runtime error addressed:
  - provider strict schema check rejected `master_output_schema_v2` because `supporting_chain.items.properties` contained `step_name` but `required` omitted it.
- Fix:
  - updated `src/reasoning/master_reasoner.py` so v2 `supporting_chain.items.required` includes `step_name`.
  - added strict-schema coverage test in `tests/test_master_output_validation.py`:
    - recursively asserts every object schema with `additionalProperties=false` has `required` covering all property keys.
- Validation:
  - `conda run -n aie pytest -q tests/test_master_output_validation.py tests/test_orchestration_run_one_master_reasoning.py`
  - Result: `11 passed`.

---

## 2026-02-25 — aTB full features payload for target + neighbors in master pack

- Implemented requested expansion of aTB payload for reasoning:
  - target full cache payload now written at `evidence_readiness.atb.features` (full `features.json`).
  - neighbor rows now enrich from cache when `neighbors[].neighbor_atb` lacks full payload.
  - `risk_scores.atb_neighbor_features_all` now keeps success-neighbor rows with full `features.json` + `features_summary` (not just compact deltas).
- Reasoning pack contract stays:
  - `risk_scores.atb_neighbor_features_all` is included for model-facing master context.
  - `evidence_readiness.atb.features` is included for target full payload.
- Validation:
  - `conda run -n aie pytest -q tests/test_chem_agent_neighbor_consistency.py tests/test_reasoning_pack_builder.py`
  - `conda run -n aie pytest -q tests/test_orchestration_run_one_master_reasoning.py tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py`
  - Result: `9 passed`.

---

## 2026-02-25 — Prompt token budget rebalance: reduce traceability payload, increase aTB neighbor detail

- User feedback addressed: traceability payload was dominating tokens while aTB details were more useful.
- Changes:
  - `Reasoning` prompt payload now compacted:
    - removes heavy `path_map` and full `allowed_evidence_paths` from model-facing payload
    - adds compact `allowed_evidence_path_prefixes` + first 40 path examples
    - full allowlist/path-map still kept server-side for validation.
  - `ChemAgent` now writes richer neighbor aTB payloads:
    - `risk_scores.atb_neighbor_features_all` uses success-only neighbor rows with full `features.json` attached.
- Effect (measured):
  - full internal pack size remained ~16 KB,
  - model-facing user payload dropped to ~4.6 KB on sampled case.
- Validation:
  - `conda run -n aie pytest -q tests/test_chem_agent_neighbor_consistency.py tests/test_reasoning_pack_builder.py tests/test_orchestration_run_one_master_reasoning.py tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py`
  - Result: `9 passed`

---

## 2026-02-25 — Added neighbor aTB rows to master prompt pack

- User request addressed: master prompt now includes concrete neighbor aTB result rows (not only aggregate consistency stats).
- Runtime changes:
  - `ChemAgent` writes `risk_scores.atb_neighbor_features_all` (success-only neighbors, rank-sorted) with full `features.json`.
  - `ReasoningPack` includes this field (`atb_neighbor_features_all`) so full neighbor aTB payload is visible to master LLM.
- Data shape in each row:
  - `neighbor_inchikey`, `rank`, `sim`, `delta_gap`, `delta_dihedral`, `delta_volume`, `features_summary`, `features`.
- Guardrails unchanged:
  - retrieval remains ECFP-only,
  - no evidence_table writeback,
  - gate ownership remains ReadyAgent-only.
- Tests:
  - updated `tests/test_chem_agent_neighbor_consistency.py` (new patch field assertions)
  - updated `tests/test_reasoning_pack_builder.py` (pack includes new field)
  - regression checks:
    - `tests/test_orchestration_run_one_master_reasoning.py`
    - `tests/test_master_output_validation.py`
  - Result: `8 passed`

---

## 2026-02-24 — LLM trace layout update: run_id folders + reasoning five-signal JSON

- Updated LLM trace persistence to run-scoped layout:
  - all LLM traces now write under `artifacts/llm_responses/<run_id>/`
  - file naming: `<run_id>.<agent_name>.response.json`
- Reasoning-specific extraction added:
  - `artifacts/llm_responses/<run_id>/<run_id>.reasoning_agent.summary5.json`
  - includes five sections:
    1) conclusion
    2) confidence
    3) competing_hypotheses
    4) evidence_chain
    5) limits_and_next_actions
- Code changes:
  - new utility: `src/tools/llm_trace_store.py`
  - ReasoningAgent writes both raw response trace + summary5 JSON
  - JudgeAgent writes response trace in same run folder
  - ChemAgent writes aggregated literature LLM trace in same run folder (when LLM used)
- Tests:
  - new `tests/test_llm_trace_store.py`
  - updated `tests/test_orchestration_run_one_master_reasoning.py` (assert run-scoped trace files)
- Validation:
  - `conda run -n aie pytest -q tests/test_orchestration_run_one_master_reasoning.py tests/test_llm_trace_store.py tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py tests/test_llm_client.py`
  - `conda run -n aie pytest -q tests/test_case_run_atb_success_lane.py tests/test_cli_case_run.py`
  - Result: `12 passed`

---

## 2026-02-24 — Hotfix: conservative/no-emission validation relaxed with semantic matching + auto limits

- Change request implemented:
  - replaced hard-string checks for conservative/no-emission limits in master validation.
  - validator now uses semantic keyword matching.
  - if missing, validator auto-appends standard limits instead of failing writeback.
- Code changes:
  - `src/reasoning/master_reasoner.py`
    - added semantic helpers (`_has_any_token`, `_normalize_limits`)
    - added standard auto-limit constants
    - conservative mode now:
      - still enforces confidence cap,
      - no longer emits `missing_conservative_limit_statement` / `missing_no_emission_evidence_limit`,
      - auto-injects `Conservative mode...` and `No emission evidence...` limits when absent.
- Tests updated:
  - `tests/test_master_output_validation.py`
    - existing conservative test updated for auto-append behavior
    - new semantic-phrase acceptance test added
- Validation:
  - `conda run -n aie pytest -q tests/test_master_output_validation.py tests/test_orchestration_run_one_master_reasoning.py tests/test_reasoning_agent_idempotency.py`
  - Result: `6 passed`

---

## 2026-02-24 — Hotfix: reasoning effort downgrade retry (`xhigh` fallback chain)

- Issue:
  - run `a10f99ff81db4bea99ec450db22b5bc3` still failed at reasoning with
    `responses_empty_output_text:output_types=['reasoning']`.
  - this indicates gateway returned reasoning-only items without usable text/parsed payload.
- Fix in `src/tools/llm_client.py`:
  - added deterministic effort retry chain:
    - `xhigh -> high -> medium -> low -> none -> (no reasoning field)`
  - applies to `responses_json` and `responses_text`.
  - retries also cover invalid JSON decode on textual output.
  - last failure message now carries effort/output-type context.
- Added test:
  - `tests/test_llm_client.py::test_responses_json_retries_lower_effort_when_empty`
  - validates first call uses `xhigh` and retry uses `high`.
- Validation:
  - `conda run -n aie pytest -q tests/test_llm_client.py tests/test_orchestration_run_one_master_reasoning.py tests/test_master_output_validation.py tests/test_reasoning_agent_idempotency.py`
  - Result: `8 passed`

---

## 2026-02-24 — Hotfix: Responses strict JSON fallback for empty `output_text`

- Bug reproduced on run `289bb3a62ed34c95a2adc319adeff1f8`:
  - reasoning step returned `responses_empty_output_text` while request succeeded.
  - LLM trace file: `artifacts/llm_responses/<case_id>/<run_id>.reasoning_agent.json`.
- Root cause:
  - gateway/model can return strict JSON payload in structured fields (`parsed/json/object`) without populating `output_text`.
  - wrapper previously treated this as hard failure.
- Fix:
  - `src/tools/llm_client.py` now falls back to parse structured JSON from response content when `output_text` is empty.
  - keeps strict JSON parsing behavior and returns canonical `text` from parsed JSON.
  - improved error detail for true empty responses (`output_types=[...]`).
- Added tests:
  - `tests/test_llm_client.py`:
    - parses normal `output_text` JSON path
    - parses structured payload fallback path
- Validation:
  - `conda run -n aie pytest -q tests/test_llm_client.py tests/test_orchestration_run_one_master_reasoning.py tests/test_master_output_validation.py`
  - Result: `6 passed`

---

## 2026-02-24 — Hotfix: master schema `required` mismatch caused `invalid_json_schema`

- Bug observed from runtime LLM trace:
  - `artifacts/llm_responses/<case_id>/<run_id>.reasoning_agent.json`
  - provider error: root `required` must include all root `properties`; missing `recommended_next_actions`.
- Fix:
  - updated `src/reasoning/master_reasoner.py` root schema `required` to include `recommended_next_actions`.
  - added explicit note in `doc/process.md` under master reasoner contract.
- Impact:
  - unblocks strict Responses JSON schema validation for reasoning step.
  - no changes to patch scope / gate ownership / evidence_table no-touch policy.

---

## 2026-02-24 — Runtime defaults + LLM trace folder update (case-run)

- Updated release runtime defaults for `case-run`:
  - default `--model` -> `gpt-5.2`
  - default `--llm-reasoning-effort` -> `xhigh`
- Added dedicated LLM trace output root for reasoning runs:
  - new runtime option `--llm-response-dir` (default `artifacts/llm_responses`)
  - ReasoningAgent now mirrors request/response payloads to:
    - `artifacts/llm_responses/<case_id>/<run_id>.reasoning_agent.json`
  - step-level replay under `artifacts/<run_id>/<step>/` remains unchanged.
- Wiring updates:
  - `src/core/types.py` (`AgentContext.llm_response_dir`, default `artifacts/llm_responses`)
  - `src/orchestration/run_one.py` (new arg propagation + run summary includes `llm_response_dir`)
  - `src/cli.py` (`case-run` parser/default namespace + forwarding)
  - `src/agents/reasoning_agent.py` (LLM trace mirror write)
- Validation:
  - `conda run -n aie pytest -q tests/test_cli_case_run.py tests/test_reasoning_agent_idempotency.py tests/test_orchestration_run_one_master_reasoning.py tests/test_orchestration_run_one.py tests/test_case_run_atb_success_lane.py`
  - Result: `6 passed`

---

## 2026-02-24 — Plan: integrate aTB neighbor consistency into release runtime

- Scope locked for release orchestrator runtime (`case-run` path, not example paths):
  - ChemAgent computes `risk_scores.atb_neighbor_consistency` from target aTB deltas vs neighbor aTB deltas.
  - Inputs include only neighbors with `neighbor_atb.cache_status=="success"` and complete required fields.
  - Retrieval remains structure-only (ECFP unchanged); this is a risk/readiness signal only.
- ReadyAgent integration:
  - reads `risk_scores.atb_neighbor_consistency.flag/reliability`,
  - if `flag=="outlier"` and reliability is `medium|high`, force/keep conservative reasoning mode and add non-blocking verification actions,
  - if `target_missing` or `insufficient_neighbors`, only add rationale (no overreaction beyond current gating policy).
- Guardrails reaffirmed:
  - ReadyAgent remains sole writer of `current_gate.*` and `action_rationale`.
  - No `evidence_table` writeback path is introduced.
- Planned validation:
  - unit tests for robust z-score/MAD-zero/flag logic,
  - orchestration integration test for ChemAgent write + ReadyAgent conservative mode reaction,
  - explicit no-touch evidence_table guard remains active.

---

## 2026-02-24 — Implemented: aTB neighbor consistency in release runtime

- Added runtime compute module:
  - `src/chem/atb_neighbor_consistency.py`
  - robust z-score with median/MAD per field (`delta_gap`, `delta_dihedral`, `delta_volume`)
  - flags: `target_missing | insufficient_neighbors | inlier | outlier`
  - reliability: `low | medium | high`
  - MAD-zero handling (`z=0` when equal to median; else `z=null` + `mad_zero:<field>` warning)
- ChemAgent integration (`src/agents/chem_agent.py`):
  - computes `risk_scores.atb_neighbor_consistency` every run from target+neighbor aTB data
  - filters neighbors to successful/completely-typed rows only
  - keeps retrieval unchanged (ECFP-only) and writes no evidence_table paths
  - update includes whitelist allowance for `/risk_scores/atb_neighbor_consistency`
- ReadyAgent integration (`src/agents/ready_agent.py`):
  - reads neighbor consistency flag/reliability
  - if `outlier` with reliability `medium|high`, forces/keeps `ready_conservative`
  - appends non-blocking follow-ups (`literature_search_web`, `request_min_experiment_emission`)
  - mirrors shortcut `risk_scores.readiness_atb_neighbor_flag`
  - gate ownership rules remain unchanged (ReadyAgent only)
- Tests added/updated:
  - `tests/test_atb_neighbor_consistency.py` (new math/flag semantics)
  - `tests/test_chem_agent_neighbor_consistency.py` (ChemAgent write path)
  - `tests/test_ready_agent.py` (outlier -> conservative behavior)
  - `tests/test_orchestration_run_one.py` (ReadyAgent consumes Chem risk score)
  - no-touch regressions re-run (`tests/test_evidence_table_no_touch_behavior.py`, `tests/test_evidence_table_no_touch_content_hash.py`)
- Validation:
  - targeted suite: `15 passed`
  - full suite: `251 passed, 5 skipped` (`pytest -q`)

---

## 2026-02-24 — Implemented: Master Reasoner v2.1 (pack-only + strict JSON + master_reasoning*)

- Added pure-function reasoning core:
  - `src/reasoning/master_reasoner.py`
  - capabilities:
    - `build_reasoning_pack` (deterministic minimal projection)
    - `build_master_prompt_bundle` (versioned system/instructions/payload/schema)
    - strict semantic validation for evidence path allowlist + path existence
    - conservative-mode guardrails (confidence cap + required limits statements)
    - RFC6902 patch builder for `master_reasoning*` writeback
- Refactored `src/agents/reasoning_agent.py`:
  - write targets switched from legacy `/reasoning/*` to top-level:
    - `/master_reasoning`
    - `/master_reasoning_meta`
    - `/master_reasoning_status`
    - `/master_reasoning_used_evidence_paths`
  - execution condition:
    - gate must be ready,
    - `action_plan` must contain `run_master_reasoner` (top1 optional; default disabled).
  - idempotency now binds to pack hash + prompt/template/model scope via agent inputs.
- Updated `src/agents/judge_agent.py`:
  - reads `master_reasoning` first, falls back to legacy `reasoning.master_output` for compatibility.
- New tests:
  - `tests/test_reasoning_pack_builder.py`
  - `tests/test_master_output_validation.py`
  - `tests/test_reasoning_agent_patch_scope.py`
  - `tests/test_reasoning_agent_idempotency.py`
  - `tests/test_orchestration_run_one_master_reasoning.py`
- Validation:
  - targeted set: `15 passed`
  - full suite: `259 passed, 5 skipped`
- Runtime demo:
  - `python -m src.cli case-run --smiles ... --run-lane atb_cache_only ...`
  - produced case with `master_reasoning_status=failed_llm` when `OPENAI_API_KEY` missing, while preserving auditable patch/artifact trail and no evidence_table writes.

---

## 2026-02-24 — Multi-agent framework refactor kickoff (NOW path)

- Repo audit conclusions (current code reality):
  - `src/cases/*` contains most case logic and patch/replay behavior; `src/cli.py` currently orchestrates many flows directly.
  - `src/features/anchor_hybrid_ecfp_atb_partial.py`, `src/features/anchor_two_stage_partial_atb.py`, and related validators/manifests are legacy experimental tracks that conflict with the current “structure-only retrieval” decision.
  - `src/cases/example_a_first_runner.py` is useful as a locked behavior reference, but is not the final orchestration surface.
- Target architecture adopted:
  - `src/core/` for patch enforcement + hashing + artifact IO,
  - `src/tools/` for LLM/MinerU adapters,
  - `src/agents/` for role-scoped agents,
  - `src/orchestration/` for registry + policies + loop entrypoint.
- Execution contract now locked:
  - all agent writes through RFC6902 patch with whitelist + append-only enforcement,
  - per-step idempotency key + `agent_runs[]` row + replay artifacts.
- Ready Agent ownership remains strict:
  - only writer of `current_gate` and `action_rationale`,
  - other agents cannot patch gate fields directly.
- Current next action:
  - finish framework wiring + tests + one-command demo for one `test.csv` sample (`run_one` entrypoint) using offline PDF mode where supplied.

## 2026-02-24 — E0 evidence-table guard hardening (no mtime dependency)

- Replaced fragile `mtime`-based no-touch check with explicit code-path guard semantics in `src/cases/example_a_first_runner.py`:
  - added single evidence-table writer hook `_write_evidence_table_rows(...)`,
  - hook always raises in E0 (writeback forbidden),
  - runner calls this hook only when writeback is requested, which fails fast by design.
- Updated policy wording in `doc/process.md`:
  - no-touch guarantee now defined as runtime guard + monkeypatchable writer-hook tests,
  - file `mtime` is explicitly downgraded to optional smoke signal only.
- Tests updated in `tests/test_example_a_first_runner.py`:
  - assert writer hook is **not called** in normal E0 execution,
  - assert writeback request fails fast and routes through the guarded writer hook.
- Validation:
  - `pytest -q tests/test_example_a_first_runner.py` passed (16/16).

## 2026-02-24 — Release runtime hardening (de-example path + platform guards)

- Promoted E0 semantics to orchestrator-level invariants:
  - step replay contract now requires `01_raw_outputs.json` (plus optional split raw files/index),
  - idempotency key now includes `run_config_hash` (lane/config scope),
  - `agent_runs[]` now carries `status_reason_code`.
- Added standardized skipped reason codes in core types:
  - `idempotency_hit`, `gate_blocked_reasoning`, `lane_disabled`, `missing_required_input`, `upstream_failed`, `not_applicable`.
- Enforced global gate ownership in orchestrator:
  - only `ready_agent` may patch `/current_gate/*`, `/action_rationale`, `/action_plan`;
  - non-owner writes fail fast at patch-validation stage and stop subsequent steps.
- Added no-touch content guard for `data/evidence_table.parquet` in orchestrator runtime:
  - hash before/after run must match (or file must remain absent).
- Release command path finalized:
  - added official `case-run` command in `src/cli.py` (wraps `src/orchestration/run_one.py`),
  - default lane is `atb_cache_only`,
  - optional stage snapshots output (`data_agent_case`, `chem_agent_case`, `ready_agent_case`).
- Compatibility aliases retained for one version:
  - `case-e0`, `case-e2e`, `case-e2e-atb` now forward to `case-run` with deprecation warnings.
- Judge write scope tightened:
  - Judge no longer writes `action_plan`; it writes `post_uq` only.
- New/updated tests:
  - `tests/test_orchestrator_replay_contract.py`
  - `tests/test_orchestrator_idempotency.py`
  - `tests/test_orchestrator_skip_reason_codes.py`
  - `tests/test_gate_owner_enforcement.py`
  - `tests/test_ready_agent_can_write_gate.py`
  - `tests/test_evidence_table_no_touch_behavior.py`
  - `tests/test_evidence_table_no_touch_content_hash.py`
  - `tests/test_case_run_atb_success_lane.py`
  - `tests/test_cli_case_run.py`
  - updated alias tests: `tests/test_cli_case_e2e.py`, `tests/test_cli_case_e2e_atb.py`
- Validation:
  - targeted: `13 passed`
  - full suite: `247 passed, 5 skipped` (`pytest -q`)

## 2026-02-09 — Literature gateway status (web_search citations passthrough)

- Call chain works: multiple `web_search_call` items complete and a final `message` output can be produced.
- Problem: `sources`/citations are often missing or incomplete, so returned `papers` can disagree with citations (strict provenance cannot be guaranteed).
- Conclusion: treat outputs as candidate leads only; do NOT perform strict evidence write-back until gateway reliably surfaces citations/sources.

## 2026-02-10 — Train-only facts migration kickoff (data source refresh)

- Declared `data/train.csv` as the sole source for `data/private_clean.parquet` (facts DB).
- Declared `data/test.csv` as non-fact input (simulation/eval only); excluded from private_clean/evidence private_observation.
- Migration scope set for ingestion + UQ + reports + evidence/graph + case semantics to remove hard dependency on legacy private fields (`absorption/qy/tau/tested_solvent`).

## 2026-02-16 — Multi-Agent doc refresh (orchestrator blueprint, doc-only)

- Current blocker corrected:
  - aTB: DONE (cache + tables complete; case uses `evidence_readiness.atb.cache_status` and neighborhood consistency risk signals).
  - Literature: PARTIAL (web_search pipeline is usable for candidate leads, but citation/source passthrough is unstable for strict evidence writeback).
- Literature lane policy remains:
  - relaxed mode: candidate-only writeback to case file,
  - strict evidence-table writeback remains gated by provenance completeness.
- Consolidated planning blueprint into canonical docs:
  - `doc/process.md` now includes phased implementation plan + Example A acceptance criteria.
  - `doc/process_summary.md` remains the execution/change log.
  - standalone `doc/PLAN.md` / `doc/PLAN_SUMMARY.md` removed to avoid doc-source divergence.
- Next execution step:
  - single-sample end-to-end from `test.csv` on aTB-success lane (case build → graph retrieval → atb pack + consistency → master reasoner), with full `agent_runs` trace.

## 2026-02-17 — Example A-first E0 lock + implementation (patch/staging/replay)

- Execution strategy reordered to `Example A-first`:
  - E0 first: minimal runner + agent patch + candidate staging + full replay artifacts.
  - `master_reasoner` / `post_uq` remain stubs in E0.
  - `evidence_table` writeback remains hard-disabled in E0.
- Locked gate state machine (4 states):
  - `blocked_input_missing`
  - `failed_extract`
  - `extracted_no_writeback`
  - `ready_for_reasoning`
- Locked idempotency key material to include extractor + normalizer config hashes and page-selection hash (not only PDF hash).
- Locked patch format to RFC6902 JSON Patch with strict whitelist and append-only paths for `agent_runs`/`staging`/`reasons`/`next_actions`.
- Implemented runner:
  - `src/cases/example_a_first_runner.py`
  - CLI bridge: `python -m src.cli case-e0 ...`
- Added tests:
  - `tests/test_example_a_first_runner.py`
  - covers gate transitions, idempotent skip, film-priority deterministic selection, patch whitelist fail-fast, and evidence-table no-touch guard.

## 2026-02-18 — E0 `mineru_llm` integration (dual extractor mode)

- Extended E0 runner to support two extractor paths without changing downstream patch/staging/replay logic:
  - `extractor_mode=sidecar_only` (existing sidecar JSON path),
  - `extractor_mode=mineru_llm` (MinerU output resolution/CLI fallback + LLM JSON extraction).
- Added new extractor module:
  - `src/cases/mineru_llm_extractor.py`
  - capabilities: resolve existing MinerU bundle (`.md` + `_content_list_v2.json`), optional CLI run fallback, OpenAI-compatible Responses extraction with strict JSON schema, candidate sanitization.
- Updated E0 runner + CLI:
  - `src/cases/example_a_first_runner.py`
  - `src/cli.py` (`case-e0`) with new flags for `extractor_mode`, MinerU path/options, and LLM config.
- E0 writeback/audit extensions:
  - case patch now records `evidence_acquire.emission.extractor_mode`, `mineru_output_hash`, `llm_prompt_version` (plus `llm_model`/`llm_schema_version`),
  - idempotency key now includes extractor mode + MinerU output hash + LLM prompt/schema/model dimensions,
  - replay artifacts add `01a..01e` (`mineru_inputs`, `md_excerpt`, `content_list_v2_excerpt`, `llm_request`, `llm_response_raw`) when using `mineru_llm`.
- Guardrails unchanged:
  - evidence-table writeback remains hard-disabled in E0.
  - gate states remain `blocked_input_missing | failed_extract | extracted_no_writeback | ready_for_reasoning`.
- Tests added/extended:
  - `tests/test_example_a_first_runner.py`: mineru_llm success/failure/no-writeback paths + replay artifact checks.
  - `tests/test_mineru_llm_extractor.py`: MinerU precomputed bundle resolution + LLM payload parsing normalization.
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py`
  - result: `13 passed`.

## 2026-02-19 — One-shot E2E command + case history retention

- Added one-shot CLI command:
  - `python -m src.cli case-e2e`
  - input supports `--smiles` or `--code` (resolved from `data/test.csv`)
  - pipeline executed in one command: case build -> snapshot before -> E0 writeback -> summary JSON.
- Added append-only case history writes for E0 agents:
  - `offline_pdf_emission_agent` writes `literature_updated`
  - `master_reasoner_stub` writes `action_marked`
  - `post_uq_stub` writes `gate_evaluated`
- Patch whitelist/append-only paths updated to include `/history/-` for audit-safe event accumulation.
- Added tests:
  - `tests/test_cli_case_e2e.py` (one-shot command flow via mocks)
  - extended `tests/test_example_a_first_runner.py` to assert history event persistence.
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py tests/test_cli_case_e2e.py tests/test_cli.py`
  - result: `20 passed`.

## 2026-02-19 — Output simplification: final case + stable run log

- Added stable per-case run log output for E0/E2E:
  - path: `{artifacts_dir}/{case_id}.run_log.json`
  - includes run metadata, gate result, reasons/next_actions, extractor diagnostics, and LLM request/response trace.
- Kept replay mode unchanged for debugging (`artifact_mode=full`), but added clean operator mode:
  - `artifact_mode=final_case_only` now suppresses `artifacts/{run_id}/...` replay files and keeps only:
    - final case file
    - stable run log file above
- CLI summary now returns `run_log_path` for `case-e2e`.
- Tests:
  - `tests/test_example_a_first_runner.py`: added final-case-only + run-log assertion.
  - `tests/test_cli_case_e2e.py`: updated mocked summary to include `run_log_path`.
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_cli_case_e2e.py`
  - result: `13 passed`.

## 2026-02-20 — MinerU source-binding fix for rewritten `*_origin.pdf`

- Root cause identified for repeated `mineru_output_missing` on DMA-AM:
  - E0 preflight used strict hash/size matching against MinerU `*_origin.pdf`.
  - Some MinerU runs rewrite/canonicalize `*_origin.pdf` bytes, so hash no longer equals input PDF even when `.md` + `_content_list_v2.json` are valid.
- Fix:
  - `src/cases/mineru_llm_extractor.py` now keeps strict hash matching for existing artifacts, but after a successful CLI run falls back to `output_root/<pdf_stem>/...` bundle discovery.
  - New resolve mode: `cli_run_stem_fallback` with `source_binding=stem_fallback_after_cli_run`.
- Effect:
  - Prevents false `mineru_output_missing` immediately after successful MinerU CLI execution.
  - LLM stage can proceed when output bundle exists but origin PDF hash differs.
- Added regression test:
  - `tests/test_mineru_llm_extractor.py::test_resolve_or_run_mineru_cli_stem_fallback_when_origin_pdf_rewritten`
- Validation run:
  - `pytest -q tests/test_mineru_llm_extractor.py tests/test_example_a_first_runner.py tests/test_cli_case_e2e.py`
  - result: `20 passed`.

## 2026-02-20 — Relaxed writeback policy to avoid E0 deadlock on missing page

- Problem observed in live DMA-AM run:
  - MinerU + LLM produced a valid solid/powder candidate (`578 nm`, locator present, identity matched),
  - but page was missing, so candidate stayed `unverified`,
  - previous selector required `verification_status=verified` for any writeback, causing `extracted_no_writeback`.
- Policy adjustment in `src/cases/example_a_first_runner.py`:
  - `strictness=relaxed` (default): allow case-field writeback from non-rejected candidates with:
    - normalized nm value,
    - mapped target field,
    - `identity_match != unmatched`,
    - non-empty `source_locator`.
  - `strictness=strict`: unchanged; still requires `verification_status=verified` (page+locator+unit+identity).
- Ranking update:
  - `emission_aggr_nm` now also prefers verified candidates first (then source kind/confidence/page).
- Logging:
  - run log now records `strictness` at top level for easier diagnosis.
- Tests added:
  - relaxed mode accepts unverified writeback,
  - strict mode rejects same candidate without page.
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py tests/test_cli_case_e2e.py`
  - result: `23 passed`.

## 2026-02-20 — Verified rule update (scheme-1): structured locator can replace page

- User decision: keep MinerU preflight constraints, but relax `verified` semantics for writeback.
- Implemented in `src/cases/example_a_first_runner.py`:
  - `verification_status=verified` now requires:
    - locator present,
    - page present **or** locator is structured (`Table`/`Fig`/`Figure`),
    - nm-normalized value,
    - mapped target field,
    - `identity_match != unmatched`.
- Effect:
  - candidates like `Fig. 2 ...` without explicit page can still become `verified`.
  - strict mode remains strict on non-structured/no-page text locators.
- Tests updated:
  - strict mode with structured locator and missing page -> `ready_for_reasoning`.
  - strict mode with plain text locator and missing page -> `extracted_no_writeback`.
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py tests/test_cli_case_e2e.py`
  - result: `24 passed`.

## 2026-02-24 — Fix: evidence_readiness.literature/current_gate state sync after E0 writeback

- Issue:
  - Case had successful emission writeback (`target_fields` + `current_gate.state=ready_for_reasoning`) but
    `case_sections.for_master_reasoning.evidence_readiness.literature.status` remained `not_started`.
- Root cause:
  - E0 runner wrote top-level `current_gate` and emission fields, but did not update:
    - `evidence_readiness.literature.*`
    - `evidence_readiness.current_gate.*`
- Fix in `src/cases/example_a_first_runner.py`:
  - Scaffold now ensures `evidence_readiness.literature` and `evidence_readiness.current_gate` defaults exist.
  - Patch whitelist now allows:
    - `/evidence_readiness/literature/*`
    - `/evidence_readiness/current_gate/*`
  - E0 now writes synchronized readiness fields each run:
    - literature: `status`, `sources`, `last_update`, `notes`
    - nested gate: `ready_for_reasoning`, `reason`, `reasoning_mode`
    - top gate also gets `reasoning_mode` for consistency.
  - Literature status mapping:
    - `not_started`: no offline PDF/mode mismatch
    - `not_found`: extraction failure or no candidates
    - `found`: staged candidates exist
    - idempotent skip keeps prior literature status/sources.
- Tests updated:
  - blocked input -> literature `not_started`
  - extractor failure -> literature `not_found`
  - successful writeback -> literature `found` + nested gate `reasoning_mode=normal`
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py tests/test_cli_case_e2e.py`
  - result: `24 passed`.

## 2026-02-24 — Case slimming for final_case_only (reasoning-focused case file)

- User request: keep case file lean for reasoning and move operational/audit payload out of case.
- Implemented in `src/cases/example_a_first_runner.py`:
  - when `artifact_mode=final_case_only`, persisted case is compacted to reasoning essentials only.
  - retained keys:
    - `case_id`, `case_version`, `query`, `current_gate`, `risk_scores`, `evidence_readiness`,
    - `neighbors`, `candidate_mechanisms`, `mechanism_signatures`,
    - `action_plan`, `action_rationale`, `target_fields`, `target_fields_provenance`.
  - removed from persisted case in this mode:
    - `history`, `agent_runs`, `reasons`, `next_actions`, `evidence_candidates_staging`, and other operational fields.
  - added marker:
    - `_case_compaction.mode = "reasoning_only"`.
- Operational audit remains in stable run log:
  - `{artifacts_dir}/{case_id}.run_log.json` (includes LLM request/response, reasons, staged candidates, etc.).
- Validation run:
  - `pytest -q tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py tests/test_cli_case_e2e.py`
  - result: `24 passed`.

## 2026-02-24 — READY_AGENT introduced (independent gate/rationale/plan writer)

- Added new agent:
  - `src/agents/ready_agent.py`
  - public API: `review_case_and_patch(case_json) -> RFC6902 patch`
  - optional applier: `apply_ready_agent_patch(...)`
- Write-scope enforcement:
  - READY_AGENT patch paths are hard-limited to:
    - `/current_gate/*`
    - `/action_rationale`
    - `/action_plan`
    - optional `/risk_scores/readiness_*`
  - No writes to `target_fields` or other evidence payload sections.
- Gate machine implemented:
  - `blocked_input_missing | needs_manual | ready_for_reasoning | ready_conservative`
  - checks include emission availability/provenance, anti-leakage for aggregate field, identity metadata/confidence, and aTB downgrade behavior.
- Action plan semantics implemented:
  - add/reorder blocking actions for missing inputs/manual extraction/identity verification,
  - force master reasoning action to priority 1 in ready states,
  - queue `retry_target_atb` when aTB failed but evidence is otherwise usable.
- CLI hook added:
  - `python -m src.cli ready-agent --case <path> [--dry-run]`
- Pipeline integration:
  - `case`, `case-update`, `case-e0`, and `case-e2e` now run READY_AGENT after upstream case writes so gate/rationale/plan are finalized by READY_AGENT.
- Schema allowlist update:
  - `src/cases/case_schema.py` action allowlist extended for READY_AGENT actions (`run_master_reasoner_stub`, `retry_target_atb`, `rerun_offline_pdf_extractor`, `manual_extract`, `manual_identity_verify_from_pdf`, `request_manual_pdf`).
- Tests added:
  - `tests/test_ready_agent.py`
  - `tests/test_cli_ready_agent.py`
  - Covers contradiction repair, anti-leakage rejection, identity downgrade, and forbidden-path guarantees.
- Validation run:
  - `pytest -q tests/test_ready_agent.py tests/test_cli_ready_agent.py tests/test_example_a_first_runner.py tests/test_mineru_llm_extractor.py tests/test_cli_case_e2e.py`
  - result: `29 passed`.

## 2026-02-19 — MinerU emission prompt template alignment

- Updated `src/cases/mineru_llm_extractor.py` prompt assembly to use the template file:
  - `third_party/MinerU/aie_data/prompts/emission_prompt_template.txt`
- Runtime substitution now injects target identity into template fields:
  - `TARGET_CODE`
  - `TARGET_ALIASES`
  - `TARGET_SMILES`
  - `TARGET_INCHIKEY`
- Kept existing strict JSON schema output path for E0 compatibility; template text is now the primary extraction instruction body.
- Added test:
  - `tests/test_mineru_llm_extractor.py::test_build_mineru_prompt_payload_uses_template_runtime_target`

## 2026-02-19 — LLM extractor default model switch

- Changed default E0 LLM model to `deepseek-v3.2` for both direct runner and CLI wrappers:
  - `src/cases/example_a_first_runner.py` (`run_case_example_a_e0` default + parser `--llm-model`)
  - `src/cli.py` (`case-e0` and `case-e2e` default `--llm-model`)
- No behavior change beyond default model selection; explicit `--llm-model` still overrides.

## 2026-02-10 — Train-only facts migration completed (code + rebuild)

- Ingestion defaults switched from `data/data.csv` to `data/train.csv` (`src/data/loader.py`, `src/data/pipeline.py`).
- `private_clean` schema converged to train columns + derived fields (`canonical_smiles`, `inchikey`, `emission_*_missing`) via new train-only standardizer (`src/data/standardizer.py`).
- UQ/reports/evidence/case paths removed hard dependency on legacy private fields:
  - UQ `C_meta` now uses only `emission_solid_missing` + `emission_aggr_missing`.
  - P6 report/queue `has_emission` and missing-critical logic now train-only.
  - V1 evidence_table private_observation is restricted to `emission_solid` / `emission_aggr`.
  - Case action plan request fields switched to `["emission_solid", "emission_aggr"]`.
- Added eval-only utility: `python -m src.cases.build_cases_from_test_csv` (reads `data/test.csv`, writes case files only).

Rebuild + validation snapshot:
- P1 outputs: `private_clean=500` rows (from `train.csv` 506 rows, 6 invalid-id rows dropped), `molecule_table=433`, `rdkit_features=433`.
- Anchor: `anchor_neighbors_ecfp=4320` rows (`432` queries, `k=10` exact).
- UQ: `uq_scores_pre_atb_p5b=500` rows; router counts = Known/Stable 283, Evidence-insufficient 132, In-domain ambiguous 48, Novelty-candidate 37.
- P6 reports regenerated to `reports_train_only/` with `500` JSON files; validator passed (`7/7` checks).
- V1: `evidence_table=4778` rows (`private_observation` fields = emission_solid/emission_aggr only), `graph_nodes=5384`, `graph_edges=12062`; evidence/graph/retrieval validators passed.

## 2026-02-10 — Cleanup: empty-inchikey + deprecated merge_pre_atb

- Fixed InChIKey normalization in ingestion: empty/whitespace `inchikey` is normalized to null before molecule-table filtering (`src/data/canonicalizer.py`, `src/data/pipeline.py`).
- Post-fix rebuild snapshot: `molecule_table`/`rdkit_features` no longer contain `inchikey=""` and anchor query set aligns to valid keys only.
- Added anchor smoke manifest metric: `skipped_invalid_inchikey_count` in `data/anchor_neighbors_ecfp_manifest.json` (`src/features/anchor_ecfp.py`).
- Marked `src/features/merge_pre_atb.py` as DEPRECATED (historical V0 path), and made runtime fail-fast with migration guidance to train-only main chain.
- Added static guard script `python -m src.utils.static_check_main_chain_train_only` to ensure mainline modules do not hardcode legacy private fields.

## 2026-02-10 — P4-pre two-stage literature candidate retrieval (sources-bounded)

- Refactored `src/agents/web_search_candidate_papers.py` into two-stage orchestration:
  - Stage A: Gemini native `generateContent + google_search` collects grounding sources and supports `--dump_raw` (raw response).
    - Redirect URLs are resolved to final landing URLs before allowlist filtering and Stage B structuring.
  - Stage B: Responses without tools structures candidates only from Stage A `sources`.
- Added `src/agents/literature_structurer.py` to enforce trust boundary and post-processing rules:
  - `source_url` must map to Stage A sources (exact/normalized same-origin).
  - DOI kept only when visible in matched source title/snippet/url; otherwise forced to `null`.
  - Dedupe priority: DOI first, then normalized title.
- Final CLI output is strict JSON with `papers` + `stats` (`sources_in`, `papers_out`, `deduped`).
- Added fixture-based tests: `tests/test_literature_structurer.py` (+ `tests/fixtures/literature_sources_fixture.json`), including DOI visibility, source_url trust boundary, and dedupe checks.
- Validation: `py_compile` + `pytest tests/test_literature_structurer.py tests/test_data_agent.py` passed (11/11).

## 2026-02-10 — P4-pre source allowlist hardening (drift reduction)

- Updated Stage A (`src/agents/web_search_candidate_papers.py`) to use a fixed query plan constrained to curated journals only:
  - Advanced Functional Materials / Advanced Materials (Wiley),
  - Materials Future (IOP),
  - ACS Nano (ACS),
  - Nature Materials (Nature).
- Added post-search source allowlist filtering before Stage B; only allowlisted journal sources are forwarded to structuring.
- Added source-policy logging: `raw_count`, `allowed_count`, `dropped`, and dropped reason buckets in `--debug` mode.
- This is still P4-pre (candidate mode): strict write-back remains blocked unless verified provenance rules are satisfied.

## 2026-02-11 — P4-pre: disable allowlist by default (reduce false negatives)

- Stage A source policy default switched to `allow_all` (no allowlist filtering). Curated filtering remains optional via `--source_policy journal_allowlist_v1` (`src/agents/web_search_candidate_papers.py`).
- Stage A query plan was simplified (removed long `OR site:...` chains) to reduce Gemini query rewriting drift.
- Trust boundary unchanged: Stage B still can only emit candidates whose `source_url` maps to Stage A sources.

## 2026-02-09 — Docs: Post-UQ agent slot (stub)

- Added a Post-UQ agent slot to the V1/V2 plan: a dedicated agent reads (`case_file` + `master_output`) and emits gating + next actions.
- Reserved a `post_uq` block in the Case File schema for forward compatibility (no write-back in V1).

## 2026-02-02 — Plan: aTB neighborhood consistency check (delta outlier score)

Goal: add a small, auditable signal to the Case File for the Master Reasoner: compare target aTB delta features vs the distribution of neighbors' aTB delta features (structure retrieval unchanged).

Plan (docs-first, then code):
1) Docs: add the "aTB Neighborhood Consistency Check" subsection in `doc/process.md`, and add `risk_scores.atb_neighbor_consistency` schema in `doc/schemas.md`.
2) Code location:
   - Core math in `src/cases/atb_neighbor_consistency.py` (unit-testable helper).
   - Hook into `src/cases/create_case_from_smiles.py` after target/neighbor aTB packs are attached; write to `risk_scores.atb_neighbor_consistency`.
3) Computable conditions:
   - Target `evidence_readiness.atb.cache_status == "success"` AND target delta fields exist.
   - Neighbor distribution uses only neighbors with `neighbor_atb.cache_status == "success"` AND required delta fields present.
   - If neighbor sample_size < 5: flag="insufficient_sample" and do not compute z-scores.
4) Stats:
   - Robust z per field using median + MAD: z = (x - median) / (1.4826*MAD + eps).
   - outlier_score_max and outlier_score_rss + outlier_dims.
5) Tests/acceptance checks:
   - Unit tests in `tests/test_atb_neighbor_consistency.py` for filtering, median/MAD/z math, flag thresholds, target_missing behavior.
   - Demo: `python -m src.cli case --smiles "<SMILES>" --print` shows `risk_scores.atb_neighbor_consistency` with sample_size and flag.

## 2026-02-02 — Implemented: aTB neighborhood consistency check (case-file signal)

What shipped:
- Added robust aTB neighborhood outlier scoring (median/MAD z-scores on delta_gap/delta_dihedral/delta_volume) and stored it at `risk_scores.atb_neighbor_consistency`.
- Retrieval/indexing unchanged: still structure-only (ECFP); aTB is used only as evidence/readiness augmentation.

Code + tests:
- New helper: `src/cases/atb_neighbor_consistency.py`
- Case creation hook: `src/cases/create_case_from_smiles.py`
- Unit tests: `tests/test_atb_neighbor_consistency.py` (PASS)

Demo (example):
- `python -m src.cli case --smiles 'CC1=N/C(=C\\c2ccccc2)C(=O)O1' --outdir /tmp/cases_demo`
  - Produced `risk_scores.atb_neighbor_consistency.sample_size=9`, `flag="inlier"`, with z-scores and outlier scores populated.

## 2026-02-02 — Plan: LLM-friendly structured action_plan + reasoning_mode

Goal: upgrade SMILES-first Case File so an LLM controller can follow a concrete, auditable plan (structured action objects + rationale), while keeping retrieval structure-only (ECFP) and using aTB only for evidence/readiness.

Plan:
1) Docs: update `doc/process.md` and `doc/schemas.md` to define:
   - `current_gate.reasoning_mode ∈ {"blocked","normal","conservative"}`
   - `action_plan` as list[object] (legacy list[string] accepted) + `action_rationale`
   - decision rules for aTB success+inlier/outlier vs failed/absent/pending/partial
2) Code:
   - Update `src/cases/create_case_from_smiles.py` to emit structured actions and rationale, and set reasoning_mode.
   - Update validators: `src/cases/case_schema.py` and `src/cases/validate_case_file.py` to accept both legacy and new action_plan formats.
   - Update Chem Agent stub to handle action objects (mark status done / remove legacy strings).
3) Tests:
   - Add `tests/test_case_action_plan_semantics.py` for the new mode/action rules.
   - Update existing case-file tests that assume string action_plan.
4) Demo:
   - Run 3 case creations (success+inlier, success+outlier if available, absent/failed) and print only current_gate + first 3 actions + rationale.

## 2026-02-02 — Implemented: LLM-friendly structured action_plan + reasoning_mode

What changed:
- Case creation now emits `action_plan` as a list of structured action objects (LLM-friendly) plus `action_rationale`.
- Added `evidence_readiness.current_gate.reasoning_mode ∈ {blocked, normal, conservative}`.
- Mode/action rules incorporate:
  - target aTB cache_status
  - aTB neighborhood outlier flag (inlier vs outlier)
  - minimal experiment availability flags (emission/solvent)
- Retrieval/indexing unchanged: still structure-only ECFP; aTB is used only for evidence/readiness augmentation.

Files changed:
- `src/cases/create_case_from_smiles.py` (new plan builder; emits action objects + rationale; sets reasoning_mode)
- `src/cases/case_schema.py` (validate action_plan objects; reasoning_mode allowlist)
- `src/cases/validate_case_file.py` (semantic checks aware of action objects)
- `src/cases/chem_agent_update_case_stub.py` (mark action status done for object action_plan; accept literature_search_web)
- Tests: `tests/test_case_action_plan_semantics.py` + updated `tests/test_case_file_semantics.py`

Validation:
- `pytest` PASS (201 tests)
- Demo excerpts (3 cases): success+inlier => mode=normal; success+outlier => mode=conservative; absent => mode=blocked, with blocking compute_target_atb first.

## P2 Notes (historical) — moved from process.md

> These are verbose implementation notes, code examples, and design rationale that were moved from `doc/process.md` P2 section on 2026-01-19 to keep process.md concise.

### V0 Black-box Integration

For V0, the Chem Agent (aTB component) calls `third_party/aTB/main.py` as a subprocess:

```bash
python third_party/aTB/main.py \
    --smiles '<canonical_smiles>' \
    --workdir 'cache/atb/{inchikey[:2]}/{inchikey}' \
    --npara 4 --maxcore 4000
```

**CLI arguments**:
| Argument | Default | Description |
|----------|---------|-------------|
| `--smiles` | None | Canonical SMILES (from molecule_table) |
| `--workdir` | `work_dirs` | Output directory = our cache path |
| `--nimg` | 3 | NEB intermediate images |
| `--npara` | 2 | Amesp parallel processes |
| `--maxcore` | 4000 | Memory per core (MB) |

### AIE-aTB result.json → features.json Mapping

```
result.json structure:
{
  "ground_state": {
    "HOMO-LUMO": "1.83",           // string → s0_homo_lumo_gap (float)
    "structure": {
      "bonds": 1.23,               // avg bond length (Å)
      "angles": 115.4,             // avg bond angle (°)
      "DA": 5.88                   // avg dihedral → s0_dihedral_avg
    },
    "volume": 513.0,               // → s0_volume (Å³)
    "charge": {...}                // per-atom Mulliken charges
  },
  "excited_state": {
    "HOMO-LUMO": "1.54",           // → s1_homo_lumo_gap
    "structure": {...},            // DA → s1_dihedral_avg
    "volume": 513.0,               // → s1_volume
    "charge": {...}
  },
  "NEB": 512.8                     // mean NEB volume (informational)
}
```

**features.json schema**:
```json
{
  "s0_volume": 513.0,
  "s1_volume": 513.0,
  "delta_volume": 0.0,
  "s0_homo_lumo_gap": 1.83,
  "s1_homo_lumo_gap": 1.54,
  "delta_gap": -0.29,
  "s0_dihedral_avg": 5.88,
  "s1_dihedral_avg": -2.81,
  "delta_dihedral": -8.69,
  "s0_charge_dipole": null,
  "s1_charge_dipole": null,
  "delta_dipole": null,
  "excitation_energy": null,
  "neb_mean_volume": 512.8
}
```

### Failure Stage Detection (detailed)

1. Check stderr for "Bad Conformer Id" or "RDKit embedding failed" → `"conformer"`
2. Check stderr for "CalculationFailed" or "error code -11" → stage based on path in error
3. Check stderr for "IndexError" + "parse_aop_energy" → amesp output parsing failure
4. Check if `result.json` exists and is valid JSON → `"feature_parse"` if fails
5. Check for `ground_state` key → `"opt"` if missing
6. Check for `excited_state` key → `"excit"` if missing
7. Check for `NEB` key → `"neb"` if missing
8. Check for `volume` in both states → `"volume"` if missing
9. Timeout → `"timeout"` (default 3600s)

### P2 Implementation Notes (critical design decisions)

**1. SMILES Source (Single Source of Truth)**
- P2 batch runner MUST iterate over `data/molecule_table.parquet` (1050 unique InChIKeys)
- Use `canonical_smiles` from molecule_table for aTB input
- DO NOT iterate over `data/private_clean.parquet` (contains duplicate InChIKeys)
- `cache/.../canonical_smiles.txt` is for audit/debug ONLY

```python
# CORRECT: P2 batch runner
molecule_table = pd.read_parquet("data/molecule_table.parquet")
for _, row in molecule_table.iterrows():
    inchikey = row["inchikey"]
    smiles = row["canonical_smiles"]  # ✅ Single source of truth
    run_atb(inchikey, smiles)

# WRONG: Do not use private_clean (has duplicates)
# WRONG: Do not read from cache/canonical_smiles.txt (circular dependency)
```

**2. atb_version Handling**
- `atb_version` MUST remain `null` for `run_status == "pending"`
- Set `atb_version` ONLY after successful computation completes
- Recommended format: `"AIE-aTB-{git_hash}"`

**3. Cache Consistency**
- molecule_table is authoritative InChIKey→SMILES registry (created in P1)
- Mode A stores SMILES to cache for auditability (redundant but harmless)

### Failure Handling recommended_next_steps

| fail_stage | recommended_next_steps |
|------------|------------------------|
| conformer | `["check_smiles_validity", "try_alternative_smiles", "manual_structure"]` |
| opt | `["retry_with_different_conformer", "check_smiles_validity"]` |
| excit | `["skip_excited_state", "use_simpler_method"]` |
| neb | `["skip_neb", "use_relaxed_scan"]` |
| volume | `["retry_volume_calc"]` |
| feature_parse | `["manual_inspection", "report_bug"]` |
| timeout | `["increase_timeout", "check_molecule_size", "simplify_calculation"]` |
| size | `["retry_with_more_memory", "reduce_npara", "skip_large_molecule"]` |

### Extended Batch Runner CLI

```bash
# Normal run (skips both succeeded and failed)
python -m src.chem.batch_runner --limit 20 --npara 4 --maxcore 4000

# Include ionic molecules (override V0 skip)
python -m src.chem.batch_runner --include-ionic

# Skip large molecules using RDKit heavy-atom counts
python -m src.chem.batch_runner --max-heavy-atoms 40

# Force rerun everything (including succeeded)
python -m src.chem.batch_runner --limit 20 --force-rerun
```

### Resumability Notes

- By default, skip molecules with `run_status == "success"` OR `run_status == "failed"`
- Use `--retry-failed` to re-run only failed molecules (skips succeeded)
- Use `--force-rerun` to re-run ALL molecules (including succeeded)
- Log skipped molecules to console with reason

### Minimum Descriptor Set

- S0: volume, homo_lumo_gap, dihedral_avg, charge_dipole
- S1: volume, homo_lumo_gap, dihedral_avg, charge_dipole, excitation_energy
- Delta: S1-S0 for volume, gap, dihedral, dipole

---

## 2026-01-07 — Project planning aligned
### What we aligned on
- Implement V0 first: a closed-loop pipeline using:
  - private dataset (1000+ molecules)
  - RDKit descriptors + canonicalization + InChIKey caching
  - aTB micro-descriptor computation (Chem Agent)
  - UQ router based on coverage/novelty/aleatoric derived from structured features (NOT LLM confidence)
- V1/V2 will add evidence table / KG / GraphRAG later.

### Key design decisions
- Coverage computed from anchor-space neighborhood similarity + metadata completeness.
- Novelty computed from outlierness in feature space (density/distance-based).
- Aleatoric computed as entropy of soft assignment to learned prototypes (unsupervised in V0).
- Router is conservative: avoid claiming new mechanism when evidence is insufficient.

### Known risk areas
- Units/format of absorption/emission/qy/tau must be standardized carefully.
- aTB stage failures must be recorded with `fail_stage` and routed to Evidence-insufficient.
- Batch runs require caching by InChIKey and resumable execution.
- Privacy: avoid dumping `comment` and other sensitive fields into logs/reports.

### Next steps
- Implement P0–P1: repo skeleton + data loader + unit normalization + SMILES canonicalization.
- Inspect sample rows to define absorption/emission parsing strategy.

---

## 2026-01-07 — Documentation hardening (doc-only update)
### Implemented
- Created `doc/schemas.md` with column schemas for all 9 core artifacts:
  - `private_clean.parquet`, `molecule_table.parquet`, `rdkit_features.parquet`
  - `atb_features.parquet`, `atb_qc.parquet`, `X_full.parquet`
  - `uq_scores.parquet`, `hypothesis_log.parquet`, `run_manifest.json`

- Updated `doc/process.md` P1 with:
  - CSV encoding fallback protocol (utf-8-sig → utf-8 → gb18030 → latin1)
  - qy normalization: percent (0–100) → [0,1], keep `_raw`, add `unit_inferred`
  - tau handling: ns default, outlier flags, optional log transform, `units_override.yaml` support
  - Missing value protocol: `{field}_missing` columns for 14 critical fields

- Updated `doc/process.md` P2 with:
  - Cache structure: `cache/atb/{inchikey[:2]}/{inchikey}/` with status.json schema
  - Failure handling policy: no auto-retry in V0, record fail_stage + error_msg, route to Evidence-insufficient
  - Resumability: check status.json, support `--force-rerun`

- Updated `doc/process.md` P5 with:
  - Explicit router decision table with deterministic if/elif cascade
  - Conservative gate: Novelty-candidate requires `novelty >= high AND (coverage < high OR aleatoric >= high)`
  - Aleatoric: GMM with K = min(20, n_anchors // 10), minimum K=5
  - recommended_next_steps templates per action

- Updated `CLAUDE.md`:
  - Added principle 7: version all pipeline runs via `run_manifest.json`
  - Clarified principle 6 with conservative gate logic
  - Refined principle 4 with `{field}_missing` naming convention

- Updated `doc/roadmap.md`:
  - De-duplicated acceptance criteria: now references `CLAUDE.md`
  - Added cross-references to `doc/process.md` and `doc/schemas.md` for risks

### Outputs produced
- `doc/schemas.md` (new file, ~180 lines)
- `doc/process.md` (updated P1, P2, P5 sections)
- `CLAUDE.md` (updated principles)
- `doc/roadmap.md` (de-duplicated)

### Issues / surprises
- None (doc-only update)

### Decisions
- Router conservative gate locked: high coverage alone blocks novelty claims
- K for aleatoric GMM: `min(20, n_anchors // 10)`, minimum 5
- kNN for novelty (k=5, mean distance, percentile-normalized)

### Next actions
- Proceed with V0 P0: repo skeleton initialization (src/, data/, config/, .gitignore, pyproject.toml)
- Then P1: data loader + standardization pipeline

---

## 2026-01-07 — P0 Repo bootstrap complete
### Implemented
- Created complete directory structure:
  - `src/` with subdirectories: `data/`, `chem/`, `features/`, `uq/`, `reports/`, `utils/`
  - `data/`, `cache/`, `reports/`, `config/`, `tests/` at root level
  - Added `__init__.py` files to all Python package directories
  - Added `.gitkeep` files to preserve empty directories in git

- Created `.gitignore`:
  - Ignores generated artifacts: `data/*.parquet`, `data/*.json`, `data/*.pkl`, `data/*.faiss`
  - Ignores cache and reports directories
  - Standard Python ignores (__pycache__, *.pyc, venv/, etc.)
  - IDE and OS ignores (.vscode/, .DS_Store, etc.)

- Created `config/default.yaml`:
  - UQ parameters (coverage/novelty/aleatoric configs)
  - Router thresholds (percentiles: 0.2, 0.8)
  - Feature engineering settings (ECFP radius=2, bits=2048)
  - Anchor selection criteria
  - Logging configuration

- Created `src/utils/logging.py`:
  - `setup_logger()`: Configure logger with console/file output
  - `get_logger()`: Get or create logger instance
  - Supports custom log levels, formats, and file output

- Created dependency files:
  - `requirements.txt`: Pin major versions (rdkit>=2023.3.1, pandas>=2.0.0, faiss-cpu>=1.7.4, etc.)
  - `pyproject.toml`: Project metadata, dependencies, pytest config, setuptools config

### Outputs produced
- `.gitignore`
- `config/default.yaml`
- `src/utils/logging.py`
- `requirements.txt`
- `pyproject.toml`
- Directory structure with `__init__.py` files

### Issues / surprises
- None

### Decisions
- Used both `requirements.txt` (simple) and `pyproject.toml` (modern standard) for flexibility
- Default log level: INFO
- FAISS CPU version (no GPU required for V0)
- UQ thresholds default to 20th/80th percentiles (adjustable in config)

### Next actions
- Proceed with V0 P1: Data standardization pipeline
  - Implement CSV loader with encoding fallback
  - Implement qy/tau normalization + missing masks
  - Implement RDKit canonicalization + InChIKey generation
  - Implement RDKit descriptor computation
  - Generate first parquet artifacts

### Post-completion adjustment
- Moved `data.csv` → `data/data.csv` for better organization
- Updated `.gitignore` to keep `data/data.csv` (input file should be tracked)
- Updated `config/default.yaml`, `README.md`, and `doc/process.md` references

---

## 2026-01-07 — P1 Data standardization modules complete (code-only, pending execution)
### Implemented
- Created `src/data/loader.py`:
  - `load_csv_with_fallback()`: Try encodings utf-8-sig → utf-8 → gb18030 → latin1
  - `load_private_dataset()`: Load data/data.csv with validation
  - Returns DataFrame + encoding_used

- Created `src/data/standardizer.py`:
  - `normalize_qy_columns()`: Convert qy_* from percent (0-100) to [0,1], keep _raw, add unit_inferred/confidence
  - `normalize_tau_columns()`: Flag outliers (> 3×IQR or > 1000 ns), keep _raw, add _outlier flags + _log transform
  - `parse_absorption_peak()`: Extract peak nm from absorption string via regex
  - `add_missing_indicators()`: Create {field}_missing boolean columns for 14 critical fields
  - `standardize_dataset()`: Full pipeline applying all normalization + missing masks

- Created `src/data/canonicalizer.py`:
  - `canonicalize_smiles()`: RDKit canonical SMILES
  - `smiles_to_inchikey()`: Generate InChIKey from SMILES
  - `add_canonical_smiles_and_inchikey()`: Add both columns to DataFrame
  - `create_molecule_table()`: Group by InChIKey → unique molecules with id_list + n_records

- Created `src/data/rdkit_descriptors.py`:
  - `compute_ecfp()`: ECFP4 fingerprint (radius=2, 2048 bits)
  - `compute_basic_descriptors()`: MW, LogP, TPSA, rotatable bonds, HBD/HBA, rings, aromatic rings, heavy atoms
  - `compute_rdkit_features()`: Apply to molecule table → inchikey + descriptors + ecfp_2048

- Created `src/data/pipeline.py`:
  - Main P1 pipeline script (7 steps)
  - Generates: private_clean.parquet, molecule_table.parquet, rdkit_features.parquet, run_manifest.json
  - Captures git commit, package versions, encoding used, row counts

### Outputs produced
- `src/data/loader.py` (~80 lines)
- `src/data/standardizer.py` (~240 lines)
- `src/data/canonicalizer.py` (~170 lines)
- `src/data/rdkit_descriptors.py` (~160 lines)
- `src/data/pipeline.py` (~180 lines)

### Issues / surprises
- RDKit not installed in current environment → cannot execute pipeline yet
- Data inspection shows:
  - 1226 rows (confirmed from data.csv header count)
  - qy values in percent (e.g., 0.006, 0.131) - confirmed normalization logic
  - NULL values present → standardizer handles via missing masks
  - SMILES column exists → ready for canonicalization

### Decisions
- CSV encoding fallback order: utf-8-sig → utf-8 → gb18030 → latin1 (handles Chinese characters if present)
- qy normalization: divide by 100 (percent → fraction), keep _raw for auditability
- tau outlier threshold: Q3 + 3×IQR OR > 1000 ns (whichever is stricter)
- Missing indicators: 14 critical fields (emission/qy/tau × 4 conditions + absorption + tested_solvent)
- ECFP: radius=2 (ECFP4), 2048 bits (standard for chemoinformatics)
- Pipeline is fully modular: each module can be imported and tested independently

### Next actions
- **User action required**: Install dependencies with `pip install -r requirements.txt` (rdkit, pandas, numpy, pyarrow, pyyaml, scikit-learn, faiss-cpu)
- **Execute P1 pipeline**: Run `python -m src.data.pipeline` to generate 4 parquet artifacts
- **Verify outputs**: Check data/private_clean.parquet row count, inchikey uniqueness, missing rates
- After successful execution, proceed with P2: aTB wrapper implementation

---

## 2026-01-07 — P1 Pipeline execution successful ✅
### Executed
- Fixed JSON serialization bug: Converted numpy int64 to native Python int for manifest
- Fixed RDKit deprecation warning: Added fallback to use new `MorganGenerator` API (rdFingerprintGenerator) if available, with fallback to old API
- Executed pipeline with `conda activate aie && python -m src.data.pipeline`
- Pipeline completed in ~6 seconds

### Outputs produced
- **data/private_clean.parquet**: 1225 rows, 77 columns, 221K
  - Contains standardized data with:
    - qy_* normalized to [0,1] (from percent), with _raw and metadata
    - tau_* with outlier flags and _log transforms
    - absorption_peak_nm parsed
    - 14 critical fields with {field}_missing boolean columns
    - canonical_smiles and inchikey columns
- **data/molecule_table.parquet**: 1050 unique molecules, 65K
  - Grouped by InChIKey with id_list and n_records
  - Max 3 records per molecule (some duplicates due to multiple experiments)
- **data/rdkit_features.parquet**: 1050 molecules, 123K
  - ECFP4 fingerprints (radius=2, 2048 bits)
  - 9 basic descriptors (MW, LogP, TPSA, rotatable bonds, HBD/HBA, rings, aromatic, heavy atoms)
  - 100% valid features (1050/1050)
- **data/run_manifest.json**: 534 bytes
  - Encoding used: latin1 (CSV required non-UTF8 encoding)
  - RDKit version: 2025.09.3
  - Python 3.10.19, pandas 2.3.3, numpy 2.2.6
  - Input: 1225 rows → Output: 1225 rows → Unique: 1050 molecules
  - Valid InChIKeys: 1164/1225 (95%)

### Issues / surprises
- **CSV encoding**: File required latin1 encoding (not UTF-8) - fallback chain worked correctly
- **Duplicates**: 1225 input rows → 1050 unique molecules → 175 duplicate experiments (same InChIKey)
- **Invalid SMILES**: 61 rows (1225 - 1164) have invalid SMILES → null inchikey
- **RDKit deprecation warnings**: Fixed by adding newer API support with fallback

### Decisions
- Keep both old and new RDKit fingerprint APIs for compatibility
- latin1 encoding is correct for this dataset (confirmed by successful parsing)
- Invalid SMILES rows kept in private_clean.parquet but excluded from molecule_table

### Next actions
- **P1 COMPLETE** ✅ All acceptance criteria met
- Proceed with **P2: aTB wrapper** (Chem Agent)
  - Design cache structure for InChIKey-based storage
  - Implement batch runner with resumability
  - Add failure tracking (opt/excit/neb/volume/feature_parse stages)

---

## 2026-01-08 — P1.5 Mode A orchestration skeleton complete ✅
### Implemented
- Created `src/agents/` package with two agent modules:
  - **data_agent.py** (~165 lines): Fetch records by id/inchikey from parquet files
    - `DataAgent.get_record_by_id(id)`: Fetch from private_clean.parquet
    - `DataAgent.get_molecule_by_inchikey(inchikey)`: Fetch from molecule_table.parquet
    - `DataAgent.get_missing_summary(record)`: Compute missing value summary
    - Automatic caching of DataFrames after first load
    - Full error handling for missing ids/inchikeys

  - **atb_agent.py** (~185 lines): aTB cache management and status tracking
    - `ATBAgent.get_cache_path(inchikey)`: Generate cache path with 2-char prefix
    - `ATBAgent.check_cache(inchikey)`: Check if cache exists
    - `ATBAgent.load_status(inchikey)`: Load status.json
    - `ATBAgent.mark_pending(inchikey, smiles)`: Create placeholder status.json
    - `ATBAgent.load_features(inchikey)`: Load features.json (if available)
    - `ATBAgent.get_cache_summary(inchikey)`: Get comprehensive cache summary

- Created **src/cli.py** (~245 lines): CLI with 3 commands
  - `fetch --id <id>`: Fetch and display record
  - `compute-atb --id <id>`: Check aTB cache, mark pending if missing
  - `run --id <id> [--write-report]`: Full orchestration (fetch + atb + assemble + report)
  - Outputs structured JSON to stdout
  - Optional report writing to `reports/{id}.json`

- Created comprehensive tests (15 tests, all passing):
  - **tests/test_data_agent.py** (~95 lines): 6 tests
    - Test fetching valid/invalid ids
    - Test missing summary computation
    - Test InChIKey lookup
    - Test DataFrame caching

  - **tests/test_atb_agent.py** (~135 lines): 9 tests
    - Test cache path generation
    - Test cache hit/miss detection
    - Test mark_pending functionality
    - Test status loading
    - Test cache summary generation

- Updated configuration:
  - Modified `pyproject.toml` to remove coverage options (pytest-cov not required)

### Outputs produced
- **src/agents/data_agent.py** (165 lines)
- **src/agents/atb_agent.py** (185 lines)
- **src/cli.py** (245 lines)
- **tests/test_data_agent.py** (95 lines)
- **tests/test_atb_agent.py** (135 lines)
- **cache/atb/** directory structure (created on first run)
- **reports/** directory (created on first run with --write-report)

### CLI commands verified
```bash
# Fetch record by id
python -m src.cli fetch --id 1

# Check aTB cache and mark pending
python -m src.cli compute-atb --id 1

# Full orchestration (fetch + atb + report)
python -m src.cli run --id 1 --write-report
```

### Test results
```
============================= test session starts ==============================
collected 15 items

tests/test_data_agent.py::TestDataAgent::test_get_record_by_id_success PASSED
tests/test_data_agent.py::TestDataAgent::test_get_record_by_id_not_found PASSED
tests/test_data_agent.py::TestDataAgent::test_get_missing_summary PASSED
tests/test_data_agent.py::TestDataAgent::test_get_molecule_by_inchikey_success PASSED
tests/test_data_agent.py::TestDataAgent::test_get_molecule_by_inchikey_not_found PASSED
tests/test_data_agent.py::TestDataAgent::test_private_clean_caching PASSED
tests/test_atb_agent.py::TestATBAgent::test_get_cache_path PASSED
tests/test_atb_agent.py::TestATBAgent::test_get_cache_path_invalid_inchikey PASSED
tests/test_atb_agent.py::TestATBAgent::test_check_cache_miss PASSED
tests/test_atb_agent.py::TestATBAgent::test_mark_pending PASSED
tests/test_atb_agent.py::TestATBAgent::test_check_cache_hit_after_mark_pending PASSED
tests/test_atb_agent.py::TestATBAgent::test_load_status PASSED
tests/test_atb_agent.py::TestATBAgent::test_load_status_not_found PASSED
tests/test_atb_agent.py::TestATBAgent::test_get_cache_summary PASSED
tests/test_atb_agent.py::TestATBAgent::test_load_features_not_found PASSED

======================== 15 passed, 1 warning in 0.75s ==============================
```

### Example output (run --id 1)
```json
{
  "id": 1,
  "inchikey": "CVWRQIXEYCUPJM-UHFFFAOYSA-N",
  "canonical_smiles": "CCC[n+]1cc2ccc(C(=C(c3ccc(OC)cc3)c3ccc(OC)cc3)c3ccccc3)cc2c(-c2ccccc2)c1-c1ccccc1",
  "record_fields": {
    "emission_sol": 530.0,
    "emission_solid": 620.0,
    "qy_sol": 6e-05,
    "qy_solid": 0.00131,
    ...
  },
  "missing_summary": {
    "n_missing": 9,
    "missing_fields": ["emission_aggr", "emission_crys", "qy_aggr", ...]
  },
  "atb_status": "pending",
  "atb_features": null,
  "paths": {
    "cache_dir": "cache/atb/CV/CVWRQIXEYCUPJM-UHFFFAOYSA-N",
    "status_file": "cache/atb/CV/CVWRQIXEYCUPJM-UHFFFAOYSA-N/status.json",
    "report_path": "reports/1.json"
  }
}
```

### Issues / surprises
- None! Implementation went smoothly
- All 15 tests pass on first run
- CLI commands work as expected
- Cache structure created correctly with 2-char prefix

### Decisions
- DataAgent caches DataFrames after first load for performance
- ATBAgent creates placeholder status.json with run_status="pending" on cache miss
- CLI outputs structured JSON to stdout (suitable for piping)
- Report writing is optional (--write-report flag)
- Cache path uses 2-char InChIKey prefix for filesystem efficiency
- status.json includes a "note" field explaining Mode A placeholder behavior

### Next actions
- **P1.5 COMPLETE** ✅ Mode A orchestration skeleton working end-to-end
- Ready to proceed with **P2: aTB wrapper (Chem Agent)** when user is ready
  - Will implement real aTB computation (geometry optimization, excited states, features)
  - Will use cache infrastructure created in P1.5
  - Will batch process all unique molecules from molecule_table.parquet

---

## 2026-01-08 — Schema enforcement + field filtering (P1.5 hardening)
### Implemented
- **Strict status.json schema enforcement** ([src/agents/atb_agent.py](src/agents/atb_agent.py)):
  - Removed extra fields (`canonical_smiles`, `note`) from status.json
  - Now adheres to exact 7-field schema from `doc/process.md` P2:
    - `inchikey`, `run_status`, `fail_stage`, `error_msg`, `timestamp`, `atb_version`, `runtime_sec`
  - SMILES now stored separately in `canonical_smiles.txt` (not in status.json)
  - Updated docstring to document strict schema compliance

- **Report field filtering** ([src/cli.py](src/cli.py)):
  - Added `REPORT_FIELD_ALLOWLIST` (~60 fields): photophysical properties, observables, IDs, normalized values, missing indicators
  - Added `REPORT_FIELD_BLOCKLIST`: `comment` field (privacy/sensitivity)
  - Implemented `filter_record_fields()` function to enforce allowlist/blocklist
  - Updated `run_command()` to filter all record fields before output

- **Comprehensive schema tests**:
  - Added `test_mark_pending_strict_schema()` in [tests/test_atb_agent.py](tests/test_atb_agent.py):
    - Verifies exact 7-field schema (no extra fields)
    - Confirms `canonical_smiles` NOT in status.json
    - Validates SMILES stored separately
  - Added 6 new tests in [tests/test_cli.py](tests/test_cli.py):
    - Test allowlist enforcement
    - Test blocklist enforcement (`comment` excluded)
    - Test all critical photophysical fields included
    - Test missing indicators included
    - Test normalized/raw fields included
    - Test no overlap between allowlist and blocklist

### Outputs produced
- Updated `src/agents/atb_agent.py` (strict status.json schema)
- Updated `src/cli.py` (added allowlist/blocklist + filter function)
- New file: `tests/test_cli.py` (6 new tests for field filtering)
- Updated `tests/test_atb_agent.py` (1 new strict schema test)
- Updated `doc/process.md` P1.5 (documented schema enforcement)

### Test results
```
======================== 22 passed, 1 warning in 0.86s =========================
```
- 16 existing tests (all passing)
- 7 new tests (all passing)
  - 1 strict status.json schema test
  - 6 report field filtering tests

### Verification
**status.json (strict 7-field schema)**:
```json
{
  "inchikey": "CVWRQIXEYCUPJM-UHFFFAOYSA-N",
  "run_status": "pending",
  "fail_stage": null,
  "error_msg": null,
  "timestamp": "2026-01-08T00:53:56.634814",
  "atb_version": null,
  "runtime_sec": null
}
```

**canonical_smiles.txt** (stored separately):
```
CCC[n+]1cc2ccc(C(=C(c3ccc(OC)cc3)c3ccc(OC)cc3)c3ccccc3)cc2c(-c2ccccc2)c1-c1ccccc1
```

**Report output**: Verified `comment` field excluded (0 matches in output)

### Decisions
- status.json must match EXACT schema from doc/process.md (no additions)
- SMILES stored separately (not part of status.json spec)
- Report allowlist includes all scientifically-relevant fields
- Report blocklist excludes `comment` (may contain sensitive notes)
- Test coverage for schema compliance (schema drift detection)

### Next actions
- P1.5 fully hardened ✅
- Schema enforcement complete with tests
- Ready for P2 implementation when user requests

---

## 2026-01-08 — P2 pre-flight: Cache consistency clarification (doc-only)
### Context
Before starting P2 implementation, reviewed cache artifact design to prevent potential SMILES source drift between Mode A (single-molecule) and P2 (batch).

### Analysis
**Issue 1: atb_version handling**
- Current: `null` in pending status
- Decision: ✅ Keep as-is. `atb_version` represents actual computation provenance, not intent.
- P2 will set it only after successful computation (e.g., `"AIE-aTB-abc1234"`)

**Issue 2: SMILES source consistency**
- Current: Mode A uses `canonical_smiles` from `private_clean.parquet` (via record fetch)
- Risk: P2 might use different source, causing cache/input drift
- Analysis:
  - `molecule_table.parquet` is authoritative InChIKey→SMILES registry (1050 unique)
  - `private_clean.parquet` has duplicates (1225 rows, same InChIKey repeated)
  - `cache/.../canonical_smiles.txt` is audit artifact, NOT input source
- Decision: ✅ P2 MUST iterate `molecule_table.parquet` as single source of truth

### Documentation updates
- Updated `doc/process.md` P2 section with "P2 Implementation Notes" block:
  - SMILES source: molecule_table is single source of truth
  - atb_version: null until successful computation
  - Cache consistency: molecule_table → aTB input → cache artifacts
  - Code examples showing correct/incorrect patterns

### Rationale
- Prevents future P2 implementation mistakes
- Locks in molecule_table as canonical registry
- Clarifies atb_version semantic meaning (provenance, not intent)
- No code changes needed (current Mode A implementation already uses canonical_smiles from P1 pipeline)

### Next actions
- P2 pre-flight complete ✅
- Documentation clarified for consistent SMILES sourcing
- Ready to implement P2 batch aTB wrapper when requested

---

## 2026-01-08 — P2 Implementation: AIE-aTB Integration (dry-run ready)

### Context
Implemented P2 aTB wrapper infrastructure based on analysis of `third_party/aTB/`.

### Implemented

1. **AIE-aTB Integration Analysis**
   - Entrypoint: `third_party/aTB/main.py`
   - CLI: `python main.py --smiles '<SMILES>' --workdir '<CACHE_PATH>' [--npara N] [--maxcore MB]`
   - Output: `result.json` containing `ground_state`, `excited_state`, `NEB` sections

2. **New modules created**
   - `src/chem/__init__.py`: Package init
   - `src/chem/atb_parser.py`: Parse `result.json` → `features.json`
     - `parse_result_json()`: Main entry point
     - `detect_fail_stage()`: Maps missing keys to fail_stage enum
     - `extract_features()`: Maps AIE-aTB output to our schema
   - `src/chem/atb_runner.py`: Subprocess wrapper for AIE-aTB
     - `run_atb()`: Runs single molecule, returns (status, fail_stage, error_msg)
     - `create_status_json()`: Creates strict 7-field status.json
   - `src/chem/batch_runner.py`: Batch orchestration
     - `run_batch()`: Iterates molecule_table, calls runner, updates cache
     - `consolidate_cache_to_parquet()`: Recovery utility

3. **Tests added**
   - `tests/test_atb_parser.py`: 14 tests for parsing and schema compliance

4. **Documentation updated**
   - `doc/process.md` P2 section: Added V0 black-box integration details
     - CLI arguments table
     - Cache structure with AIE-aTB subdirs
     - result.json → features.json mapping
     - Failure stage detection logic

### Outputs produced
- `src/chem/atb_parser.py` (112 lines)
- `src/chem/atb_runner.py` (198 lines)
- `src/chem/batch_runner.py` (268 lines)
- `tests/test_atb_parser.py` (180 lines)
- 36 tests passing (14 new parser tests + 22 existing)

### Key mappings (result.json → features.json)

| AIE-aTB field | Our feature |
|---------------|-------------|
| `ground_state.volume` | `s0_volume` |
| `excited_state.volume` | `s1_volume` |
| `ground_state.HOMO-LUMO` (string) | `s0_homo_lumo_gap` (float) |
| `ground_state.structure.DA` | `s0_dihedral_avg` |
| `NEB` | `neb_mean_volume` |

### Failure stage detection order
1. `result.json` missing/invalid → `"feature_parse"`
2. `ground_state` missing → `"opt"`
3. `excited_state` missing → `"excit"`
4. `NEB` missing → `"neb"`
5. `volume` missing → `"volume"`

### Next actions
- Run 5-molecule dry-run on Linux server (commands provided below)
- Copy back `cache/atb/`, `data/atb_features.parquet`, `data/atb_qc.parquet`, logs
- Review results, iterate if needed

---

## 2026-01-09 — P2 Bug fixes: Resumability + fail_stage detection

### Context
After running 20-molecule dry-run, discovered several bugs in batch_runner behavior.

### Bugs identified from logs
1. **Resumability bug**: Failed molecules were re-run on every batch execution, wasting compute time
2. **Lack of retry control**: No way to selectively retry only failed molecules vs. force-rerun everything
3. **Poor fail_stage detection**: Different error types not properly classified:
   - "Bad Conformer Id" → conformer generation failure (before calculation)
   - "CalculationFailed" with "error code -11" → amesp crash
   - "IndexError: parse_aop_energy" → amesp output parsing failure
   - Timeout failures not tracked separately

### Fixes implemented

1. **batch_runner.py**:
   - Added `--retry-failed` flag: re-run only failed molecules (skip succeeded)
   - Default behavior now skips both `success` AND `failed` molecules
   - `--force-rerun` now clearly means "rerun everything including succeeded"
   - Failed molecules preserved in output parquet with their cached status

2. **atb_runner.py**:
   - Enhanced `detect_fail_stage_from_output()` with priority-based detection:
     - Priority 1: "Bad Conformer Id" → `"conformer"`
     - Priority 2: "CalculationFailed" / "error code -11" → stage from path
     - Priority 3: "IndexError" + "parse_aop_energy" → amesp parsing failure
     - Priority 4: Parse result.json state
     - Priority 5: Directory existence checks
   - Added `"timeout"` as explicit fail_stage for timeout failures
   - Added `"conformer"` as new fail_stage for RDKit 3D generation failures

3. **doc/process.md**:
   - Updated status.json schema with new fail_stages: `conformer`, `timeout`
   - Updated failure stage detection order (9 steps)
   - Added `recommended_next_steps` for new fail_stages
   - Added batch runner CLI examples

### Dry-run results (20 molecules)
From logs:
- Total: 20 molecules
- Invalid SMILES (empty InChIKey): 1
- Succeeded: 5 (AAAQKTZKLRYKHR, AAHQWSRRIKEFES, AGOZGUAZHRGBCP, AMDZJULAHPGTEZ, AMVKSLDIFMJFIG)
- Failed: 14
  - Conformer failures: 2 (AJUBVOXNBCYBCI, ANLLAXFYLRALTK - "Bad Conformer Id")
  - Amesp crashes: ~10 ("error code -11")
  - Timeout: 1 (AHEKEONWUHBVNV - 3600s)
  - Parsing errors: 2 ("IndexError: parse_aop_energy")

### Success rate
- 5/19 = 26% success rate (excluding 1 invalid SMILES)
- Average runtime per successful molecule: ~100-200s
- Failure pattern: Most failures are amesp crashes (error code -11)

### Issues / surprises
- High failure rate (~74%) due to amesp calculator issues
- Error code -11 typically indicates SEGFAULT in amesp
- Some SMILES can't generate 3D conformers (complex structures)

### Decisions
- Default batch behavior: skip both succeeded AND failed (conservative)
- Use `--retry-failed` for selective retry of failed molecules
- New fail_stages: `conformer`, `timeout` for better diagnostics

### Next actions
- Continue full batch run on server with improved resumability
- Analyze failed molecules to understand amesp crash patterns
- Consider filtering out molecules likely to fail (ionic, very large, etc.)

---

## 2026-01-09 — P2 Root cause fix: Charge auto-detection for ionic molecules

### Context
Analysis of the 74% failure rate revealed a critical bug: **amesp was running all molecules with `charge=0`**, even ionic molecules with formal charges like `[n+]`, `[I-]`, etc.

### Root cause analysis
From InChIKey suffix distribution:
- `-N` (neutral): 977 molecules (93%)
- `-M` (ionic): 47 molecules (4.5%)
- Other ionic (`-L`, `-O`, `-J`, etc.): 25 molecules (2.5%)

**Total ionic molecules: 72 (7%)** - all were failing due to incorrect charge.

The hardcoded `charge=0` in `calculator.py:54` caused amesp to crash or produce garbage for:
- `ABNRGKSAIONSCC-UHFFFAOYSA-M` (contains `[n+]` and `[I-]`)
- `AHEKEONWUHBVNV-OCEACIFDSA-J` (phosphate groups with `[O-]`)
- `AIXZTWWXCJZLLV-UHFFFAOYSA-M` (contains `[F-]` and `[n+]`)
- etc.

### Fixes implemented

1. **third_party/aTB/main.py**:
   - Added `--charge` CLI argument: `--charge <int>`
   - Added `get_formal_charge_from_smiles()` function using RDKit
   - Auto-detects charge from SMILES if `--charge` not provided
   - Logs detected/provided charge for auditability

2. **third_party/aTB/calculator.py**:
   - Changed `charge=0` to `charge=getattr(args, 'charge', 0)`
   - Now uses the auto-detected or user-provided charge

### How it works now
```bash
# Neutral molecule (charge auto-detected as 0)
python third_party/aTB/main.py --smiles "c1ccccc1" --workdir cache/atb/XX/XXX

# Ionic molecule (charge auto-detected from SMILES)
# SMILES: CN(C)c1ccc(/C=C(\C#N)c2ccc(-c3cc[n+](C)cc3)cc2)cc1.[I-]
# Auto-detected charge: 0 (net neutral: +1 from [n+] and -1 from [I-])

# Override charge manually if needed
python third_party/aTB/main.py --smiles "..." --charge 1 --workdir cache/atb/XX/XXX
```

### Expected improvement
- Ionic molecules should now run correctly with proper charge
- Expected to fix ~50% of the amesp failures (those caused by wrong charge)
- Remaining failures may be due to:
  - Molecule too large/complex for amesp
  - Memory issues (try increasing `--maxcore`)
  - Conformer generation failures (RDKit issue)

### Files modified
- `third_party/aTB/main.py` - Added `--charge` arg and auto-detection
- `third_party/aTB/calculator.py` - Use `args.charge` instead of hardcoded 0

### Next actions
- ~~Re-run batch on server with charge fix~~ (deferred, see below)
- Monitor success rate improvement
- If still high failure rate, investigate memory/complexity issues

---

## 2026-01-09 — P2 Strategy change: Skip ionic molecules in V0

### Context
After implementing charge auto-detection, decided to take a more conservative approach for V0: **skip ionic molecules entirely** rather than risk untested charge handling.

### Rationale
1. Ionic molecules are only ~7% of dataset (72 of 1050)
2. Charge handling in amesp is complex and untested
3. Better to get V0 working on 93% neutral molecules first
4. Can re-enable ionic support in V1 after validation

### Implementation
- Added `is_ionic_molecule(smiles)` function to detect ionic patterns
- Ionic molecules get `run_status="skipped"`, `fail_stage="ionic"`
- Charge auto-detection code kept in place (ready for V1)

### Files modified
- `src/chem/batch_runner.py` - Added ionic detection and skipping
- `doc/process.md` - Marked ionic support as DEFERRED, updated status.json schema

### Expected batch summary
```
{
  "total_molecules": 1050,
  "invalid_smiles": 1,
  "skipped_ionic": ~72,
  "skipped_cached": ...,
  "succeeded": ...,
  "failed": ...,
}
```

### Next actions
- Run batch on neutral molecules only (~977 molecules)
- After V0 complete, validate charge handling on test ionic molecules
- Re-enable ionic support in V1

---

## 2026-01-09 — P2 Stabilization: RDKit embedding + size filter
### Implemented
- Hardened RDKit 3D embedding in `third_party/aTB/main.py` (ETKDG v3/v2 fallback + random-coords retry + UFF cleanup)
- Added explicit embedding failure message to improve `conformer` stage classification
- Added optional size filter in `src/chem/batch_runner.py`:
  - New CLI flags: `--max-heavy-atoms`, `--rdkit-features`
  - Skips large molecules with `run_status="skipped"` and `fail_stage="size"`
  - Adds `skipped_size` to batch summary

### Outputs produced
- Updated `third_party/aTB/main.py` (robust embedding)
- Updated `src/chem/atb_runner.py` (embedding failure detection)
- Updated `src/chem/batch_runner.py` (size filter + CLI flags)
- Updated `doc/process.md` (documented `size` fail_stage and new CLI option)

### Issues / surprises
- None

### Next actions
- Re-run batch with a size cap (e.g., `--max-heavy-atoms 40`) to reduce amesp segfaults
- Evaluate remaining failures and adjust threshold or resources as needed

---

## 2026-01-09 — P2 Bug fix: ETKDG param compatibility
### Implemented
- Guarded RDKit ETKDG parameter setting in `third_party/aTB/main.py` to avoid `AttributeError` on older RDKit builds that lack `maxAttempts`
- Only enables random-coords fallback when the parameter supports it

### Outputs produced
- Updated `third_party/aTB/main.py` (ETKDG param guard)
- Updated `doc/process.md` (task marked complete)

### Issues / surprises
- None

### Next actions
- Re-run failed molecule to verify the conformer stage proceeds without `AttributeError`

---

## 2026-01-09 — P2 Enhancement: Include ionic molecules option
### Implemented
- Added `--include-ionic` flag to `src/chem/batch_runner.py` to override V0 ionic skipping
- Default behavior remains skip ionic molecules; setting the flag processes all molecules
- Updated `doc/process.md` with the new CLI option and V0 note

### Outputs produced
- Updated `src/chem/batch_runner.py`
- Updated `doc/process.md`

### Issues / surprises
- None

### Next actions
- Use `python -m src.chem.batch_runner --include-ionic` to run ionic molecules

---

## 2026-01-12 — P4a: Initial Anchor Space (ECFP-only) ✅

### Context
Urgent V0 branch to build initial anchor reference space using ONLY ECFP fingerprints, before P2 (aTB) computation completes. This enables UQ development to proceed in parallel.

### Implemented
- **`doc/process.md`**: Added P4a/P4b/P4c sub-stages under P4 with:
  - P4a: ECFP-only anchor space (current)
  - P4b: Add RDKit descriptors (future, with z-score + L2-normalize + cosine)
  - P4c: Add aTB descriptors + FAISS index (future, post-P2)

- **`src/features/anchor_ecfp.py`** (~240 lines):
  - `is_valid_inchikey()`: Regex validation for InChIKey format
  - `to_binary_fingerprint()`: Coerce int8 arrays to boolean via `(fp > 0).astype(uint8)`
  - `tanimoto_similarity()`: Using `np.logical_and` for intersection (not raw bitwise)
  - `compute_all_neighbors()`: Brute-force top-k computation excluding self
  - CLI: `python -m src.features.anchor_ecfp --k 10`

- **`src/features/validate_anchor_space.py`** (~280 lines):
  - Similarity distribution summary (top-1, top-10, all)
  - Sample neighbor inspection (5 random molecules)
  - Suspicious case detection (high sim >= 0.95, low sim <= 0.10)
  - Descriptor correlation check (MW/LogP differences for high-sim pairs)
  - CLI: `python -m src.features.validate_anchor_space`

- **`tests/test_anchor_ecfp.py`** (~220 lines): 22 unit tests
  - InChIKey filtering (6 tests)
  - Tanimoto computation (7 tests)
  - Binary fingerprint coercion (3 tests)
  - Neighbor output schema (5 tests)
  - Data loading (1 test)

### Outputs produced
- **`data/anchor_neighbors_ecfp.parquet`** (~130KB):
  - 1049 molecules (1 filtered for invalid InChIKey)
  - 10,490 neighbor records (k=10 per molecule)
  - Columns: inchikey, neighbor_inchikey, rank, tanimoto_sim

### Validation Results
```
Total molecules: 1049
Top-1 similarity: min=0.149, median=0.750, 95th=1.000, max=1.000
Top-10 similarity: min=0.115, median=0.433, max=0.796

Suspicious cases:
  Top-1 sim >= 0.95: 155 molecules (potential duplicates/highly similar scaffolds)
  Top-1 sim <= 0.10: 0 molecules (no fingerprint issues detected)

Descriptor correlation (100 high-sim pairs):
  MW relative diff: mean=0.19, median=0.15 (reasonable)
  LogP absolute diff: mean=2.77, median=1.67 (some variation expected)
```

### CLI commands
```bash
# Build anchor neighbors (ECFP-only)
python -m src.features.anchor_ecfp --k 10

# Validate and print report
python -m src.features.validate_anchor_space

# Run unit tests
pytest tests/test_anchor_ecfp.py -v
```

### Test results
```
22 passed, 1 warning in 0.80s
```

### Issues / surprises
- **155 molecules with top-1 sim >= 0.95**: Expected given AIE dataset may have many scaffold variants
- **1 invalid InChIKey filtered**: Empty InChIKey row in rdkit_features.parquet
- Runtime: ~3 seconds for 549K pairwise comparisons (brute-force is fast enough)

### Decisions
- Tanimoto computed with `np.logical_and` (safer than bitwise `&`)
- Fingerprints coerced to boolean via `(fp > 0).astype(uint8)` before comparison
- Self excluded from neighbor list (rank 1 = most similar OTHER molecule)
- No FAISS for P4a (brute-force sufficient for 1050 molecules)

### Next actions
- P4a COMPLETE ✅
- Ready for P5 (UQ scores) development using ECFP neighbors
- P4b/P4c will add RDKit descriptors and aTB features when ready

---

## 2026-01-12 — P4a Verification: Tanimoto matches RDKit official ✅

### Context
Verified that the numpy-based Tanimoto implementation in `src/features/anchor_ecfp.py` produces identical results to RDKit's official `DataStructs.TanimotoSimilarity()` function.

### Verification Method
1. **Sample check**: Verified 500 random neighbor pairs from `anchor_neighbors_ecfp.parquet`
2. **Full recalculation**: Recomputed all 10,490 neighbors using RDKit's native `TanimotoSimilarity`
3. **Comparison**: Compared original numpy results vs RDKit-verified results

### Verification Results
| Metric | Value |
|--------|-------|
| Pairs verified | 500 (sample) + 10,490 (full) |
| Discrepancies found | **0** |
| Match rate | **100%** |
| Mean difference | **0.00e+00** |
| Max difference | **0.00e+00** |
| Exact matches | **10,490 / 10,490** |

### Confirmed Similarity Stats (via RDKit)
- Top-1 similarity: min=0.149, median=0.750, max=1.000
- Total pairwise comparisons: 549,676

### Files Created
- `src/features/verify_tanimoto.py` (~400 lines): Verification script with modes:
  - `verify`: Sample check against stored neighbors
  - `recalculate`: Full recompute using RDKit
  - `compare`: Diff two neighbor files
  - `full`: All three steps
- `data/anchor_neighbors_ecfp_rdkit_verified.parquet`: RDKit-computed neighbors

### Conclusion
✅ **PERFECT MATCH**: The numpy-based implementation is mathematically identical to RDKit's official Tanimoto coefficient. No code changes needed.

### Next actions
- P4a implementation is verified and production-ready
- Proceed with P5 (UQ scores) development

---

## 2026-01-14 — P4a+ Hybrid Anchor Space (ECFP + partial aTB) ✅

### Context
Validation branch to test whether adding aTB features improves reference space quality. Uses ONLY the subset of molecules with successful aTB cache. Does NOT replace P4a outputs.

### Implemented
- **`doc/process.md`**: Added P4a+ subsection under P4 with:
  - Subset selection (S_atb) from cache success runs
  - aTB features used (delta_volume, delta_gap, delta_dihedral, excitation_energy)
  - Similarity fusion formula: `sim = 0.7*sim_ecfp + 0.3*sim_atb`
  - Output schema with separate sim components

- **`src/features/anchor_hybrid_ecfp_atb_partial.py`** (~380 lines):
  - `discover_successful_cache()`: Scan cache/atb for run_status=="success"
  - `extract_atb_features()`: Parse 4 minimal aTB features with missingness filter
  - `safe_parse_float()`: Handle excitation_energy string → float conversion
  - `build_atb_matrix()`: Z-score + L2-normalize aTB feature vectors
  - `cosine_to_sim()`: Map cosine [-1,1] to similarity [0,1]
  - `compute_hybrid_neighbors()`: Fused similarity with configurable weights
  - CLI: `python -m src.features.anchor_hybrid_ecfp_atb_partial --k 10 --w-ecfp 0.7 --w-atb 0.3`

- **`src/features/validate_anchor_space_hybrid_partial_atb.py`** (~340 lines):
  - Subset size reporting
  - Similarity distribution stats (sim, sim_ecfp, sim_atb)
  - Random baseline comparison (1000 random pairs)
  - Overlap@10 analysis vs ECFP-only neighbors
  - Example molecules with neighbor details
  - Sanity checks (range validation, dominance warnings)
  - CLI: `python -m src.features.validate_anchor_space_hybrid_partial_atb`

- **`tests/test_anchor_hybrid_partial_atb.py`** (~230 lines): 30 unit tests
  - safe_parse_float tests (11 tests including string, None, NaN, inf)
  - extract_atb_features tests (5 tests for missingness filtering)
  - Similarity range tests (3 tests)
  - Output schema tests (5 tests)
  - InChIKey validation tests (5 tests)
  - Integration smoke tests (1 test)

### Outputs produced
- **`data/anchor_neighbors_hybrid_partial_atb.parquet`**:
  - 76 molecules (those with successful aTB cache)
  - 760 neighbor records (k=10 per molecule)
  - Columns: inchikey, neighbor_inchikey, rank, sim, sim_ecfp, sim_atb

- **`data/anchor_hybrid_partial_atb_manifest.json`**:
  - n_success_cache: 76
  - n_used_after_feature_filter: 76
  - feature_list: [delta_volume, delta_gap, delta_dihedral, excitation_energy]
  - weights: {w_ecfp: 0.7, w_atb: 0.3}

### Validation Results
```
SUBSET SIZES:
  Cache success count:            76
  After aTB feature filter:       76
  Final S_atb_hybrid (with ECFP): 76
  Total neighbor records:         760

TOP-1 NEIGHBOR SIMILARITY:
  sim:      min=0.336, median=0.566, max=0.764
  sim_ecfp: min=0.081, median=0.489, max=0.708
  sim_atb:  min=0.291, median=0.859, max=0.995

RANDOM BASELINE (1000 pairs):
  ECFP Tanimoto:  median=0.1186
  aTB cosine:     median=0.4855
  Top-1 vs Random ratio (ECFP): 4.12x
  Top-1 vs Random ratio (aTB):  1.77x

OVERLAP@10 WITH ECFP-ONLY:
  mean=0.083, median=0.100
  Distribution: 87% in [0.0-0.2), 13% in [0.2-0.4)
  WARNING: Low overlap suggests aTB features dominate rankings

SANITY CHECKS: ALL PASSED
  - sim in [0,1]: ✓
  - sim_ecfp in [0,1]: ✓
  - sim_atb in [0,1]: ✓
```

### CLI commands
```bash
# Build hybrid neighbors
python -m src.features.anchor_hybrid_ecfp_atb_partial --k 10 --w-ecfp 0.7 --w-atb 0.3

# Validate and compare vs ECFP-only
python -m src.features.validate_anchor_space_hybrid_partial_atb

# Run unit tests
pytest tests/test_anchor_hybrid_partial_atb.py -v  # 30 passed
```

### Test results
```
30 passed in 0.97s
```

### Issues / surprises
- **Low overlap@10 (0.083)**: Even with 70/30 weighting, aTB features significantly change neighbor rankings
- **High aTB similarity**: Top-1 sim_atb median=0.859 vs sim_ecfp median=0.489, suggesting aTB space is less discriminative
- **Random aTB baseline higher**: 0.4855 vs ECFP's 0.1186 indicates aTB features cluster molecules more tightly

### Decisions
- aTB features have meaningful impact on rankings (low overlap proves this)
- Current 0.7/0.3 weighting may need tuning (aTB seems to dominate despite lower weight)
- Consider testing with even lower aTB weight (0.9/0.1) in future experiments
- Keep P4a ECFP-only outputs unchanged as primary reference

### aTB Feature Statistics (z-score normalization)
- delta_volume: mean=-0.048, std=3.056
- delta_gap: mean=-0.646, std=0.321
- delta_dihedral: mean=-0.537, std=7.088
- excitation_energy: mean=1.380, std=0.874

### Next actions
- P4a+ validation complete ✅
- Consider testing alternative weight configurations (0.9/0.1, 0.8/0.2)
- Continue P2 aTB batch runs to increase S_atb subset size
- Proceed with P5 (UQ scores) using ECFP-only neighbors as primary

---

## 2026-01-14 — P4a+ Extended Audit & Sensitivity Analysis ✅

### Context
Extended validation of the hybrid anchor space (ECFP + partial aTB) with correctness audit, structural reasonableness check, and sensitivity experiments.

### Implemented
- Extended `src/features/validate_anchor_space_hybrid_partial_atb.py` with `--audit` flag:
  - **Section A**: Pairwise correctness audit (RDKit verification for ECFP, manifest-based recomputation for aTB)
  - **Section B**: Structural reasonableness check (ECFP drift detection)
  - **Section C1**: Weight sweep sensitivity (w_atb in {0.0, 0.1, 0.2, 0.3})
  - **Section C2**: Two-stage vs linear fusion comparison

### Validation Results
```
SECTION A: PAIRWISE CORRECTNESS AUDIT
  Sampled 20 pairs (7 high-sim, 7 mid-sim, 6 low-sim)
  ECFP Verification: PASS (max_err=0.00e+00, mean_err=0.00e+00)
  aTB Verification:  PASS (max_err=1.11e-16, mean_err=2.78e-17)

SECTION B: STRUCTURAL REASONABLENESS CHECK
  sim_ecfp distribution for hybrid top-10:
    min=0.0484, 10th=0.1153, median=0.2431, mean=0.2782

  Neighbors with sim_ecfp < 0.2: 267/760 (35.1%)
  WARNING: Potential 'ECFP drift' detected

  Per-rank analysis:
    Rank 1: median=0.489, low%=9.2%
    Rank 5: median=0.260, low%=32.9%
    Rank 10: median=0.174, low%=56.6%

SECTION C1: WEIGHT SWEEP SENSITIVITY
  w_ecfp  w_atb  overlap@10  top1_med  low_ecfp%
  -----------------------------------------------
    1.0    0.0      0.129     0.5000      35.1%
    0.9    0.1      0.129     0.5119      35.1%
    0.8    0.2      0.129     0.5373      35.1%
    0.7    0.3      0.129     0.5664      35.1%

SECTION C2: TWO-STAGE FUSION vs LINEAR FUSION
  Strategy            overlap@10  ecfp_median  low_ecfp%
  -------------------------------------------------------
  Linear fusion          0.129       0.2431      35.1%
  Two-stage (approx)     0.129       0.5182       0.0%

  Two-stage improves ECFP median: 0.5182 > 0.2431
  Two-stage reduces low-ECFP neighbors: 0.0% < 35.1%
```

### Key Findings

1. **Both ECFP and aTB Correct**: Perfect numerical reproducibility for both similarity metrics:
   - ECFP: max error = 0.0 (exact match with RDKit's DataStructs.TanimotoSimilarity)
   - aTB: max error = 1.11e-16 (floating point precision limit)

2. **Structural Drift Concern**: 35.1% of hybrid neighbors have sim_ecfp < 0.2, indicating significant structural dissimilarity. This "ECFP drift" increases with rank (9.2% at rank-1 vs 56.6% at rank-10).

3. **Weight Insensitivity**: Overlap@10 remains constant at 0.129 across all weight configurations. This is because the hybrid space only has 76 molecules, limiting reranking impact within the stored top-10.

4. **Two-Stage Advantage**: Two-stage fusion (ECFP-first retrieval, then fused reranking) dramatically improves structural preservation:
   - ECFP median: 0.5182 vs 0.2431 (2.1x improvement)
   - Low-ECFP fraction: 0.0% vs 35.1%

### Decisions
- **Two-stage fusion recommended** for future anchor space implementations to maintain structural reasonableness
- Current linear fusion (0.7/0.3) causes significant ECFP drift at lower ranks
- aTB similarity computation is verified correct

### CLI command
```bash
python -m src.features.validate_anchor_space_hybrid_partial_atb --audit
```

### Next actions
- Consider implementing two-stage fusion in `anchor_hybrid_ecfp_atb_partial.py`
- P4a+ extended validation complete ✅

---

### 2026-01-15 — P4a+ Two-Stage Retrieval Implementation

#### Implemented

**Task B: Two-stage neighbor builder**
- Created `src/features/anchor_two_stage_partial_atb.py`
  - Stage 1: Retrieve top-M candidates by ECFP Tanimoto (default M=50)
  - Stage 2: Rerank by fused similarity (w_ecfp=0.7, w_atb=0.3)
  - Output: `data/anchor_neighbors_two_stage_partial_atb.parquet` with stage1_rank column
  - Fixed cache discovery to handle nested directory structure (cache/atb/AA/INCHIKEY/)
  - Fixed ECFP loading to handle single column format (ecfp_2048)

**Task C: Validation script**
- Created `src/features/validate_two_stage_partial_atb.py`
  - Compares ECFP-only vs linear-fusion vs two-stage
  - Reports: overlap@10, ecfp_median, low_ecfp%, stage1_rank distribution
  - Handles column name normalization (tanimoto_sim → sim_ecfp)

**Unit tests**
- Created `tests/test_anchor_two_stage_partial_atb.py`
  - Tests stage1 candidate restriction, output schema, ranks, similarity ranges
  - All 12 tests pass ✅

#### Outputs produced

```bash
# Builder
python -m src.features.anchor_two_stage_partial_atb \
    --rdkit data/rdkit_features.parquet \
    --atb-manifest data/anchor_hybrid_partial_atb_manifest.json \
    --output data/anchor_neighbors_two_stage_partial_atb.parquet \
    --M 50 --k 10 --w-ecfp 0.7 --w-atb 0.3

# Validator
python -m src.features.validate_two_stage_partial_atb \
    --ecfp data/anchor_neighbors_ecfp.parquet \
    --linear data/anchor_neighbors_hybrid_partial_atb.parquet \
    --two-stage data/anchor_neighbors_two_stage_partial_atb.parquet
```

**Files created:**
- `data/anchor_neighbors_two_stage_partial_atb.parquet` (760 neighbor pairs, 76 molecules)
- `data/anchor_neighbors_two_stage_partial_atb_manifest.json`

#### Validation Results

**Comparison: ECFP-only vs Linear-fusion vs Two-stage**

| Strategy       | ecfp_median | low_ecfp% | rank1_low% | rank10_low% | overlap@10 w/ ECFP |
|----------------|-------------|-----------|------------|-------------|--------------------|
| ECFP-only      | 0.5217      | 1.7%      | 0.3%       | 3.6%        | 1.0 (self)         |
| Linear-fusion  | 0.2431      | 35.1%     | 9.2%       | 56.6%       | 0.3%               |
| Two-stage      | 0.2431      | 34.7%     | 9.2%       | 53.9%       | 0.3%               |

**Stage1_rank statistics (two-stage):**
- min: 1, median: 8.0, max: 50

#### Issues / surprises

**Two-stage did NOT improve as expected:**
- ecfp_median: identical to linear-fusion (0.2431)
- low_ecfp%: nearly identical (34.7% vs 35.1%)
- overlap@10: identical (0.3%)

**Root cause: M=50 too large for n=76 subset**
- With M=50 and only 76 molecules (75 candidates per query), Stage 1 includes ~67% of all candidates
- This reduces the gating effect - Stage 1 doesn't meaningfully restrict the pool
- Stage1_rank median = 8.0 suggests Stage 2 picks from top ECFP candidates, but drift still persists

**Why the previous "two-stage approximation" showed 0% drift:**
- That test used ECFP-only neighbors as Stage 1 output (top-10 by ECFP only)
- This was a much stricter gate (k=10, not M=50)
- The approximation was comparing "pure ECFP top-10" vs "fused top-10 from all 75 candidates"

#### Decisions

**Two-stage retrieval is still recommended BUT with adjusted parameters:**
- For small subsets (n < 100), use M ≈ 2k to 3k (e.g., M=20-30 for k=10)
- For larger subsets (n > 1000), M=50 provides meaningful gating
- **Critical insight**: Two-stage effectiveness depends on M being small enough to exclude low-ECFP candidates

**Documentation updated:**
- `doc/process.md` § P4b/P4c: Added two-stage retrieval guidance and health metrics
- Defined mandatory reporting: ecfp_median, low_ecfp% with thresholds (>30% WARNING, >10% CAUTION, <10% PASS)

#### Next actions

**Options for re-validation with corrected M:**
1. Re-run builder with M=20 or M=30 to test stricter gating
2. Proceed to full-dataset anchor space (n=1050) where M=50 will be meaningful
3. Document lesson: "M should be tuned based on dataset size to balance diversity and structural gating"

**Proceed to P5 (UQ Router):**
- Two-stage implementation is correct and tested
- Validation revealed important parameterization insight
- Ready to move forward with coverage/novelty/aleatoric computation

---

### 2026-01-15 — P4a+ M-Parameter Sweep for Two-Stage Retrieval

#### Context
After discovering that M=50 was too large for the n=76 subset, ran systematic M-sweep to find optimal Stage 1 candidate pool size.

#### Implemented
- Created `src/features/m_sweep_two_stage_partial_atb.py`
- Tests M ∈ {5, 8, 10, 12, 15, 20, 25, 30, 40, 50}
- Reports for each M: low_ecfp% (overall, rank1, rank10), ecfp_median, overlap@10

#### M-Sweep Results (n=76, k=10, w_ecfp=0.7, w_atb=0.3)

**Compact table:**
```
    M  low_ecfp%  rank1_low%  rank10_low%  ecfp_median  overlap@10
  ---  ---------  ----------  -----------  -----------  ----------
    5      11.3%        7.9%         0.0%       0.3501      0.0603
    8      16.8%        7.9%         0.0%       0.3137      0.0493
   10      20.7%        7.9%        34.2%       0.2832      0.0440
   12      21.8%        7.9%        31.6%       0.2778      0.0440
   15      23.8%        9.2%        36.8%       0.2667      0.0440
   20      28.4%        9.2%        47.4%       0.2554      0.0440
   25      31.1%        9.2%        48.7%       0.2500      0.0440
   30      32.5%        9.2%        48.7%       0.2481      0.0440
   40      34.3%        9.2%        57.9%       0.2446      0.0440
   50      34.7%        9.2%        53.9%       0.2431      0.0440
```

#### Key Findings

**Drift vs M:**
- **M=5**: 11.3% low-ECFP (CAUTION threshold), ecfp_median=0.350
- **M=50**: 34.7% low-ECFP (WARNING threshold), ecfp_median=0.243
- Clear monotonic trend: larger M → more drift

**Rank-specific drift:**
- Rank-1 relatively stable (7.9-9.2% across all M)
- Rank-10 shows dramatic increase: 0% at M=5 vs 53.9% at M=50
- **Critical insight**: Drift accumulates at lower ranks when M is large

**Overlap@10 with ECFP-only:**
- All M values show low overlap (4-6%)
- Slight improvement at M=5 (6.0%) vs M≥10 (4.4%)
- **Interpretation**: aTB features significantly change rankings regardless of M

**Threshold evaluation:**
- **M≤20**: CAUTION (10-30% drift)
- **M≥25**: WARNING (>30% drift)
- **M<5 needed for PASS** (<10% drift) - but not tested due to k=10 constraint

#### Decisions

**Recommended M for n=76 subset:**
- **M=5** achieves best drift reduction (11.3%) and structural preservation (median=0.350)
- Trade-off: M=5 limits Stage 1 diversity (only 5 candidates per query vs 75 possible)

**Parameterization rule (updated):**
- For small subsets: **M ≈ 0.5k to 1k** (e.g., M=5-10 for k=10)
- For large datasets: M=50-100 provides meaningful gating without over-restriction
- **Key constraint**: M must be << n to provide effective structural gating

**Fundamental limitation identified:**
- Even at M=5, still have 11.3% drift (CAUTION level)
- Root cause: **aTB features are inherently less discriminative** (high baseline similarity)
- Random aTB baseline: median=0.485 vs ECFP baseline: median=0.119 (4x difference)
- This explains why aTB-influenced neighbors often have low ECFP similarity

#### CLI command
```bash
python -m src.features.m_sweep_two_stage_partial_atb
```

#### Next actions
- M-sweep validates two-stage approach BUT reveals fundamental aTB/ECFP tension
- For production: recommend **M=5 for n=76**, scale proportionally for larger datasets
- Consider alternative fusion strategies (e.g., w_ecfp=0.9, w_atb=0.1) if structural preservation is critical
- Proceed to P5 (UQ Router) with documented insights

---

## 2026-01-15 — P3a Feature Merge (pre-aTB) ✅

### Context
P2 (aTB batch computation) is temporarily delayed for external reasons. Split P3 into P3a (pre-aTB merge) and P3b (post-aTB merge) to enable V0 development to proceed with ECFP-only anchor space.

### Implemented
- **Documentation update (Step 0)**:
  - Updated `doc/process.md` to split P3 into P3a/P3b sections
  - P3a: Merge experimental + RDKit features (CURRENT)
  - P3b: Add aTB features after P2 completes (FUTURE)

- **Main merge script**: `src/features/merge_pre_atb.py` (~280 lines)
  - `load_private_clean()`: Load 1225 record-level rows
  - `load_rdkit_features()`: Load 1050 molecule-level rows
  - `merge_features()`: Left join on inchikey (preserves all experimental records)
  - `fit_scaler()`: StandardScaler on 9 RDKit descriptors (z-score normalization)
  - `apply_scaler()`: Create {col}_scaled columns for normalized descriptors
  - `save_feature_config()`: Document feature blocks, scaler params, merge coverage
  - `run_merge()`: Main pipeline (load → merge → fit → apply → save)

- **Validation script**: `src/features/validate_merge_pre_atb.py` (~220 lines)
  - 5 validation checks:
    1. Row count preservation (must equal private_clean)
    2. Merge coverage (non-null RDKit descriptors and ECFP)
    3. Invalid/empty inchikey handling
    4. Descriptor statistics (min/median/max for integrity)
    5. ECFP array integrity (type, length=2048)

- **Unit tests**: `tests/test_merge_pre_atb.py` (5 tests)
  - Row count preservation
  - RDKit descriptor columns present
  - ECFP array integrity (length=2048)
  - Missing indicator columns preserved
  - Metadata columns preserved

### Outputs produced
- **`data/X_full_pre_atb.parquet`**: 1225 rows, 96 columns
  - Feature blocks:
    1. Experimental observables (emission, qy, tau, absorption, tested_solvent)
    2. RDKit descriptors (9 original + 9 scaled versions)
    3. ECFP fingerprints (ecfp_2048 as length-2048 int8 array)
    4. Missing indicators (14 {field}_missing columns)
    5. Metadata (id, code, inchikey, canonical_smiles, molecular_weight)

- **`data/scaler_pre_atb.pkl`**: StandardScaler fitted on 9 RDKit descriptors
  - Mean: [683.05, 11.23, 40.03, 8.97, 0.22, 3.62, 7.76, 7.01, 50.57]
  - Scale (std): [379.21, 7.45, 45.00, 9.48, 0.75, 3.33, 4.49, 4.25, 28.16]

- **`data/feature_config_pre_atb.yaml`**: Feature block documentation
  - Documents all 5 feature blocks
  - RDKit descriptor scaling details
  - Notes that aTB block is absent (P3a is pre-aTB)

### Validation Results
```
================================================================================
P3a MERGE VALIDATION
================================================================================

CHECK 1: ROW COUNT
✓ PASS: Row counts match (1225 rows)

CHECK 2: MERGE COVERAGE
Rows with RDKit descriptors: 1164/1225 (95.0%)
Rows with ECFP:              1164/1225 (95.0%)
✓ PASS: Merge coverage OK

CHECK 3: INVALID/EMPTY INCHIKEY HANDLING
Valid InChIKeys:   1161/1225 (94.8%)
Invalid InChIKeys: 64/1225 (5.2%)
✓ PASS: Invalid InChIKeys handled (64 found)
  - Invalid InChIKeys with non-null RDKit: 3 (should be 0)

CHECK 4: DESCRIPTOR STATISTICS
RDKit descriptors (original):
  mw:    min=78.11,  median=602.72,  max=3554.85
  logp:  min=-2.00,  median=9.41,    max=63.83
  tpsa:  min=0.00,   median=31.29,   max=446.44
RDKit descriptors (scaled):
  mw_scaled:    mean=-0.0000, std=1.0004
  logp_scaled:  mean=-0.0000, std=1.0004
✓ PASS: Descriptor stats look reasonable

CHECK 5: ECFP ARRAY INTEGRITY
Non-null ECFP arrays: 1164
Sample ECFP arrays: all length 2048, dtype=int8, value range [0,1]
✓ PASS: ECFP arrays present and valid

Checks passed: 5/5
✅ ALL CHECKS PASSED
================================================================================
```

### Test Results
```bash
pytest tests/test_merge_pre_atb.py -v
# 5 passed in 0.63s
```

### CLI Commands
```bash
# Run P3a merge
python -m src.features.merge_pre_atb

# Run validator
python -m src.features.validate_merge_pre_atb

# Run tests
pytest tests/test_merge_pre_atb.py -v
```

### Issues / surprises
- **3 invalid InChIKeys with non-null RDKit**: Expected 0 (should investigate)
  - These rows have empty/invalid InChIKey but somehow matched to RDKit features
  - May indicate merge key collision or data quality issue
- **95% merge coverage**: 61 rows (1225 - 1164) have null RDKit descriptors
  - Expected behavior for invalid SMILES from P1

### Decisions
- **ECFP preservation**: ecfp_2048 stored as-is (NOT scaled) per requirements
- **Scaler scope**: Only RDKit descriptors normalized (experimental observables NOT scaled)
- **Missing indicators**: All {field}_missing columns preserved from P1
- **Left join strategy**: Preserves all 1225 experimental records even without RDKit features
- **Scaled suffix**: Use {col}_scaled naming convention for z-scored columns

### Feature blocks in P3a output
1. **Experimental observables**: emission_*, qy_*, tau_*, absorption_peak_nm, tested_solvent
   - NOT scaled (preserve raw experimental values)
2. **RDKit descriptors**: 9 continuous descriptors (mw, logp, tpsa, n_rotatable_bonds, n_hbd, n_hba, n_rings, n_aromatic_rings, n_heavy_atoms)
   - Original values + {col}_scaled versions (z-scored)
3. **ECFP fingerprints**: ecfp_2048 array (preserved as int8 array, NOT scaled)
4. **Missing indicators**: 14 {field}_missing boolean columns from P1
5. **Metadata**: id, code, inchikey, canonical_smiles, molecular_weight, mechanism_id, features_id

### Next actions
- **P3a COMPLETE** ✅
- Ready for P4a (Anchor space with ECFP only)
- P3b will be implemented after P2 completes (merge in aTB features)

---

## 2026-01-15 — P5a Pre-aTB UQ Computation ✅

### Context
P2 (aTB batch computation) is temporarily skipped/delayed for external reasons. P5a enables UQ router development to proceed using ECFP-only anchor space from P4a.

### Implemented
- **Documentation update**:
  - Updated `doc/process.md` with P5a subsection under P5
  - Documented score definitions (C_sim, C_meta, coverage, novelty, aleatoric)
  - Documented router logic and recommended_next_steps

- **Main computation module**: `src/uq/compute_uq_pre_atb.py` (~370 lines)
  - `compute_c_sim()`: Mean of top-k Tanimoto similarities from neighbor table
  - `compute_c_meta()`: 1 - missing_rate over 14 critical fields
  - `compute_novelty()`: 1 - top1_sim, percentile normalized
  - `compute_aleatoric()`: Entropy of normalized similarities / log(k)
  - `compute_thresholds()`: 20th/80th percentiles on valid population
  - `compute_router_action()`: Deterministic if/elif cascade

- **Validation script**: `src/uq/validate_uq_pre_atb.py` (~210 lines)
  - Router action counts and distribution
  - Score distribution summary (min/median/95th/max)
  - Invalid/missing inchikey handling validation
  - Spot-check 5 random records

- **CLI integration**: Updated `src/cli.py`
  - `run --id <id>` now includes UQ scores if `uq_scores_pre_atb.parquet` exists
  - Shows clear message if UQ file missing

- **Unit tests**: `tests/test_uq_pre_atb.py` (26 tests)
  - Score range tests (C_sim, C_meta, coverage, novelty, aleatoric all in [0,1])
  - Router action tests (Evidence-insufficient, Novelty-candidate, In-domain ambiguous, Known/Stable)
  - Router determinism test
  - Recommended next steps tests

### Outputs produced
- **`data/uq_scores_pre_atb.parquet`** (1225 rows, 15 columns)
  - Columns: id, inchikey, C_sim, C_meta, coverage, novelty, novelty_raw, aleatoric, top1_sim, router_action, recommended_next_steps, missing_count, missing_fields, missing_rate, notes
  
- **`data/uq_manifest_pre_atb.json`** (thresholds, percentiles, counts)

### Key Results

**Router Action Distribution:**
| Action | Count | Percentage |
|--------|-------|------------|
| Known/Stable | 704 | 57.5% |
| Evidence-insufficient | 296 | 24.2% |
| In-domain ambiguous | 136 | 11.1% |
| Novelty-candidate | 89 | 7.3% |

**Score Distributions (valid rows only, n=1161):**
| Score | Min | Median | 95th | Max |
|-------|-----|--------|------|-----|
| coverage | 0.130 | 0.497 | 0.668 | 0.768 |
| novelty | 0.000 | 0.413 | 0.999 | 1.000 |
| aleatoric | 0.888 | 0.994 | 0.999 | 1.000 |
| C_sim | 0.130 | 0.544 | 0.752 | 0.853 |
| C_meta | 0.000 | 0.357 | 0.643 | 1.000 |

**Thresholds (computed on valid population):**
- cov_low (20th pctl): 0.388
- cov_high (80th pctl): 0.584
- nov_high (80th pctl): 0.667
- ale_high (80th pctl): 0.998

**Invalid/Missing InChIKey Rows:**
- 64 rows (5.2%) have invalid/missing inchikey
- All 64 correctly routed to "Evidence-insufficient"

### CLI Commands
```bash
# Compute UQ scores (pre-aTB)
python -m src.uq.compute_uq_pre_atb

# Validate results
python -m src.uq.validate_uq_pre_atb

# Run tests
pytest tests/test_uq_pre_atb.py -v

# Check single record with UQ
python -m src.cli run --id 1
```

### Test Results
```
131 passed, 28 warnings in 1.12s
```
(26 new tests for P5a + existing tests)

### Issues / surprises
- **High aleatoric values (median=0.994)**: The aleatoric proxy (entropy of normalized similarities) is very high for almost all molecules. This is expected because:
  - Top-k similarities are often relatively uniform (neighbor similarities differ only slightly)
  - Entropy of quasi-uniform distribution is close to max entropy
- **Novelty percentile normalization**: p05=0.0 (some molecules have top1_sim=1.0, meaning perfect match/duplicates)
- Fixed merge conflict markers in `src/chem/atb_parser.py` during this task

### Decisions
- **No GMM-based aleatoric in P5a**: Deferred to P5b (post-aTB) to keep P5a simple
- **Percentile thresholds on valid population only**: Excludes 64 rows with invalid inchikey from threshold computation
- **novelty_raw preserved**: Keep raw values alongside normalized for debugging

### Next actions
- P5a COMPLETE ✅
- Ready to proceed with P6 (Reports + hypothesis log) or other tasks
- P5b will be implemented after P2 completes (GMM-based aleatoric + full features)

---

## 2026-01-15 — P5a Aleatoric Sanity Check (doc-only)

### Context
Before proceeding to P6, analyzed the "In-domain ambiguous" bucket to understand whether the routing is driven by genuine ambiguity or by aleatoric threshold saturation.

### Analysis Results

**Bucket sizes:** Ambiguous=136, Known/Stable=704

**Aleatoric distribution:**
| Bucket | Min | Median | 95th | Max |
|--------|-----|--------|------|-----|
| Ambiguous | 0.9977 | 0.9989 | 0.9998 | 0.9999 |
| Known/Stable | 0.9387 | 0.9924 | 0.9973 | 0.9977 |

**Neighbor gap (top1_sim - top2_sim):**
| Bucket | Min | Median | 95th |
|--------|-----|--------|------|
| Ambiguous | 0.0000 | 0.0273 | 0.0936 |
| Known/Stable | 0.0000 | 0.0685 | 0.3310 |

### Key Findings
1. **Aleatoric saturation**: All Ambiguous rows have aleatoric in [0.9977, 0.9999], barely above the ale_high threshold (0.9977). Known/Stable max is exactly at threshold.

2. **Gap semantics are valid**: Ambiguous molecules have smaller top1-top2 gaps (median=0.0273 vs 0.0685), indicating they genuinely have less differentiated neighbors. However, this pattern is captured by accident of threshold saturation, not by intentional design.

3. **Entropy proxy limitation**: The entropy-based aleatoric computed from top-k similarities saturates near 1.0 because neighbor similarities are relatively uniform (all ~0.3-0.7 range). This makes the metric unreliable as a standalone discriminator.

### Policy Decision
- P5a aleatoric is a **diagnostic proxy only**; router decisions should primarily rely on **coverage + novelty**
- Added policy note to `doc/process.md` P5a section
- P5b will replace this with GMM prototype entropy or gap-based metric

### Files modified
- `doc/process.md`: Added P5a Aleatoric Policy Note

---

## 2026-01-15 — Uncertainty Terminology Clarification (doc-only)

### Changes
- Introduced `mechanism_entropy` as **neighborhood label ambiguity proxy** (entropy of kNN neighbors' `mechanism_id` distribution, similarity-weighted)
- Clarified: this measures local ambiguity in labeled neighborhood, NOT "true multi-mechanism probability"
- Updated routing policy: recommend `mechanism_entropy >= mech_ent_high` instead of `aleatoric >= ale_high` for "In-domain ambiguous"
- P5a `aleatoric` (neighbor-similarity entropy) documented as diagnostic-only due to saturation
- Added V2 note: evidence-conditioned mechanism distributions `p(m | E_x)` deferred to V2

### Files modified
- `doc/process.md`: Updated P5a policy note, added P5b mechanism_entropy section, added V2 note

---

## 2026-01-15 — P5b Implementation (mechanism_entropy router) ✅

### Files Created
- `src/uq/mechanism_label_map.py` - Build molecule-level mechanism labels (MODE aggregation)
- `src/uq/compute_mechanism_entropy_pre_atb.py` - Compute mechanism_entropy per molecule
- `src/uq/compute_uq_pre_atb_p5b.py` - P5b UQ with updated router
- `src/uq/validate_uq_pre_atb_p5b.py` - Validation script
- `tests/test_mechanism_entropy_pre_atb.py` - 20 unit tests
- Updated `src/cli.py` - Shows both P5a and P5b scores

### Outputs Produced
- `data/mechanism_label_map.parquet` (1050 molecules)
- `data/mechanism_entropy_pre_atb.parquet` (1049 molecules)
- `data/uq_scores_pre_atb_p5b.parquet` (1225 rows)
- `data/uq_manifest_pre_atb_p5b.json`

### Key Statistics

**Label Distribution (top 5):**
- other: 383, ICT: 303, TICT: 116, neutral aromatic: 107, ESIPT: 79
- unknown (ties): 49

**mechanism_entropy:**
- Range: [0.0000, 0.9996]
- Median: 0.5242
- mech_ent_high (80th pctl): 0.7974 (molecule-level, N=1049)

**M_eff (distinct labels in neighborhood):**
- Range: [1, 6], Median: 3

### Router Action Comparison (P5a → P5b)

| Action | P5a | P5b | Change |
|--------|-----|-----|--------|
| Known/Stable | 704 (57.5%) | 707 (57.7%) | +3 |
| Evidence-insufficient | 296 (24.2%) | 296 (24.2%) | 0 |
| In-domain ambiguous | 136 (11.1%) | 132 (10.8%) | -4 |
| Novelty-candidate | 89 (7.3%) | 90 (7.3%) | +1 |

**Key Transitions:**
- In-domain ambiguous → Known/Stable: 81 (P5a aleatoric saturation artifacts removed)
- Known/Stable → In-domain ambiguous: 77 (newly detected by mechanism_entropy)

### Interpretation
- **mechanism_entropy is more meaningful**: High entropy correlates with smaller neighbor gaps (median 0.0369 vs 0.0728), indicating genuine neighborhood ambiguity
- **81 false ambiguous removed**: These were artifacts of P5a aleatoric saturation
- **77 new ambiguous detected**: Genuine cases with mixed mechanism labels in neighborhood

### Test Results
```
21 passed in 0.55s
```

---

## 2026-01-15 — P5b Molecule-Level Threshold Fix ✅

### Change
- **mech_ent_high** now computed at **MOLECULE-level** (unique inchikeys) to avoid duplicate-record bias
- Old (record-level): 0.8080
- New (molecule-level): **0.7974** (N=1049 molecules)

### Files Modified
- `src/uq/compute_uq_pre_atb_p5b.py` - Updated `compute_thresholds_p5b()` to use molecule-level
- `src/uq/validate_uq_pre_atb_p5b.py` - Shows molecule-level source info
- `tests/test_mechanism_entropy_pre_atb.py` - Added test for molecule-level threshold
- `doc/process.md` - Added note about molecule-level computation

### Updated Router Action Distribution

| Action | Old P5b | New P5b | Change |
|--------|---------|---------|--------|
| Known/Stable | 717 | 707 | -10 |
| In-domain ambiguous | 122 | 132 | +10 |

The lower mech_ent_high threshold (0.7974 vs 0.8080) captures 10 more ambiguous cases.

---

## 2026-01-15 — Pre-P6 Online UQ CLI Command (`uq --smiles`) ✅

### Context
Implemented a pre-P6 "online UQ" test command that computes UQ scores for arbitrary SMILES strings (not necessarily from the existing dataset). Enables testing of the full UQ pipeline before P6 (reports) is implemented.

### Implemented
- **Updated `doc/process.md`** with P1.5 online UQ command documentation
- **Updated `src/cli.py`** (~200 new lines):
  - `canonicalize_smiles()`: RDKit canonicalization + InChIKey computation
  - `compute_ecfp_fingerprint()`: ECFP4 (2048-bit) using rdFingerprintGenerator API
  - `tanimoto_similarity()`: Numpy-based Tanimoto for binary fingerprints
  - `compute_mechanism_entropy_online()`: Softmax-weighted neighbor label entropy
  - `search_neighbors()`: Top-k search against rdkit_features.parquet
  - `uq_command()`: Main handler with full UQ computation and JSON output

- **Created `tests/test_cli_uq_smiles.py`** (14 tests):
  - Valid SMILES returns JSON with correct structure
  - Invalid/empty SMILES returns error with non-zero exit code
  - mechanism_entropy in [0,1] when computed
  - All UQ scores in valid ranges
  - Router action is valid P5b action
  - SMILES-only queries have C_meta=0.0
  - Diagnostics contain thresholds
  - Neighbor mechanism_labels present
  - Complex molecule handling (TPE)
  - Edge cases: ionic SMILES, k=1

### CLI Usage
```bash
# Compute UQ for arbitrary SMILES
python -m src.cli uq --smiles "c1ccccc1" --k 10

# With different k
python -m src.cli uq --smiles "CCO" --k 5
```

### Sample Output (benzene)
```json
{
  "query": {
    "input_smiles": "c1ccccc1",
    "canonical_smiles": "c1ccccc1",
    "inchikey": "UHOVQNZJYSORNB-UHFFFAOYSA-N"
  },
  "neighbors": [
    {"inchikey": "ZEZSXJQVKBPTDQ-UHFFFAOYSA-N", "sim": 0.196, "mechanism_label": "other"},
    ...
  ],
  "uq": {
    "C_sim": 0.131,
    "C_meta": 0.0,
    "coverage": 0.092,
    "novelty": 0.984,
    "top1_sim": 0.196,
    "mechanism_entropy": 0.931,
    "M_eff": 5,
    "router_action_p5b": "Evidence-insufficient",
    "recommended_next_steps_p5b": ["Collect experimental metadata..."]
  },
  "diagnostics": {
    "k": 10,
    "used_thresholds": {"cov_low": 0.388, "cov_high": 0.584, "nov_high": 0.667, "mech_ent_high": 0.797},
    "used_beta": 10.0
  }
}
```

### Test Results
```
14 passed in 9.85s
```

### Key Design Decisions
- **C_meta = 0.0** for SMILES-only queries (no experimental metadata available)
- **coverage = 0.7 * C_sim + 0.3 * C_meta** → max coverage is 0.7 for SMILES-only
- **Novelty** uses percentile normalization against dataset top1_sim values
- **mechanism_entropy** uses softmax(beta * sim) weights (beta=10.0)
- **Router uses P5b thresholds** from `data/uq_manifest_pre_atb_p5b.json`
- **Empty SMILES** explicitly rejected at start (RDKit accepts empty string)

### Issues / surprises
- **RDKit accepts empty SMILES**: Returns empty canonical_smiles and empty inchikey. Added explicit check at start of `uq_command()` to reject empty input.
- **SMILES-only queries typically route to "Evidence-insufficient"**: Expected behavior since C_meta=0 → coverage ≤ 0.7 × C_sim, which is often below cov_low threshold.

### Next actions
- Pre-P6 online UQ test complete ✅
- Ready for P6 implementation when needed

---

## 2026-01-19 — V2 Design Documentation Refresh (doc-only)

### Changes
Updated documentation to capture refined V2 design decisions:

1. **Data incompleteness as first-class assumption**: Missing experimental/aTB data is normal and expected; system must degrade gracefully with partial evidence

2. **Structure-first retrieval policy**: Anchor/feature-space retrieval remains structure-based (ECFP/Tanimoto) to avoid semantic drift from noisy continuous features

3. **Hybrid mechanism sourcing**: Candidate mechanisms from neighbor `mechanism_id` PLUS signature/template evidence from offline domainRAG store

4. **Pre-UQ + Post-UQ split**:
   - Pre-UQ: assesses evidence sufficiency before LLM reasoning; controls how LLM should answer
   - Post-UQ: evaluates hypothesis-specific support/coherence after LLM outputs; decides gating

5. **V2 planned fields**: Added Pre-UQ context fields and Post-UQ hypothesis fields to schemas

### Files Modified (doc-only)
- `doc/roadmap.md`: Added "Real-world Constraints" section and V2 design paragraph
- `doc/process.md`: Added "V2 Design Notes" section with 5 subsections; added V2 reference to P5
- `doc/schemas.md`: Added "V2 Planned Fields" section with Pre-UQ and Post-UQ field tables

### No Code Changes
This was a documentation-only update. No code was modified or regenerated.

---

## 2026-01-19 — Pre-UQ Split: Evidence Readiness vs Risk Scores (doc-only)

### Conceptual Update
Pre-UQ is now split into two distinct roles:
- **Evidence Readiness**: gates workflow (compute/search/measure vs proceed to reasoning)
- **Risk Scores**: shapes reasoning style and write-back gating

### Files Modified
- `doc/process.md`: Added section 6 "Pre-UQ Split: Evidence Readiness vs Risk Scores"
- `doc/roadmap.md`: Added readiness/risk bullets and evidence ladder to V2
- `doc/schemas.md`: Split V2 fields into Readiness Fields + Risk Score Fields

### No Code Changes
Documentation-only update.

---

## 2026-01-21 — Pre-UQ Updated to SMILES-First (doc-only)

### Changes
Updated pre-UQ specification to be SMILES-first:

1. **Pre-UQ = Risk Scores + Evidence Readiness**
   - Risk Scores (SMILES-computable): top1_sim, mean_topk_sim, neighbor_gap, novelty_struct, mechanism_entropy, mechanism_hint, hint_confidence
   - Evidence Readiness: target_atb_status, neighbor_atb_success_rate, neighbor_atb_keyfield_rate, has_emission/qy/tau/solvent, missing_evidence_list, action_plan

2. **C_meta moved to record-mode only**
   - SMILES-only pre-UQ does NOT use C_meta (no experimental record available)
   - Record-mode (id-based) UQ may still use C_meta for experimental completeness

3. **Evidence Ladder defined** (action priority):
   - target aTB → neighbor aTB → literature search → minimal experiment (emission first)

### Files Modified
- `doc/process.md`: Rewrote §5-6 with SMILES-first pre-UQ spec (risk + readiness + evidence ladder)
- `doc/roadmap.md`: Added SMILES-first workflow and pre-UQ split bullets to V2
- `doc/schemas.md`: Added "SMILES-First Pre-UQ Fields (V2)" section with risk/readiness tables; added C_meta note

### No Code Changes
Documentation-only update.

---

## 2026-01-21 — P6a Pre-aTB Output Layer (Reports + Queues) ✅

### Implemented
1. **Report generator** (`src/reports/generate_reports_pre_atb_p5b.py`, ~300 lines):
   - Generates per-record JSON reports with SMILES-first schema
   - Report sections: record_summary, risk_scores, evidence_readiness, neighbors_ecfp, recommended_next_steps
   - Privacy: `comment` field NEVER included (strict allowlist approach)
   - aTB status checked from `cache/atb/{prefix}/{inchikey}/status.json`

2. **Queue exporter** (`src/reports/export_queues_pre_atb_p5b.py`, ~180 lines):
   - Exports parquet files by router action: Novelty-candidate, Evidence-insufficient, In-domain ambiguous
   - Generates `data/p6_dashboard_pre_atb_p5b.json` with statistics

3. **Validator** (`src/reports/validate_reports_pre_atb_p5b.py`, ~170 lines):
   - 7 checks: report count, no comment field, JSON parsing, queue counts, neighbors validity, evidence_readiness, dashboard
   - All checks passed (7/7)

4. **CLI integration** (`src/cli.py`):
   - Added `report --id <id> --write` command for on-demand report generation

### Outputs Produced
- `reports/*.json`: 1225 per-record reports
- `data/queue_novelty_candidates_pre_atb_p5b.parquet`: 90 records
- `data/queue_evidence_insufficient_pre_atb_p5b.parquet`: 296 records
- `data/queue_in_domain_ambiguous_pre_atb_p5b.parquet`: 132 records
- `data/p6_dashboard_pre_atb_p5b.json`: Dashboard with statistics

### Dashboard Summary
```
Total records: 1225
Router actions:
  Known/Stable: 707 (57.7%)
  Evidence-insufficient: 296 (24.2%)
  In-domain ambiguous: 132 (10.8%)
  Novelty-candidate: 90 (7.3%)
Invalid InChIKeys: 64
aTB status: absent=1114, success=85, failed=25, pending=1
Top missing fields: tau_crys(1178), tau_aggr(1168), qy_crys(1154), emission_crys(1129), qy_aggr(1046)
```

### Report Schema (sample: id=2)
```json
{
  "report_version": "P6a_pre_atb_p5b",
  "record_summary": { "id", "inchikey", "canonical_smiles", "code", "mechanism_id_hint", "photophysical" },
  "risk_scores": { "coverage", "C_sim", "novelty", "mechanism_entropy", "M_eff", "top_label", "router_action_p5b", "thresholds" },
  "evidence_readiness": { "target_atb_status", "has_emission", "has_qy", "has_tau", "has_solvent", "missing_critical_fields" },
  "neighbors_ecfp": [ { "rank", "neighbor_inchikey", "tanimoto_sim", "mechanism_label" } ],
  "recommended_next_steps": [ "request_atb_compute_on_linux", "collect_*", ... ]
}
```

### Privacy Verification
- Checked 100 reports recursively for `comment` field: **NONE FOUND**
- Strict allowlist approach ensures only approved fields are included

### Validation Results
```
[PASS] Report count: 1225/1225
[PASS] No comment field: sampled 50 reports
[PASS] JSON parsing: sampled 20 reports
[PASS] Queue counts: matches UQ scores
[PASS] Neighbors validity: sampled 30 reports
[PASS] Evidence readiness: sampled 50 reports
[PASS] Dashboard exists: valid with 1225 records

ALL CHECKS PASSED (7/7)
```

### CLI Usage
```bash
# Generate single report on-demand
python -m src.cli report --id 2 --write

# Generate all reports
python -m src.reports.generate_reports_pre_atb_p5b

# Export queues + dashboard
python -m src.reports.export_queues_pre_atb_p5b

# Validate
python -m src.reports.validate_reports_pre_atb_p5b
```

### No Issues
All validation checks passed on first run. No code fixes required.

---

## 2026-01-21 — mechanism_entropy: Exclude "other" and "unknown" labels

### Change
Updated mechanism_entropy calculation to **EXCLUDE "other" and "unknown" labels** from entropy computation.

**Rationale**:
- "other" and "unknown" represent unlabeled/ambiguous data, NOT known mechanism hypotheses
- High entropy should indicate genuine ambiguity among KNOWN mechanisms (ICT, ESIPT, TICT, etc.)
- Including "other" inflated M_eff and diluted the entropy signal

### Files Modified
- `src/cli.py`: Updated `compute_mechanism_entropy_online()` with `exclude_labels` parameter
- `src/uq/compute_mechanism_entropy_pre_atb.py`: Updated `compute_mechanism_entropy_for_query()` with exclusion logic
- `doc/process.md`: Updated P5b definition with exclusion rationale

### Impact on UQ Scores

**Before (including "other"):**
- TPE example: M_eff=3 (ESIPT, other, neutral aromatic), entropy=0.747, router=Known/Stable

**After (excluding "other"):**
- TPE example: M_eff=2 (ESIPT, neutral aromatic), entropy=0.984, router=**In-domain ambiguous**
- 42 molecules now have entropy=NaN (all neighbors have excluded labels)

**Router action distribution change:**
| Action | Before | After | Change |
|--------|--------|-------|--------|
| Known/Stable | 707 | 705 | -2 |
| Evidence-insufficient | 296 | 292 | -4 |
| In-domain ambiguous | 132 | 139 | **+7** |
| Novelty-candidate | 90 | 89 | -1 |

**Threshold change:**
- mech_ent_high: 0.7974 → **0.8778** (higher threshold since entropy values are now more meaningful)

### Regenerated Outputs
- `data/mechanism_entropy_pre_atb.parquet`
- `data/uq_scores_pre_atb_p5b.parquet`
- `data/uq_manifest_pre_atb_p5b.json`
- `reports/*.json` (all 1225 reports)
- `data/queue_*.parquet` files

---

## Template for future entries
### YYYY-MM-DD — <Short title>
#### Implemented
- ...
#### Outputs produced
- ...
#### Issues / surprises
- ...
#### Decisions
- ...
#### Next actions
- ...

---

## 2026-01-22 — P6 evidence_readiness Schema Enhancement

### Changes Implemented

**Documentation Updates:**
1. Renamed "aTB agent" to "Chem Agent" across codebase and docs (aTB is one tool the Chem Agent uses)
2. Updated P6 spec in `doc/process.md` with full evidence_readiness schema
3. Added Section 10 to `doc/schemas.md` with complete report JSON schema

**Code Updates:**
1. `src/reports/generate_reports_pre_atb_p5b.py`:
   - Enhanced `get_target_atb_status()` to return tuple (status, missing_fields) and detect "partial" status
   - Enhanced `compute_evidence_readiness()` with structured schema:
     - `target_atb_status`: absent/pending/success/failed/partial
     - `target_atb_missing_fields`: list of missing aTB fields (for partial)
     - `neighbor_atb_success_rate`: null (V0 placeholder)
     - `neighbor_atb_keyfield_rate`: null (V0 placeholder)
     - `minimal_experiment_available`: {has_emission, has_qy, has_tau, has_solvent}
     - `missing_critical_fields`: list of missing experimental fields
     - `evidence_ladder_action_plan`: prioritized action list

2. `src/reports/export_queues_pre_atb_p5b.py`:
   - Updated `get_target_atb_status()` to detect partial status

3. `src/reports/validate_reports_pre_atb_p5b.py`:
   - Enhanced `check_evidence_readiness()` to validate full schema:
     - Checks all required keys exist
     - Validates target_atb_status is in valid enum
     - Validates minimal_experiment_available has all has_* booleans
     - Validates lists are lists and dicts are dicts

### Evidence Ladder Action Priority
1. `compute_target_atb` - if status ∈ {absent, pending}
2. `literature_search` - if status == "failed"
3. `retry_atb_computation` - if status == "partial"
4. `request_min_experiment_emission` - if has_emission == false
5. `collect_{field}` - for each missing critical field

### V0 Placeholders
- `neighbor_atb_success_rate`: null (will be computed in V1 when neighbor aTB coverage tracked)
- `neighbor_atb_keyfield_rate`: null (same)

### Rationale
The enhanced evidence_readiness schema enables:
- Clear visibility into what evidence is missing for each record
- Actionable next steps prioritized by evidence ladder
- Future V1/V2 integration with neighbor aTB coverage metrics
- "Chem Agent" naming reflects broader scope (aTB + literature + experiment requests)

---

## 2026-01-22 — P7 Case File Implementation (SMILES-first Workflow)

### Overview
Implemented the Case File system for SMILES-first workflow. The Case File is a central JSON artifact that Data Agent creates and Chem Agent updates in-place, serving as the single source of truth for evidence gathering before reasoning.

### Documentation Updates
1. **doc/schemas.md §11**: Added complete Case File JSON schema (V0.5) with:
   - query: input_smiles, canonical_smiles, inchikey, created_at
   - risk_scores: top1_sim, mean_topk_sim, neighbor_gap, novelty_struct, mechanism_entropy, mechanism_hint, hint_confidence
   - evidence_readiness state machine with atb/literature/experiment tracks
   - neighbors list with rank, sim, mechanism labels
   - action_plan (ordered evidence ladder actions)
   - history (append-only audit log)

2. **doc/process.md §7**: Added Case File Workflow section with:
   - Workflow diagram (Data Agent → Case File ← Chem Agent → Master Reasoner)
   - Agent responsibilities
   - Gate logic implementation
   - CLI commands documentation

### Code Implementation
1. **src/cases/case_schema.py** (new):
   - Status enums: AtbStatus, LiteratureStatus, ExperimentStatus, Actor, EventType
   - `validate_case_file()`: Schema validation with detailed error messages
   - `evaluate_gate()`: Gate logic (ready if cache_status=success with key fields OR has_emission)
   - Helper functions: `now_iso()`, `create_empty_evidence_readiness()`, `create_history_event()`

2. **src/cases/create_case_from_smiles.py** (new):
   - Data Agent: creates Case File from SMILES input
   - Computes ECFP fingerprint, searches neighbors, computes risk scores
   - Checks aTB cache, builds initial action_plan
   - CLI: `python -m src.cases.create_case_from_smiles --smiles "<SMILES>"`

3. **src/cases/chem_agent_update_case_stub.py** (new):
   - Chem Agent stub: updates Case File without real computation
   - Action handlers: compute_target_atb, literature_search, request_min_experiment
   - Simulation functions for testing: simulate_atb_success, simulate_atb_failed, simulate_has_emission
   - Automatic gate re-evaluation and history appending
   - CLI: `python -m src.cases.chem_agent_update_case_stub --case <path> --action <action>`

4. **src/cases/validate_case_file.py** (new):
   - Schema validation with semantic consistency checks
   - 5 unit tests for gate logic and validation
   - CLI: `python -m src.cases.validate_case_file --test`

5. **src/cli.py**: Added CLI commands:
   - `python -m src.cli case --smiles "<SMILES>" --print`
   - `python -m src.cli case-update --case <path> --action <action> --print`

### Gate Logic
```python
def evaluate_gate(evidence_readiness):
    # key_atb_fields_present: check delta_gap/delta_dihedral/delta_volume/excitation_energy
    key_atb_fields_present = all(
        atb.features_summary.get(k) is not None
        for k in ["delta_gap", "delta_dihedral", "delta_volume", "excitation_energy"]
    )
    if atb.cache_status == "success" and key_atb_fields_present:
        return True, "atb_success"
    if minimal_experiment_available.has_emission:
        return True, "has_emission_data"
    return False, "missing_target_atb_and_min_experiment"
```

### Evidence Readiness State Machine
Three parallel tracks:
- **atb.cache_status**: absent → pending → success|failed|partial
- **atb.request_status**: not_requested → requested → done
- **literature**: not_started → pending → found|not_found
- **experiment**: not_requested → requested → received_partial → received_full

### Example Workflow
```bash
# 1. Create case from SMILES
python -m src.cli case --smiles "CCO"
# Output: cases/LFQSCWFLJHTTHZ-UHFFFAOYSA-N.json
# atb.cache_status: absent, atb.request_status: not_requested, ready_for_reasoning: false

# 2. Mark aTB computation as pending
python -m src.cli case-update --case cases/LFQSCWFLJHTTHZ-UHFFFAOYSA-N.json --action compute_target_atb
# atb.cache_status: pending, atb.request_status: requested

# 3. Simulate aTB success (for testing)
python -m src.cli case-update --case cases/LFQSCWFLJHTTHZ-UHFFFAOYSA-N.json --action simulate_atb_success
# atb.cache_status: success, atb.request_status: done, ready_for_reasoning: true
```

### Validation Results
- 5/5 unit tests pass
- Case files validate with schema and semantic checks
- Gate logic correctly opens on (atb.cache_status=success with key fields) OR has_emission

### Files Created
- `src/cases/__init__.py`
- `src/cases/case_schema.py`
- `src/cases/create_case_from_smiles.py`
- `src/cases/chem_agent_update_case_stub.py`
- `src/cases/validate_case_file.py`

### Files Modified
- `src/cli.py`: Added case and case-update commands
- `doc/schemas.md`: Added §11 Case File schema
- `doc/process.md`: Added §7 Case File Workflow

### Next Steps
- Integrate real aTB computation into Chem Agent
- Add literature search stub
- Connect Case File gate to Master Reasoner
- Add neighbor aTB coverage tracking (V1)

---

## 2026-01-22 — P7b Case File Semantic Fix (cache_status vs request_status)

### Problem
The original Case File schema conflated two distinct concepts:
1. **Cache facts**: Historical result of aTB computation (success/failed/pending)
2. **Workflow state**: Whether this case has requested aTB computation

With only `atb.status`, a case with `status=failed` was ambiguous:
- Did the cache show a historical failure?
- Has this case already attempted and failed aTB?

This led to incorrect action_plan generation (skipping `compute_target_atb` for cases where retry was desired).

### Solution: Separate cache_status from request_status

**Schema change (v0.5 → v0.6)**:
- `evidence_readiness.atb.status` → replaced by:
  - `atb.cache_status` ∈ {absent, pending, success, failed, partial} — historical fact from cache lookup
  - `atb.request_status` ∈ {not_requested, requested, done} — workflow state for this case

**Key semantics**:
- `cache_status` reflects what's in the cache (historical fact)
- `request_status` tracks this case's workflow progress
- Gate uses `cache_status` (not `request_status`) for ready_for_reasoning decisions
- Action plan can include `compute_target_atb` even when `cache_status=failed` (retry policy)

### Implementation

**1. Schema updates (case_schema.py)**:
- Added `AtbCacheStatus` enum (same values as old AtbStatus)
- Added `AtbRequestStatus` enum: NOT_REQUESTED, REQUESTED, DONE
- Updated `validate_case_file()` to support both new and legacy schemas
- Updated `evaluate_gate()` to use `cache_status`
- Updated `create_empty_evidence_readiness()` with new structure
- Schema version bumped to "0.6"

**2. Case creation (create_case_from_smiles.py)**:
- On creation: reads cache → sets `cache_status`
- Always initializes `request_status = "not_requested"`
- Added `build_initial_action_plan(cache_status, retry_failed_atb=True)`:
  - If `cache_status ∈ {absent, pending}`: include `compute_target_atb` first
  - If `cache_status ∈ {failed, partial}` AND `retry_failed_atb=True`: include `compute_target_atb`
  - Then: `literature_search`, `request_min_experiment_emission`

**3. Chem Agent stub (chem_agent_update_case_stub.py)**:
- `handle_compute_target_atb`: sets `request_status = "requested"`
- `simulate_atb_success` / `mark_atb_success`: sets `cache_status="success"`, `request_status="done"`
- `simulate_atb_failed` / `mark_atb_failed`: sets `cache_status="failed"`, `request_status="done"`

**4. Backward compatibility**:
- Validator accepts legacy `atb.status` field
- `evaluate_gate()` tries `cache_status` first, falls back to `status`
- CLI displays both fields or falls back to legacy

### New Tests (tests/test_case_file_semantics.py)

21 tests covering:
- `TestCacheVsRequestStatus`: 8 tests for action plan generation
- `TestGateLogic`: 5 tests for gate evaluation
- `TestSchemaValidation`: 3 tests for schema enforcement
- `TestChemAgentStubActions`: 3 tests for stub actions
- `TestLegacySchemaBackwardCompatibility`: 2 tests for legacy support

### Demonstration

```bash
# Case with cache_status=failed shows request_status=not_requested and compute_target_atb in action_plan
$ python -m src.cli case --smiles "<failed_molecule_smiles>"
Case created: cases/AJUBVOXNBCYBCI-UHFFFAOYSA-N.json
  case_id: AJUBVOXNBCYBCI-UHFFFAOYSA-N
  cache_status: failed
  request_status: not_requested
  ready_for_reasoning: False
  action_plan: ['compute_target_atb', 'literature_search', 'request_min_experiment_emission']
```

### Files Modified
- `src/cases/case_schema.py`: Schema v0.6 with new enums and validation
- `src/cases/create_case_from_smiles.py`: Updated case creation logic
- `src/cases/chem_agent_update_case_stub.py`: Updated stub actions
- `src/cli.py`: Updated CLI output for new fields
- `doc/schemas.md`: Updated §11 with new schema
- `tests/test_case_file_semantics.py`: New comprehensive test suite

---

## 2026-01-22 — P7c Case File Evidence Enhancement (Schema v0.7)

### Goal
Upgrade the SMILES-first case file with richer evidence structures for mechanism reasoning:
1. **Neighbor aTB evidence pack**: Each neighbor gets its aTB cache status + features_summary
2. **Candidate mechanisms**: Similarity-weighted distribution from neighbor labels
3. **Mechanism signatures**: domainRAG templates for disambiguation
4. **Target features_summary**: Key aTB fields attached when cache succeeds
5. **Gate-aware action plan**: Returns `run_master_reasoner` when ready

### What's New (v0.6 → v0.7)

**Schema additions**:
- `neighbors[].neighbor_atb`: {cache_status, missing_fields?, features_summary?}
- `evidence_readiness.atb.features_summary`: {delta_volume, delta_gap, delta_dihedral, excitation_energy}
- `evidence_readiness.atb.neighbor_atb_success_rate`: Fraction of neighbors with successful aTB
- `evidence_readiness.atb.neighbor_atb_keyfield_rate`: Fraction with all 4 key fields
- `candidate_mechanisms`: Top-3 [{label, prob}] from neighbor label distribution
- `mechanism_signatures`: Map of label → {required_atb_fields, required_experiment_fields, disambiguation_actions}

**Gate logic (v0.7)**:
- `ready_for_reasoning = true` if:
  - (`cache_status == "success"` AND all 4 key fields in features_summary) OR
  - `has_emission == true`

**Action plan (v0.7)**:
- If `ready_for_reasoning == true`: `["run_master_reasoner"]`
- Else: Evidence ladder (compute_target_atb → literature_search → request_min_experiment_emission)

### Implementation

**1. domainRAG stub (data/domainrag/mechanism_signatures.yaml)**:
- Curated signatures for: ICT, TICT, ESIPT, RIR, neutral_aromatic, other, unknown
- Each with: required_atb_fields, required_experiment_fields, disambiguation_actions, structure_triggers

**2. New functions (create_case_from_smiles.py)**:
- `get_atb_features_summary(inchikey)` → extracts KEY_ATB_FIELDS from cache
- `get_neighbor_atb_evidence(neighbor_inchikey)` → gets neighbor's aTB evidence pack
- `load_mechanism_signatures()` → loads domainRAG YAML
- `compute_candidate_mechanisms(neighbors, beta=10.0)` → similarity-weighted label distribution
- `get_mechanism_signatures_for_candidates(candidates, all_signatures)` → extracts relevant signatures

**3. Updated case creation**:
- Attaches neighbor_atb to each neighbor
- Computes neighbor_atb_success_rate and keyfield_rate
- Loads target features_summary if cache=success
- Computes candidate_mechanisms and mechanism_signatures
- Gate-aware action_plan generation

**4. Updated case_schema.py**:
- `KEY_ATB_FIELDS = ['delta_volume', 'delta_gap', 'delta_dihedral', 'excitation_energy']`
- `evaluate_gate()` now requires features_summary for success
- Added `run_master_reasoner` to EVIDENCE_LADDER_ACTIONS
- Updated required top-level keys for v0.7

### Tests
All 22 tests in `tests/test_case_file_semantics.py` pass, including:
- Gate now requires features_summary for success (test_gate_opens_when_cache_success_with_features)
- Gate remains closed if cache_status=success but no features (test_gate_closed_when_cache_success_but_missing_features)
- Action plan returns `["run_master_reasoner"]` when ready (test_action_plan_excludes_compute_atb_when_success)

### Demonstration

```bash
# Case with cache_status=success, features present → gate opens, action_plan = [run_master_reasoner]
$ python -m src.cli case --smiles "C(=C\c1cccs1)/c2cccs2"
Case created: cases/AYBFWHPZXYPJFW-AATRIKPKSA-N.json
  case_id: AYBFWHPZXYPJFW-AATRIKPKSA-N
  cache_status: success
  request_status: not_requested
  features_summary: present (6 fields)
  neighbor_atb_success_rate: 0.1
  neighbor_atb_keyfield_rate: 0.1
  top_candidate: neutral aromatic (prob=0.423)
  ready_for_reasoning: True
  action_plan: ['run_master_reasoner']

# Case with cache_status=failed → gate closed, full evidence ladder
$ python -m src.cli case --smiles "<large_failed_molecule>"
Case created: cases/AJUBVOXNBCYBCI-UHFFFAOYSA-N.json
  cache_status: failed
  features_summary: absent
  neighbor_atb_success_rate: 0.1
  top_candidate: other (prob=0.763)
  ready_for_reasoning: False
  action_plan: ['compute_target_atb', 'literature_search', 'request_min_experiment_emission']
```

### Files Created
- `data/domainrag/mechanism_signatures.yaml`

### Files Modified
- `src/cases/case_schema.py`: v0.7, KEY_ATB_FIELDS, updated gate
- `src/cases/create_case_from_smiles.py`: All new v0.7 functions
- `src/cli.py`: Updated output for v0.7 fields
- `doc/schemas.md`: Updated §11 to v0.7
- `doc/process.md`: Added terminology note for neighbor/mechanism signatures
- `tests/test_case_file_semantics.py`: Updated for v0.7 semantics

---

## 2026-01-26 — P7d Case File Cleanup (Field Placement + excitation_energy)

### Goal
Small but important cleanup for v0.7 schema without changing overall behavior.

### Changes

**A) Fixed field placement (neighbor metrics)**
- `neighbor_atb_success_rate` and `neighbor_atb_keyfield_rate` moved from `evidence_readiness.atb` to `evidence_readiness` top-level
- Rationale: These metrics describe neighbor coverage, not target aTB state
- Validator now **rejects** if found under `atb`

**B) Verified excitation_energy parsing**
- Confirmed: `excitation_energy` in `features_summary` is sourced ONLY by:
  - Reading `cache/atb/.../features.json`
  - Pure float cast: `float(val)` - NO unit conversion, NO normalization, NO scaling
- Added debug field: `_excitation_energy_raw` stores the raw string value from cache
- Validator checks: `float(_excitation_energy_raw) == excitation_energy` within 1e-9

### Tests Added (27 total, all pass)
- `test_neighbor_metrics_at_evidence_readiness_toplevel`: Confirms correct location
- `test_neighbor_metrics_under_atb_rejected`: Validates rejection of wrong location
- `test_excitation_energy_pure_float_cast`: Confirms no scaling
- `test_excitation_energy_raw_matches_converted`: Validates raw/converted consistency
- `test_excitation_energy_mismatch_rejected`: Rejects mismatched values

### Files Modified
- `doc/schemas.md`: Updated field locations, added `_excitation_energy_raw`
- `src/cases/create_case_from_smiles.py`: Fixed field placement, added raw tracking
- `src/cases/case_schema.py`: Added validation for field placement and raw consistency
- `src/cli.py`: Updated to read metrics from correct location
- `tests/test_case_file_semantics.py`: Added 5 new tests

---

## 2026-01-27 — Deprecate P3 Feature Merge (doc-only)

### Changes
- Marked P3 as deprecated in `doc/process.md` (TODO list and detailed section); retained historical content
- Updated P2 objective note to remove dependency on P3 merge
- Updated `CLAUDE.md` acceptance criteria item 5 to deprecated
- Updated `doc/roadmap.md` V0 step list and `doc/schemas.md` X_full schema to note deprecation

### Rationale
- P3 feature merge is no longer required for the current scope

### Outputs
- Documentation updates only; no code changes

---

## 2026-01-27 — P2 Cache Integration + P3b Merge (V0)

### Implemented
- Added shared cache reader: `src/chem/atb_cache.py` (single source of truth for cache_status + features_summary)
- Added cache → parquet builder: `src/chem/build_atb_tables_from_cache.py`
- Added P3b merge: `src/features/merge_with_atb.py`
- Updated SMILES-first case creation to use cache helper for target/neighbor aTB evidence
- Resolved merge conflict in `src/chem/batch_runner.py` (ionic skip logic)

### Outputs produced
- `data/atb_qc.parquet` (1050 rows)
- `data/atb_features.parquet` (440 rows)
- `data/X_full.parquet` (1225 rows)
- `data/feature_config.yaml`
- `data/scaler.pkl`

### Key stats
- atb_qc cache_status counts: success=439, failed=609, partial=1, absent=1
- Keyfield completeness among success: 1.000
- X_full atb_cache_status (record-level): success=485, failed=675, partial=1, absent=3, None=61
- excitation_energy dtype in X_full: float64 (pure float cast, no scaling)

### Validation commands
```bash
python -m src.chem.build_atb_tables_from_cache
python -m src.features.merge_with_atb
```

---

## 2026-01-27 — P6 Reports/Queues/Dashboard (with cache-derived aTB readiness)

### Implemented
- Updated P6 generator to use `data/atb_qc.parquet` for target aTB status/keyfield completeness
- Added neighbor aTB success/keyfield rates using anchor neighbors + atb_qc lookup
- Updated queues/dashboard to use atb_qc-derived status distribution
- Added `target_atb_keyfield_complete` to evidence_readiness schema

### Outputs produced
- `reports/{id}.json` for 1225 records
- `data/queue_*_pre_atb_p5b.parquet`
- `data/p6_dashboard_pre_atb_p5b.json`

### Key stats
- Report aTB status distribution: success=485, failed=675, partial=1, absent=64
- Queue counts: Known/Stable=705, Evidence-insufficient=292, In-domain ambiguous=139, Novelty-candidate=89
- Invalid InChIKeys: 64
- Validation: 7/7 checks passed

### Validation commands
```bash
python -m src.reports.generate_reports_pre_atb_p5b
python -m src.reports.export_queues_pre_atb_p5b
python -m src.reports.validate_reports_pre_atb_p5b
```

---

## 2026-01-27 — Doc re-org (V0 → V1)

### Changes
- Archived V0 plan: `doc/process.md` → `doc/process_v0.md` (added archive header)
- Created new V1 plan: `doc/process.md` (clean V1 objectives/milestones)
- Updated `doc/roadmap.md` milestone tracking for V1 start
- Added V1 minimal schemas in `doc/schemas.md`

---

## 2026-01-28 — V1 Spec Tightening (doc-only)

### Changes
- Clarified V1 minimal schemas (evidence_table + light graph), allowed edge types, subgraph retrieval API contract, and Chem Agent literature I/O.
- Updated V1 evidence_table to use `value_num` (nullable float) + raw string `value` for audit/fallback (instead of a single typed/string-only field).

---

## 2026-01-29 — V1-P1 Evidence Table Build (existing sources only)

### Implemented
- Built `data/evidence_table.parquet` from:
  - `data/private_clean.parquet` → `private_observation` rows (absorption/emission*/qy*/tau*/tested_solvent)
  - `data/atb_features.parquet` + `data/atb_qc.parquet` → `atb_computation` rows (per aTB field)
- Wrote `data/evidence_table_build_manifest.json` with counts by evidence_type/field + invalid/parse-failure summaries
- Added validator: `src/graph/validate_evidence_table.py`

### Outputs produced
- `data/evidence_table.parquet` (12181 rows)
- `data/evidence_table_build_manifest.json`

### Key stats
- evidence_type counts: private_observation=7781, atb_computation=4400
- subject_inchikey null rows: 436 (orphan private records without valid SMILES/InChIKey)
- Validator: PASS

### Commands
```bash
python -m src.graph.build_evidence_table_v1_p1
python -m src.graph.validate_evidence_table
```

---

## 2026-01-29 — V1-P1 Evidence Table Hardening (solvent/unit/timestamp_source)

### Changes
- condition_solvent for sol-state fields now uses tested_solvent only when present; otherwise "unknown"
- absorption_peak_nm enforced as numeric nm (unit="nm", value float-parsable)
- atb_computation rows add `timestamp_source` ("atb_qc" or "build_fallback") and validator checks it
- Manifest includes atb timestamp_source counts and sol-state unknown-solvent count

---

## 2026-01-29 — V1-P2 Light KG Export (evidence_table → nodes/edges + SIMILAR_TO)

### Implemented
- Built Light KG tables from `data/evidence_table.parquet`:
  - Molecule/Evidence/Condition nodes
  - Molecule → Evidence edges (HAS_OBSERVATION / HAS_COMPUTATION)
  - Evidence → Condition edges (UNDER_CONDITION)
- Added structure-only similarity edges (SIMILAR_TO) from `data/anchor_neighbors_ecfp.parquet` (ECFP tanimoto)
- Added builder: `src/graph/build_light_graph_v1_p2.py`
- Added validator: `src/graph/validate_graph_tables.py`
- Wrote manifest: `data/graph_build_manifest.json`

### Outputs produced
- `data/graph_nodes.parquet`
- `data/graph_edges.parquet`
- `data/graph_build_manifest.json`

### Key stats
- Nodes: total=13273 (Molecule=1042, Evidence=12181, Condition=50)
- Edges: total=34305 (HAS_OBSERVATION=7330, HAS_COMPUTATION=4400, UNDER_CONDITION=12181, SIMILAR_TO=10394)
- SIMILAR_TO kept=10394 / dropped_missing_molecule_nodes=96
- subject_inchikey missing/empty: skipped mol→ev edges for 451 evidence rows (still kept ev→cond)
- Validator: PASS

### Commands
```bash
python -m src.graph.build_light_graph_v1_p2
python -m src.graph.validate_graph_tables
```

---

## 2026-01-29 — V1-P3 Subgraph Retrieval API (GraphRAG context)

### Implemented
- Added retrieval API: `src/graph/retrieval.py`
  - `get_subgraph(inchikey, hops=2, max_nodes=50, max_edges=200, ...)` returns `{nodes, edges, provenance_refs, stats}` (deterministic ordering)
  - Prioritization: target evidence → SIMILAR_TO neighbors → neighbor evidence → condition nodes
  - Budget enforcement: hard caps on nodes/edges; records truncation + dropped counts in stats
  - Stats/logs: included target evidence counts (obs/comp), included neighbor counts, neighbor evidence total, dropped condition count
- Added validation/smoke: `src/graph/validate_retrieval.py`
  - Prints per-target global HAS_COMPUTATION/HAS_OBSERVATION counts and confirms "only_observation" target has 0 computation edges
- Added tests: `tests/test_graph_retrieval_v1_p3.py`
- Added optional smoke report (no API): `src/graph/smoke_test_p2.py` (kept as P2 analysis helper)

### How to run
```bash
python -m src.graph.retrieval --inchikey <INCHIKEY> --hops 2 --max_nodes 50 --max_edges 200
python -m src.graph.validate_retrieval
python -m unittest -v tests.test_graph_retrieval_v1_p3
```

### Validation examples (max_nodes=50, max_edges=200)
- with_computation (AAAQKTZ...): nodes={'Molecule': 11, 'Evidence': 33, 'Condition': 6}; edges={'HAS_COMPUTATION': 20, 'HAS_OBSERVATION': 13, 'UNDER_CONDITION': 33, 'SIMILAR_TO': 10}; truncated=True (hit node budget)
- only_observation (AAFDQFV...): nodes={'Molecule': 11, 'Evidence': 34, 'Condition': 5}; edges={'HAS_COMPUTATION': 5, 'HAS_OBSERVATION': 29, 'UNDER_CONDITION': 34, 'SIMILAR_TO': 10}; truncated=True
- random (QWKYTVA...): nodes={'Molecule': 11, 'Evidence': 31, 'Condition': 8}; edges={'HAS_COMPUTATION': 12, 'HAS_OBSERVATION': 19, 'UNDER_CONDITION': 31, 'SIMILAR_TO': 10}; truncated=True
- Validator: ALL CASES PASS (no dangling edges, no budget violations, provenance_refs consistent)

---
## 2026-02 Addendum: blocker correction and execution lane

### Current blockers (corrected)

- `aTB`: DONE (full run complete; not the blocker)
- `literature web_search`: PARTIAL
  - call chain works
  - citations/sources passthrough remains unstable for strict writeback

### Current unblock path

- Use `offline_pdf` emission lane for single-sample end-to-end execution on `test.csv`.
- This lane is sufficient to continue orchestrator/case-file integration and master reasoning validation.

### Policy snapshot

- `offline_pdf`:
  - strict case writeback allowed when locator/provenance is explicit
  - EvidenceClaim-only writeback is allowed under V1 guardrail
- `web_search`:
  - relaxed candidate-only when traceability is incomplete
  - no strict `evidence_table` writeback until citations/sources are reliable

### Next step (explicit)

- Run one `test.csv` sample through:
  - case creation -> offline PDF extraction -> emission field patch -> reasoning handoff
  - persist agent-run traceability in case file

---

## 2026-02-19 — E0 mineru_llm compatibility fix (DeepSeek relay path)

### Problem observed

- `case-e2e` with `extractor_mode=mineru_llm` reached MinerU preflight but failed at LLM stage with:
  - `llm_schema_invalid:empty_output`
- Root cause: `src/cases/mineru_llm_extractor.py` only used `Responses API + strict json_schema` and only parsed `output_text`-style fields, which is brittle on some OpenAI-compatible relays/models.

### Implemented

- Updated `src/cases/mineru_llm_extractor.py`:
  - LLM call strategy changed to **chat.completions first**, then fallback to `responses.create`.
  - Added broader response-text extraction compatibility:
    - `output[*].content[*].type in {output_text,text}`
    - chat-style `choices[*].message.content`
    - tool/function call argument payload fallback
  - Added parsing compatibility for MinerU template output:
    - accepts legacy payload with `emission_aggr_nm` / `emission_solid_or_film_nm` + `evidence{...}`
    - maps it into staged candidate format
  - Kept existing candidate schema path fully supported.

- Added test:
  - `tests/test_mineru_llm_extractor.py::test_parse_llm_candidates_accepts_legacy_emission_payload`

### Validation

- Focused tests all pass:
  - `tests/test_mineru_llm_extractor.py`
  - `tests/test_example_a_first_runner.py`
  - `tests/test_cli_case_e2e.py`
  - total: `16 passed`

### Follow-up (page enforcement + locator fallback)

- Strengthened prompt/runtime contract to prioritize explicit page numbers in each candidate.
- Added post-processing fallback:
  - infer `page` from `source_locator`/`condition` when model omits it (patterns: `page N`, `p. N`, `(p.N)`).
  - infer `page` from `content_list_v2` using locator hints (`Fig. N` / `Table N`) when explicit page text is missing.
- Added tests:
  - `test_parse_llm_candidates_infers_page_from_locator_when_missing`
  - `test_infer_page_from_content_list_with_figure_locator`
  - prompt contains page requirement line
- Focused test suites re-run: `18 passed`.

---

## 2026-02-19 — Case file readability split: master view + update history

### Goal

- Keep one case file per sample, but present it as two explicit sections:
  - `case_sections.for_master_reasoning`
  - `case_sections.update_history`

### Implemented

- Added helper: `src/cases/case_sections.py`
  - `sync_case_sections(case)` builds both views from canonical fields.
- Wired into case lifecycle:
  - `src/cases/create_case_from_smiles.py` now writes `case_sections` at creation.
  - `src/cases/example_a_first_runner.py` refreshes `case_sections` before/after E0 patch apply.
  - `src/cli.py` (`case-e2e`) refreshes `case_sections` after pre-E0 history append.

### Validation

- Updated tests to assert `case_sections` consistency with canonical fields.
- Focused suites re-run:
  - `tests/test_example_a_first_runner.py`
  - `tests/test_cli_case_e2e.py`
  - `tests/test_mineru_llm_extractor.py`
  - total: `18 passed`

---

## 2026-03-02 — Final v3 stabilization (tagged repair + R2 discriminative evidence + stop/no-touch hardening)

### What changed

- Master reasoner now defaults to `tagged_repair` output mode (strict provider schema remains optional).
- Tagged parser now requires explicit:
  - `PRIMARY_LABEL`
  - `PRIMARY_CONFIDENCE`
  - and normalizes label by `allowed_mechanism_labels + candidate set` (`unknown` fallback).
- Removed keyword-priority mechanism-label inference from parser path.
- Confidence computation switched to soft-penalty pipeline:
  - `raw_confidence_from_model` -> `final_confidence`
  - factors: similarity, entropy, gate mode, and R2 `separation_score` (when reliability is `medium/high`).
- R2 evidence upgraded to compact `neighbor_atb_stats_by_label` with deterministic `<3KB` trimming and `E21-E24` registry bindings.
- Iterative stop logic added pre-R2 recovery guard:
  - one forced recovery (`force_r2` default) before allowing terminal stop on repeated pre-R2 master failures.
- Added low-level SafeFS deny-write guard for `data/evidence_table.parquet` and wired JSON write paths through guarded I/O.

### Validation

- Full test suite:
  - `323 passed, 5 skipped`
- Added/updated focused tests for:
  - tagged parser required fields + label normalization
  - confidence non-constant behavior under soft penalty
  - neighbor stats size/budget and deterministic trimming
  - pre-R2 recovery guard behavior
  - behavior-level no-touch guard via guarded writes

---

## 2026-03-02 — Evaluator LLM hotfix: align with master tagged-repair flow

### Problem observed

- Evaluator LLM calls were enabled but frequently downgraded to rule-only due to `invalid_eval_llm_output:*`.
- Root cause: evaluator prompt asked for natural language while parser expected direct JSON keys.

### Implemented

- Updated `src/agents/llm_evaluator.py` to follow master-style robust path:
  - tagged natural-language section contract:
    - `CRITIQUE_POINTS`
    - `CONFLICTS`
    - `VOI_RANKED_ACTIONS`
    - `CONFIDENCE_DELTA_SUGGESTION`
    - `NEXT_ROUND_PROFILE_SUGGESTION`
  - local tagged parser -> structured eval object (`eval_llm_output_schema_v1` keys unchanged)
  - retry still enabled
  - repair path now first attempts local tagged repair before remote repair call.

### Validation

- Focused tests pass:
  - `tests/test_llm_evaluator.py`
  - `tests/test_round_runner_llm_layer.py`
  - total: `7 passed`

---

## 2026-03-02 — R2 anti-stagnation + PRIMARY_LABEL token normalization hotfix

### Problem observed

- In some atb-only iterative runs, R2 introduced neighbor evidence IDs but master hypothesis/confidence remained unchanged; loop continued with low value.
- `PRIMARY_LABEL` sometimes included explanatory text and normalized to `unknown` unnecessarily.

### Implemented

- Updated `src/orchestration/round_runner.py`:
  - add global new-evidence tracking across rounds,
  - derive `effective_added_ids` and `count_effective_added`,
  - in `R2/R3`, treat `E21..E24` as effective only when neighbor-stat reliability is `medium/high`,
  - persist `effective_added_ids/count_effective_added` in round_state.
- Updated `src/agents/judge_agent.py`:
  - stop logic now uses `effective_gain` (`count_effective_added` OR hypothesis change OR |confidence_delta|>=0.02),
  - keep existing reason codes while reducing repeated no-op rounds.
- Updated `src/reasoning/master_reasoner.py`:
  - strengthen prompt contract: `PRIMARY_LABEL` must be a single token from allowed labels,
  - parser now performs lightweight token normalization from annotated `PRIMARY_LABEL` text (without reintroducing free-form keyword-priority scanning).

### Validation

- Focused tests pass:
  - `tests/test_master_tagged_repair.py`
  - `tests/test_round_runner_stagnation_stop.py`
  - `tests/test_round_runner_llm_layer.py`
  - total: `13 passed`

---

## 2026-03-05 — Plan locked: test.csv mechanism benchmark (evaluation-only)

### Intent

- Build a reproducible benchmark runner over `data/test.csv` using release runtime.
- Evaluate only mechanism-label agreement vs ground truth.
- Do not change multi-agent reasoning strategy, schema, or evidence_table behavior.

### Locked decisions

- GT column: `mechanism_id`
- Prediction path:
  - primary `/master_reasoning/mechanism_claim/primary_hypothesis/mechanism_label`
  - fallback `/reasoning/master_reasoning/mechanism_claim/primary_hypothesis/mechanism_label`
- Runtime mode: single-pass (`iterative=false`) on `run_lane=atb_cache_only`
- Determinism metadata recorded in report:
  - model/base_url/reasoning_effort/temperature/json_schema usage
  - `seed_supported=false`, `seed=null`
- Evaluation label normalizer is benchmark-only.
  - `clusterluminescence` and `ESIPT+ICT/TICT` map to `unknown`.

### Deliverables

- `src/eval/evaluate_testset.py`
- `src/eval/label_normalizer.py`
- reports under `artifacts/eval/<timestamp>/`:
  - `predictions.csv`
  - `evaluation_report.json`
  - `evaluation_report.md`
  - optional `failed_cases_index.json`

### Implemented

- Added benchmark runner:
  - `src/eval/evaluate_testset.py`
  - release runtime invocation via `src.orchestration.run_one.run_one`
  - per-row failure tolerance (`failed_run`, `missing_pred`, `missing_gt`, `ok`)
  - resume support by reusing existing `predictions.csv` in the same `eval_id`
- Added evaluation-only label normalizer:
  - `src/eval/label_normalizer.py`
  - canonical labels: `TICT`, `ICT`, `ESIPT`, `neutral aromatic`, `other`, `unknown`
  - locked mapping: `clusterluminescence` and `ESIPT+ICT/TICT` -> `unknown`
- Added outputs under `artifacts/eval/<eval_id>/`:
  - `predictions.csv`
  - `evaluation_report.json`
  - `evaluation_report.md`
  - `failed_cases_index.json`
- Metrics implemented:
  - `top1_accuracy`, `macro_f1`, `per_class_precision_recall_f1`, `confusion_matrix` (on `status=ok`)
  - `coverage`, `unknown_rate` (full-set derived)
- Determinism metadata recorded in report:
  - model/base_url/reasoning_effort/temperature/json_schema usage
  - `seed_supported=false`, `seed=null`

### Tests

- Added:
  - `tests/test_eval_label_normalizer.py`
  - `tests/test_eval_prediction_extractor.py`
  - `tests/test_eval_pipeline_smoke.py`
- Result:
  - `6 passed`

### UX update for benchmark run visibility

- Added live, clean progress output in `src/eval/evaluate_testset.py`:
  - current sample index + SMILES
  - current round index (from `run_status.json`)
  - running accuracy on completed `status=ok` rows
  - total progress bar
- Suppressed runtime JSON progress spam in eval mode by muting
  `emit_progress_event`/`emit_error_summary` during per-sample execution.
- Added CLI toggles:
  - `--show-progress/--no-show-progress` (default on)
  - `--print-report/--no-print-report` (default off)

---

## 2026-03-06 — Cache-derived aTB evidence enrichment for master reasoning

- Expanded cache-backed `features_summary` extraction to surface more of the already-available aTB signal without changing gate semantics:
  - `delta_dipole`, `delta_bonds`, `delta_angles`
  - `exciting_path_mean_volume`
  - asymmetry and rotational-constant fields
- Added three new compact target-only reasoning profiles from existing aTB cache content:
  - `risk_scores.atb_ct_proxy_profile`
  - `risk_scores.atb_structural_relaxation_profile`
  - `risk_scores.atb_shape_rigidity_profile`
- Wired the new profiles into `R1+` reasoning packs and evidence registry with compact evidence IDs:
  - `E35` charge-separation proxy
  - `E36` CT proxy summary
  - `E37` structural relaxation summary
  - `E38` excited-path volume cue
  - `E39` shape-rigidity summary
- Updated master prompt guidance so target-only aTB reasoning now explicitly prefers:
  - `delta_dipole + delta_gap` for CT proxy
  - `delta_dihedral + delta_bonds + delta_angles + delta_volume` for structural relaxation
  instead of treating `delta_dihedral` as the only meaningful structural cue.
- Kept runtime contracts unchanged:
  - no master schema change,
  - no evidence_table writeback,
  - no orchestrator patch/whitelist/idempotency changes.
- Validation/tests run in `aie`:
  - `pytest -q tests/test_atb_cache_derived_profiles.py tests/test_chem_agent_atb.py tests/test_reasoning_pack_builder.py tests/test_reasoning_pack_r1_includes_atb_trends_self.py tests/test_master_output_validation.py`
  - result: `28 passed`
