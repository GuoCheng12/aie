# V1 Detailed Plan (CURRENT)

## Goal
Build the Evidence Layer + Light KG + Chem Agent literature evidence loop, while keeping SMILES-first case file as the shared artifact.

## Current execution mode (multi-agent refactor, authoritative)

This repo now treats the production path as a **multi-agent orchestrator loop** (not CLI-first glue code):

1. `DataAgent.run(case)` -> patch
2. `ChemAgent.run(case)` -> patch
3. `ReadyAgent.run(case)` -> patch (gate owner)
4. if gate ready: `ReasoningAgent.run(case)` -> patch/output
5. `JudgeAgent.run(case)` -> patch
6. `ReadyAgent.run(case)` again -> final gate/action reconciliation

All writes must be RFC6902 patch writes with:
- per-agent path whitelist
- append-only path enforcement
- per-step idempotency key
- replay artifacts (input snapshot, raw outputs, patch, case before/after/diff, manifest)

### Final v3 lock (this cycle)

This cycle hardens master iterative reasoning behavior and removes known convergence artifacts.

1) **Master label parsing is explicit**
- In `tagged_repair` mode, model output must include explicit `PRIMARY_LABEL` and `PRIMARY_CONFIDENCE`.
- Master no longer infers mechanism label from natural-language keyword scan order.
- Label acceptance is controlled by `reasoning_config.allowed_mechanism_labels` plus candidate-set labels from pack; unknown labels are normalized to `unknown` with warning.

2) **Confidence is soft-penalty (not hard 0.42 clipping)**
- `PRIMARY_CONFIDENCE` is stored as raw model confidence (`raw_confidence_from_model`).
- Final written confidence is computed by soft penalty factors (similarity, entropy, gate mode, and R2 separation strength when reliable), then capped by conservative cap only.
- `master_reasoning_meta` must record:
  - `raw_confidence_from_model`
  - `final_confidence`
  - `penalty_components`
  - `confidence_formula_version`

3) **R2 introduces discriminative neighbor aTB evidence**
- Add `risk_scores.neighbor_atb_stats_by_label` in profile `R2+`.
- Source rows: neighbors with `cache_status=="success"` and complete delta fields only.
- Output is compact and deterministic, including percentile/z and by-label summaries.
- Evidence registry adds compact derived entries:
  - `E21`: dihedral distribution summary
  - `E22`: delta-gap distribution summary
  - `E23`: by-label comparative summary (conditional)
  - `E24`: reliability/sample size note

4) **Deterministic size control for neighbor stats**
- Enforce strict trim order to keep object `<3KB`:
  1. by-label top-3
  2. by-label top-2
  3. summary max 3 lines
  4. keep only `delta_dihedral` + `delta_gap`
  5. minimal by-label payload (`n`, `median`) while preserving reliability/sample size/separation score

5) **Iterative stop policy guard**
- Stagnation stop remains active (`count_added==0` + repeated profile).
- Additional guard: if profile is pre-R2 and master fails consecutively, do not stop immediately; allow one recovery opportunity (`force_r2` default, optional `degraded_retry`) before terminal stop.

6) **No-touch evidence table, strengthened**
- Keep existing content-hash no-touch check.
- Add behavior-level write guard at core write entrypoints so iterative/round runner code cannot bypass and write `data/evidence_table.parquet`.

### Hotfix plan (current task): evaluator aligned to master tagged_repair

Problem observed in recent runs: evaluator LLM was enabled but frequently downgraded to rule-only because output was natural-language text while parser expected JSON keys directly, producing `invalid_eval_llm_output:*`.

Plan for this patch:
- make evaluator use the same robust flow as master:
  - tagged natural-language contract first,
  - local tagged parser to structured eval object,
  - optional JSON extraction/repair fallback if needed.
- keep evaluator output schema contract unchanged (`eval_llm_output_schema_v1` keys), but parse from tagged sections:
  - `CRITIQUE_POINTS`, `CONFLICTS`, `VOI_RANKED_ACTIONS`, `CONFIDENCE_DELTA_SUGGESTION`, `NEXT_ROUND_PROFILE_SUGGESTION`.
- preserve orchestrator behavior (rule evaluator remains base; LLM layer only augments ranking/notes).

### Hotfix plan (current task): DeepSeek runtime compatibility in multi-agent LLM client

Problem observed in recent multi-agent runs:
- `deepseek-v3.2` works in the MinerU extractor path, but fails in the shared multi-agent runtime.
- Root cause: the shared runtime client only uses `client.responses.create(...)`, while the gateway rejects `deepseek-v3.2` on the Responses API and returns empty output after retries.

Plan for this patch:
- update the shared LLM client (`src/tools/llm_client.py`) to support `chat.completions` fallback, matching the proven MinerU extractor strategy.
- preserve existing behavior for GPT/Responses-compatible models; only fall back when Responses fails or when the model is known to prefer chat completions.
- apply the fix centrally so both Master and Evaluator benefit without orchestrator changes.
- add focused tests covering:
  - Responses unsupported-model error -> chat.completions fallback succeeds
  - text mode fallback returns natural-language payload
  - JSON mode fallback returns parsed JSON payload

### Hotfix plan (current task): R2 anti-stagnation + label-token robustness

Problem observed from recent iterative runs:
- R2 introduces new IDs (`E21/E22/E24`) but hypothesis/confidence may remain unchanged.
- stop policy currently keys on raw `count_added` and can miss “no effective gain” scenarios.
- `PRIMARY_LABEL` may include explanatory text and normalize to `unknown` too often.

Plan:
- add `effective_gain` signal in round runner + judge:
  - derive `count_effective_added` from globally new evidence IDs,
  - in `R2/R3`, treat `E21..E24` as effective only when neighbor-stat reliability is `medium/high`,
  - stop when profile repeats with no effective gain.
- strengthen PRIMARY_LABEL parsing:
  - require token-style label in prompt,
  - parser supports lightweight token normalization from annotated label text (without reverting to keyword-priority scanning).

### Release-hard constraints (promotion from E0 semantics)

The following are platform rules for the release path (not example-only behavior):

1) **Patch semantics are mandatory in orchestrator**
- RFC6902 only (`add|replace|test`)
- validate before apply
- per-agent whitelist + append-only enforcement

2) **Idempotency key must include run lane/config**
- `case_id + agent_name + agent_version + inputs_hash + run_config_hash`

3) **Replay contract is step-level and mandatory**
- Each `artifacts/<run_id>/<step_idx>_<agent>/` must include:
  - `00_input_snapshot.json`
  - `01_raw_outputs.json`
  - `03_patch.json`
  - `04_case_before.json`
  - `05_case_after.json`
  - `06_case_diff.json`
  - `manifest.json` (sha256 for all step files)

4) **Structured skipped reason codes**
- When `status=skipped`, `status_reason_code` is required in `agent_runs[]`.
- Standard codes: `idempotency_hit`, `gate_blocked_reasoning`, `lane_disabled`,
  `missing_required_input`, `upstream_failed`, `not_applicable`.

5) **Gate ownership is enforced globally**
- Only `ready_agent` may write:
  - `/current_gate/*`
  - `/action_rationale`
  - `/action_plan`
- Non-owner writes must fail fast in orchestrator validation.

6) **evidence_table no-touch uses behavior + content safeguards**
- No orchestrator write path to evidence_table.
- Integration checks verify `data/evidence_table.parquet` content hash unchanged (or file remains absent).

### Agent write permissions (hard policy)

- **Data Agent**
  - allowed: `query.*`, `neighbors[]`, structural `risk_scores.*`, `agent_runs[]`
  - forbidden: `current_gate`, `action_rationale`, `action_plan`, `target_fields*`
- **Chem Agent**
  - allowed: `evidence_readiness.atb.*`, `evidence_readiness.literature.*`, `evidence_readiness.experiment.*`,
    `target_fields*`, `target_fields_provenance*`, `evidence_candidates_staging[]`,
    `risk_scores.atb_neighbor_consistency`, `agent_runs[]`
  - responsibility: compute `risk_scores.atb_neighbor_consistency` from target+neighbor successful aTB deltas only
    (ignore failed/missing neighbors; retrieval remains ECFP-only)
  - forbidden: `current_gate`, `action_rationale`
- **Ready Agent (gate owner)**
  - allowed: `current_gate.*`, `action_rationale`, `action_plan`, optional `risk_scores.readiness_*`, `agent_runs[]`
  - forbidden: `target_fields*`, `evidence_candidates_staging[]`, `evidence_readiness.*`
- **Reasoning Agent**
  - allowed: `master_reasoning`, `master_reasoning_meta`, `master_reasoning_status`,
    `master_reasoning_used_evidence_paths`, `agent_runs[]`
  - forbidden: `current_gate`, `action_rationale`, `target_fields*`
- **Judge Agent**
  - allowed: `post_uq.*`, `agent_runs[]`
  - forbidden: `current_gate`, `action_rationale`, `action_plan` (only Ready Agent may write gate/plan)

### Official runtime entrypoint (release)

- `python -m src.cli case-run` is the official release command.
- Default lane: `--run-lane atb_cache_only` (skip unfinished literature/wet-lab branches).
- Output default: final case + run summary; optional `--emit-stage-snapshots`.
- `case-e0`, `case-e2e`, `case-e2e-atb` remain one-version compatibility aliases and must forward to `case-run`.

### Output layout refactor (current lock)

This cycle upgrades runtime outputs from scattered run-id roots to a case-centric layout.

- Primary layout (`--output-layout case_centric`, default):
  - `<artifacts_dir>/cases/<case_id>/latest/`
  - `<artifacts_dir>/cases/<case_id>/runs/<YYYYMMDDTHHMMSSZ>__<run_id8>/`
  - `<artifacts_dir>/cases/<case_id>/history_index.json`
  - `<artifacts_dir>/cases/<case_id>/latest.json`
- `run_id` stays as audit key, but human navigation is now case-first.
- `latest/quick_view.json` is the one-screen summary for daily inspection.
- Retention policy: keep latest N runs per case (`--retain-runs`, default `10`), prune older `runs/*` entries and refresh history index atomically.
- Compatibility (one version): optional legacy pointer writes remain enabled by default (`--write-legacy-run-view true`).

New CLI knobs:

- `--output-layout {case_centric,run_centric}`
- `--retain-runs <int>`
- `--output-timestamp-format utc_compact`
- `--write-legacy-run-view / --no-write-legacy-run-view`

### Planned runtime default update (2026-02-24)

- Add a dedicated LLM trace output root for release runtime:
  - new runtime option `--llm-response-dir` (default `artifacts/llm_responses`)
  - all LLM agents write run-scoped files under `artifacts/llm_responses/<run_id>/`.
  - naming convention: `<run_id>.<agent_name>.response.json`.
  - ReasoningAgent additionally writes `<run_id>.reasoning_agent.summary5.json` (five-signal extract for quick reading).
- Update release defaults for master reasoning execution:
  - default model: `gpt-5.2`
  - default `llm_reasoning_effort`: `medium`
  - default `llm_temperature`: `0.2`
  - keep explicit override flags unchanged (`--model`, `--llm-reasoning-effort`, `--llm-temperature`).
- Add LLM response parser fallback for strict JSON mode:
  - if gateway returns structured payload without `output_text`, parse JSON from message content (`parsed/json/object`) before failing.
- Add reasoning effort downgrade retry for runtime stability:
  - when initial effort is `xhigh`, retry sequence is `xhigh -> high -> medium -> low -> none -> (no reasoning field)` on empty/invalid content.
  - objective is to preserve default high-quality setting while avoiding hard failure on gateway/model output sparsity.

### Master/Evaluator JSON-only contract mode (current default)

- Runtime default is now **no provider-side json_schema** (`llm_use_json_schema=false`).
- Both master and evaluator prompts append a JSON-only contract:
  - output must be a single JSON object (`{...}`),
  - no markdown/explanation/prefix/suffix,
  - required top-level keys must be present,
  - bounded list sizes and bounded note length.
- Validation stays code-side:
  - `json.loads` parse,
  - lightweight required/type/enum checks,
  - evidence resolution checks (registry membership + value existence),
  - semantic policy checks (confidence caps, chain ordering, anti-leakage).
- Failure recovery is three-stage:
  1) first call,
  2) retry once with larger token budget and stronger “JSON-only” reminder,
3) JSON-repair pass (no new facts/claims), then normal validator.

### Current task: cache-derived aTB evidence enrichment for master reasoning

Objective for this cycle:
- improve mechanism discrimination using only the current aTB cache, without adding new quantum workflows,
- strengthen positive structured evidence before designing any new external computation lanes,
- keep orchestrator semantics unchanged (patch/whitelist/idempotency/replay/no-touch evidence_table).

Scope locked for this batch:
1. Expand `evidence_readiness.atb.features_summary` with additional cache-derived fields that already exist in `features.json`:
   - `s0_charge_dipole`, `s1_charge_dipole`, `delta_dipole`
   - `delta_bonds`, `delta_angles`
   - `exciting_path_mean_volume`
   - `s0_rays_asymmetry_parameter`, `s1_rays_asymmetry_parameter`
   - `s0_rotational_constant_a/b/c`, `s1_rotational_constant_a/b/c`
2. Add compact derived reasoning profiles (pack-only, no new business writeback):
   - `risk_scores.atb_ct_proxy_profile`
   - `risk_scores.atb_structural_relaxation_profile`
   - `risk_scores.atb_shape_rigidity_profile`
3. Inject the new profiles into `R1+` reasoning packs and compact evidence registry entries.
4. Update master prompt guidance so aTB interpretation uses:
   - CT proxy evidence (`delta_dipole` + `delta_gap`)
   - structural relaxation evidence (`delta_dihedral` + `delta_bonds` + `delta_angles` + `delta_volume`)
   instead of relying on `delta_dihedral` alone.

Implementation plan:
- `src/chem/atb_cache.py`
  - widen cache summary extraction to include the stable additional fields listed above.
- `src/reasoning/atb_ct_proxy_profile.py`
  - new compact target-only CT proxy profile from existing aTB summary.
- `src/reasoning/atb_structural_relaxation_profile.py`
  - new compact target-only structural relaxation profile.
- `src/reasoning/atb_shape_rigidity_profile.py`
  - new compact target-only rigidity/shape proxy profile.
- `src/reasoning/master_reasoner.py`
  - add these profiles to `build_reasoning_pack()`,
  - add new evidence IDs with short previews,
  - update prompt wording to explicitly use the new profiles.
- `src/reasoning/evidence_profiles.py`
  - ensure `R1+` includes the new cache-derived profiles.
- tests
  - add focused unit coverage for each new profile and for pack/evidence-registry integration.

Validation target for this batch:
- a single-SMILES `case-run` should complete with enriched aTB-derived evidence visible in the reasoning pack / evidence registry,
- no orchestrator contract regressions,
- no evidence_table writes.

### Offline PDF / web_search switch

- `evidence_acquire.emission.mode`:
  - `offline_pdf` (current default and milestone lane)
  - `web_search` (slot; non-blocking for this refactor)
- `offline_pdf` path:
  - MinerU parse -> LLM structured extraction -> candidate staging -> target field writeback to case (policy-gated)
- `web_search` path:
  - candidate-only unless strict citations/provenance are stable

### Guardrails locked for this refactor

- Ready Agent is the only writer for `current_gate` and `action_rationale`.
- Evidence table is **read-only** in this refactor (no writeback).
- Every agent step must append an `agent_runs[]` audit row with `inputs_hash` + `idempotency_key`.
- Replay artifacts are mandatory per run/step for deterministic audit.
- Master reasoner consumes `reasoning_pack` only (not full case payload in prompt), and all evidence references must resolve via `evidence_registry` to real case paths.

### Master Reasoner runtime contract (release)

- **Input**: `case.json` + `reasoning_config`
- **Internal projection**: build `reasoning_pack` (`master_pack_v1`) as the only model-facing context
  - include target aTB `features_summary` baseline by profile (`R0/R1`) and keep neighbor evidence token-friendly.
  - `R1` must add `risk_scores.atb_trend_profile` (self-only target aTB trend profile) and may keep `atb_trends_self` for compatibility.
  - for `R2+`, include compact `neighbor_atb_stats` (distribution summaries), not full neighbor feature payloads.
  - round increment contract (locked):
    - `R0`: gate + minimal priors only.
    - `R1`: `R0 + atb_trend_profile` (self-only trend buckets/directions from target aTB).
    - `R2`: `R1 + neighbor_atb_stats_by_label` (if available in lane).
    - `R3`: `R2 + external lane statuses`.
- **Evidence weighting policy** (locked):
  - neighbors are **prior/context** by default;
  - aTB features are **support evidence**;
  - neighbor evidence may use `role="support"` only when `top1_sim >= 0.55` (otherwise neighbors must stay context/counter only).
- **aTB discriminative rubric** (locked):
  - `abs(delta_dihedral) < 8.0` -> `atb_support_level="none"`
  - `8.0 <= abs(delta_dihedral) < 15.0` -> `atb_support_level="weak"`
  - `abs(delta_dihedral) >= 15.0` -> `atb_support_level="strong"`
  - `delta_gap` can only provide weak CT-family context (ICT/TICT), never strong evidence.
  - ESIPT cannot be strongly supported in `atb_cache_only` lane without motif/literature/experiment evidence.
- **Mechanistic chain shape**:
  - `supporting_chain` must be exactly four ordered steps `A -> B -> C -> D`
  - A: excited-state structural access (aTB features)
  - B: hypothesized nonradiative channel
  - C: aggregation/rigidification effect (RIM-style suppression)
  - D: discriminative predictions to separate ICT/TICT/ESIPT
- **Prompt bundle**: `master_prompt_bundle_v1` with `{system, instructions, user_payload, schema}`
  - model-facing `user_payload` uses compact `evidence_registry` (`E1..En`) for citations.
  - master must cite `evidence_id` only; validator resolves IDs to canonical case paths server-side.
  - for aTB argumentation, master must prioritize self-only trend evidence IDs (`E31..E34`, plus legacy `E_ATB_TREND_*` when present) over raw absolute-value narration.
- **R2 discriminative increment (locked)**:
  - `R1` introduces self-trend evidence (`atb_trend_profile`) and keeps it independent from neighbor distributions.
  - `neighbor_atb_stats` is computed from neighbor `features_summary` where `cache_status=="success"` and required deltas are present.
  - stats include only compact signals (`median`, `IQR`, `percentile`, `robust-z`, reliability), no full distributions.
  - `delta_dihedral` is emitted as both raw and absolute views:
    - `delta_dihedral` (signed audit value),
    - `abs_delta_dihedral` (primary discriminative axis for percentile/z/by-label comparison).
  - evidence registry adds stable derived entries in `R2+`:
    - `E21` (`abs_delta_dihedral` distribution summary),
    - `E22` (`delta_gap` distribution summary),
    - `E23` (label-stratified comparison, only when >=2 labels and each label `n>=2`),
    - `E24` (reliability note).
  - evidence registry adds stable self-trend entries in `R1+`:
    - `E31` (`delta_dihedral` bucket + direction from self-only trend profile),
    - `E32` (`delta_gap` bucket + direction),
    - `E33` (`delta_volume` bucket + direction),
    - `E34` (`overall_motion_proxy` + reliability from self-only trend profile),
  - legacy self-trend IDs remain supported for compatibility:
    - `E_ATB_TREND_1` (`delta_dihedral` bucket + absolute magnitude),
    - `E_ATB_TREND_2` (`delta_gap` direction + bucket),
    - `E_ATB_TREND_3` (`delta_volume` direction + bucket),
    - `E_ATB_TREND_4` (`overall_motion_proxy` + reliability).
  - `E21/E22` `value_preview` is fixed minimal object:
    `{target, neighbors_median, neighbors_iqr, target_percentile, z_robust}`.
  - `summary[]` remains in `risk_scores.neighbor_atb_stats` only (never duplicated in `evidence_registry`).
- **Output**: strict JSON + semantic validation (evidence path allowlist/existence, chain order, evidence weighting, confidence caps)
- **Writeback**: RFC6902 patch to `master_reasoning*` root keys only
- **Execution condition**:
  - gate is ready (`ready_for_reasoning=true` or ready state),
  - `action_plan` contains `run_master_reasoner` (position-independent by default; top1 check optional via config)
- **Schema strictness note**:
  - when using Responses strict `json_schema`, root `required` must include all keys in root `properties` (including `recommended_next_actions`) to satisfy provider-side schema validation.
  - same strict rule also applies to nested objects (`additionalProperties=false`): every property key must appear in `required` (e.g., `supporting_chain[].step_name` in schema v2).
- **Additional confidence caps**:
  - if `top1_sim < 0.50` -> `confidence <= 0.45`
  - if `mechanism_entropy > 0.75` -> `confidence <= 0.50`
  - if both hold -> `confidence <= 0.42`

### Derived evidence validation strategy (locked)

- `evidence_registry` rows carry source semantics:
  - `source_type="case"` with `case_path`,
  - `source_type="derived_pack"` with `pack_path` (+ optional `derived_from_case_paths`).
- Validator behavior:
  - `source_type=case`: resolve `case_path` in case JSON and require non-empty value.
  - `source_type=derived_pack`: resolve `pack_path` in `reasoning_pack` and require non-empty value.
- Derived evidence (e.g., `E21+`) is **not** required to map to a concrete case JSON path to pass validation.

### In-flight hardening plan (reasoning audit-safety + token efficiency)
- Prompt hard rule: master must not invent numeric bands/thresholds; numeric bounds can only come from `reasoning_config.thresholds` (or explicitly surfaced evidence values).
- Compact evidence trace stays evidence-id only (`E1..En`) for model output; no `case_path` in model JSON.
- `reasoning_pack.evidence_registry` stays capped (target 10-20 rows), sorted by importance, and includes `value_preview` + `role_hint` + `note_hint` to reduce model drift.
- Schema upgrade to `master_output_schema_v3`: keep strict JSON, require `supporting_chain[].step_name`, and lock step-name enum for deterministic chain semantics.
- Validation hardening:
  - reject unknown `evidence_id`;
  - reject citations resolving to missing/null/empty case values;
  - reject invented thresholds/range text unless numeric values are present in configured thresholds;
  - in conservative mode, require limits to mention missing literature/experiment when lane is disabled.
- Keep orchestrator contract unchanged: RFC6902 patch path guards, replay artifacts, idempotency, and evidence_table no-touch.
- Prompt payload trim (locked):
  - remove neighbor full-feature payload from model-facing prompt,
  - keep compact neighbor summaries and `neighbor_atb_stats` only.
- Summary quality upgrade (next patch):
  - expand `five_signals.evidence_chain` to 8-12 evidence-id items with fixed block ordering (uncertainty bounds -> aTB cues -> missing discriminators),
  - expand `five_signals.conclusion.natural_language_mechanism` to a 3-paragraph narrative (best hypothesis, mixture boundary, falsifiable next tests),
  - keep citations evidence-id only and avoid threshold/range wording unless explicit `reasoning_config.thresholds` key/value is cited.
- Mechanism-agnostic prompt refactor (next patch):
  - remove hard-coded mechanism labels from master prompt instructions and trace narratives,
  - inject dynamic candidate-set text from `reasoning_pack.mechanism_context.candidate_mechanisms_top3`,
  - fallback to generic hypothesis wording when candidate priors are absent.
- Plan B (hint isolation):
  - keep `risk_scores.mechanism_hint` / `risk_scores.hint_confidence` in case file for routing/debug,
  - exclude both fields from `reasoning_pack` (master input),
  - forbid master evidence references to these paths during validation.

### Planned implementation (iterative closure v2.3, pre-code lock)
- Add `RoundRunner` outer loop (`Round0 -> RoundN`) while keeping orchestrator core unchanged.
- Add two runner modes:
  - `dryrun_then_commit` (default): R0 writes only minimal `/iterative/*` state; R1+ may write reasoning/eval outputs.
  - `commit_all_rounds`: all rounds may write outputs.
- Add config-based `EvidenceProfileSelector` (`R0..R3`) and make R0/R1 include target aTB summary as baseline input.
- Extend evaluator sidecar output with feasibility:
  - `feasibility` (lane capabilities, constraints, overall_score)
  - `voi_ranked_actions[*]` with `feasible`, `feasibility_score`, `blocked_by`, `unblock_actions`.
- Keep `master_output_schema_version=v3` unchanged; new iteration fields are sidecar + minimal `/iterative/*` case state.

### Iterative stop/next policy hardening (v2.4)

- Add explicit information-gain handling in evaluator:
  - consume `count_added` and `hypothesis_changed` signals from round runner.
- Stop policy additions:
  - if `count_added==0` and next profile equals current profile -> stop with `reason_code=stagnation_no_new_evidence`.
  - if `count_added==0` and profile changed but lane has no higher usable evidence layer -> stop with `reason_code=no_new_evidence_available_in_lane`.
  - in `atb_cache_only`, if `R1 -> R2` adds no new evidence IDs, force `next_round_profile=NONE` and stop immediately.
- `atb_cache_only` action ranking adjustment:
  - top actions should become lane-unblocking actions (`switch_run_lane_offline_pdf`, `provide_offline_pdf`),
  - avoid repeatedly returning `request_manual_pdf` as top-1 when lane is disabled.

### Planned implementation (runtime progress feedback, pre-code lock)
- Add non-business telemetry in `RoundRunner` and `run_one`:
  - stdout structured events for `round_start`, `master_call`, `validate`, `judge`, `apply_patch`, `stop`, `round_end`.
  - every event includes `round_index`, `max_rounds`, `active_profile`, `stage`, `status`, `elapsed_ms`.
- Add atomic run status file at artifacts root:
  - `<artifacts_dir>/run_status.json` (single latest snapshot, atomically overwritten).
  - required fields: `run_id`, `case_id`, `round_index`, `max_rounds`, `active_profile`,
    `round_runner_mode`, `stage`, `last_event`, `last_updated_at`, `errors`, `round_dir`, `latest_eval_report`.
- Add failure summary propagation:
  - for `failed_llm` / `failed_schema_validation`, emit concise `error_codes` + `error_paths` to stdout
  - mirror same summary into `run_status.json.errors`.
- Add status tail tool:
  - `python -m src.orchestration.tail_status --artifacts-dir ...`
  - polling interval 0.5s, print latest `run_status.json`, Ctrl+C exits cleanly.
- Add minimal tests:
  - R0 writes `run_status.json` at least once,
  - 2-round run updates `round_index` to 1,
  - failure path fills non-empty `errors`.

### Planned implementation (optional evaluator LLM layer, pre-code lock)
- Keep deterministic Judge/Rule evaluator as source of truth for:
  - feasibility,
  - stop policy,
  - baseline scorecard/action list.
- Add optional `LLMEvaluator` sidecar layer, enabled only when:
  - `reasoning_config.evaluator.use_llm=true`.
- LLM evaluator input must stay compact:
  - `reasoning_pack`,
  - `master_output_parsed`,
  - `reasoning_config.policy + reasoning_config.thresholds`,
  - `run_lane_capabilities`.
- Add strict schema `eval_llm_output_schema_v1` with fields:
  - `critique_points`, `conflicts`, `voi_ranked_actions`, `confidence_delta_suggestion`, `next_round_profile_suggestion`.
- Merge policy:
  - final `eval_report` still comes from deterministic evaluator,
  - store LLM output under `eval_report.llm_layer`,
  - update action ranking score with
    `expected_information_gain * feasibility_score * llm_priority_weight`.
- Safety:
  - LLM evaluator writes no case patch,
  - outputs only sidecar + merged `eval_report`.

### Planned implementation (LLM config split: master vs evaluator, pre-code lock)
- Introduce explicit config split inside `reasoning_config`:
  - `reasoning_config.master.{model, reasoning_effort}`
  - `reasoning_config.evaluator.{use_llm, model, reasoning_effort}`
- Default inheritance rule:
  - evaluator model/effort inherit master settings unless evaluator fields are explicitly set.
- Apply split in runtime calls:
  - MasterReasoner LLM call uses `reasoning_config.master.*`.
  - LLMEvaluator call uses `reasoning_config.evaluator.*` (or inherited master values).
- Keep backward compatibility:
  - existing top-level `model` / `reasoning_effort` still accepted as fallback.
- Add tests:
  - evaluator override model/effort path works,
  - inheritance path (no explicit evaluator model/effort) uses master settings.

---

## Data Refresh (train-only facts, current migration)
- **Fact source**: `data/train.csv` is now the only source for `data/private_clean.parquet`.
- **Business columns (authoritative)**: `id`, `code`, `SMILES`, `reference`, `molecular_weight`, `emission_solid`, `emission_aggr`, `features_id`, `mechanism_id`, `doi`.
- **Allowed derived columns in private_clean**: `canonical_smiles`, `inchikey`, and explicit missing flags (`emission_solid_missing`, `emission_aggr_missing`).
- **Explicitly excluded from facts**: `data/test.csv` (used only for simulated user input / evaluation; never merged into private_clean or private_observation evidence).
- **Compatibility rule**: downstream V1/V0 scripts must not assume legacy private fields (`absorption/qy/tau/tested_solvent`); when encountered, they must be removed or explicitly skipped.

### Rebuild order after train-only migration
1. P1 ingestion: `private_clean.parquet` -> `molecule_table.parquet` -> `rdkit_features.parquet`
2. Anchor space: `anchor_neighbors_ecfp.parquet` (structure-only)
3. UQ (pre-aTB): `mechanism_label_map.parquet` -> `mechanism_entropy_pre_atb.parquet` -> `uq_scores_pre_atb.parquet` -> `uq_scores_pre_atb_p5b.parquet`
4. Reports/queues: `reports/*.json` + `queue_*_pre_atb_p5b.parquet` + dashboard
5. V1 evidence/graph: `evidence_table.parquet` -> `graph_nodes.parquet` + `graph_edges.parquet` (retrieval consumes updated graph directly)

---

## V1 Objectives
- Add a structured evidence_table with provenance, conditions, and confidence.
- Build a light graph (nodes/edges) for traceable evidence ↔ molecule ↔ conditions.
- Provide subgraph retrieval for GraphRAG context (structure-first neighbors + evidence).
- Extend Chem Agent to harvest literature evidence into EvidenceClaim rows.
- Update reports to cite evidence IDs and provenance (no new mechanisms yet).

## V1 Deliverables
- `data/evidence_table.parquet`
- `data/graph_nodes.parquet`
- `data/graph_edges.parquet`
- `src/graph/retrieval.py` (subgraph API)
- `src/agents/chem_literature_agent.py`
- `reports/{id}.json` (with evidence references)

## V1 Milestones
- **V1-P0**: Define evidence_table schema + provenance rules (doc/schemas.md)
- **V1-P1**: Build evidence_table from existing sources (private_clean + atb_features/atb_qc); later append literature EvidenceClaim rows
  - Inputs: `data/private_clean.parquet`, `data/atb_features.parquet`, `data/atb_qc.parquet`
  - Output: `data/evidence_table.parquet` (per `doc/schemas.md` V1 schema)
  - Manifest: `data/evidence_table_build_manifest.json` (counts by evidence_type/field + invalid rows)
  - Validator: `python -m src.graph.validate_evidence_table`
- **V1-P2**: Export light graph tables (nodes.parquet, edges.parquet) from evidence_table + similarity edges
  - Export: evidence_table → Molecule/Evidence/Condition nodes + HAS_* / UNDER_CONDITION edges
  - Add structure-only SIMILAR_TO edges from `data/anchor_neighbors_ecfp.parquet` (no aTB / no mechanism nodes)
- **V1-P3**: Implement subgraph retrieval API (inchikey → 1–2 hop neighborhood) for GraphRAG context
  - Input: `inchikey` (+ optional budgets: `max_nodes`, `max_edges`)
  - Output: `{nodes, edges, provenance_refs}`
    - nodes: list of `{node_id, node_type, props}`
    - edges: list of `{src, rel, dst, props}`
    - provenance_refs: list of `evidence_id` included
  - Budget guideline: default `max_nodes=50`, `max_edges=200`
- **V1-P4-pre**: Literature candidate loop (relaxed; NOT writeback)
  - Input: Case File (inchikey + alias/common names + candidate mechanisms) OR direct (inchikey + alias list)
  - Retrieval tool: `literature_search_tool` (implementation may be `web_search` / deep research / external gateway). Tool is replaceable; strict/relaxed rules remain the same.
  - Output: populate Case File `evidence_readiness.literature.candidates[]` (candidate papers/leads; `verification="unverified"`);
    set `evidence_readiness.literature.mode="relaxed"` and `verification_status="candidates_only"`.
  - Constraint: relaxed candidates may not have citations/sources (gateway passthrough may be incomplete); do NOT write to `evidence_table` in this phase.
  - Gateway behavior (known degradation): gateway may append non-JSON text after JSON; parser may recover the leading JSON but treat this as a relaxed-mode signal.
  - Retrieval keys (initial): InChIKey + common name/alias (no SMILES search as primary)
  - Current implementation pattern (P4-pre two-stage):
    1) Stage A: Gemini native `generateContent + google_search` to collect grounding sources (then resolve redirect URLs to final landing URLs)
    2) Stage B: `Responses without tools` to structure papers only from Stage A sources
  - Source policy (Stage A → Stage B forwarding):
    - Default: `publisher_article_only_v1` (hard filter for publisher/article-like sources; blocks common noise hosts/pages while preserving trust boundary).
    - Optional: `allow_all` (no allowlist filtering; useful for debugging/recall checks).
    - Optional: `journal_allowlist_v1` (curated allowlist for drift reduction when strict venue constraints are desired):
      - Advanced Functional Materials (`advanced.onlinelibrary.wiley.com` / journal `16163028`)
      - Advanced Materials (`advanced.onlinelibrary.wiley.com` / journal `15214095`)
      - Materials Future (`iopscience.iop.org/journal/2752-5724`)
      - ACS Nano (`pubs.acs.org/journal/ancac3`)
      - Nature Materials (`nature.com/nmat`)
  - Trust boundary: Stage B may only emit candidate items whose `source_url` maps to Stage A sources (exact URL or normalized same-origin match). If source mapping fails, candidate is dropped.
  - Gate: when `sources=0` or `papers_out=0`, emit empty candidates and route next action to wetlab/min-experiment path; no EvidenceClaim writeback.
- **V1-P4-strict**: Verified literature evidence loop (writeback allowed)
  - Requirement: each paper/claim must have traceable `source_url` (and DOI when available) plus locator info (e.g., page number / table reference / quoted snippet).
  - Output: move entries into Case File `evidence_readiness.literature.verified_sources[]` and set `mode="strict"`, `verification_status="verified"`;
    append `literature_claim` rows to `data/evidence_table.parquet` with provenance + conditions + confidence.
- **V1-P5**: Reports reference evidence IDs + provenance; add writeback for EvidenceClaim (not new mechanisms yet)
- **V1-P6 (stub)**: Post-UQ agent slot (no write-back yet)
  - Input: `case_file` + `master_output` (Master Reasoner output)
  - Output: update Case File `evidence_readiness.current_gate` + `action_plan` based on evidence coverage/conflict signals; no new mechanism/hypothesis nodes in V1

## Interfaces
- **Case File** (`cases/{case_id}.json`) remains the shared artifact.
- **Evidence Table** (`data/evidence_table.parquet`) stores EvidenceClaim rows with provenance + conditions.
- **Graph Tables** (`data/graph_nodes.parquet`, `data/graph_edges.parquet`) derived from evidence_table + similarity edges.
- **Reports** cite evidence IDs and provenance; no mechanism writeback in V1.

## V1 Policy Notes
- Anchor retrieval remains **structure-only** (ECFP/structural embeddings). aTB is evidence/readiness only.
- Literature evidence is stored as EvidenceClaim with provenance + conditions; never overwrites `private_clean`.
- SMILES-first case file remains the shared artifact; evidence/readiness should be attached, not used for retrieval.
- Guardrail: V1 writes back EvidenceClaim only; no new mechanism/hypothesis nodes yet.
- EvidenceClaim values should be typed: use `value_num` when parseable (filterable), and keep raw extracted text in `value` for audit/fallback.

### aTB Neighborhood Consistency Check (structure retrieval unchanged)
This is a **ChemAgent-computed risk signal** for ReadyAgent reasoning-mode control.  
It does **NOT** change retrieval/indexing (retrieval remains ECFP-only), and it does **NOT** write to `data/evidence_table.parquet`.

**Where computed**:
- ChemAgent, after target aTB cache is loaded and neighbor aTB packs are available.
- Target vector fields (default): `delta_gap`, `delta_dihedral`, `delta_volume`.
- Neighbor distribution includes only neighbors where:
  - `neighbor_atb.cache_status == "success"`, and
  - all required delta fields are present and numeric.
- Missing/failed/partial neighbor aTB entries are ignored.

**Math**:
- `z_i = (x_i - median_i) / (1.4826 * MAD_i)`
- `MAD_i = median(|neighbor_i - median_i|)`
- If `MAD_i == 0`:
  - if `x_i == median_i`: use `z_i = 0`
  - else: `z_i = null` and warning `mad_zero:<field>`
- Aggregates:
  - `outlier_score_max = max(|z_i|)` over valid dimensions
  - `outlier_score_rss = sqrt(mean(z_i^2))` over valid dimensions

**Flag semantics**:
- `target_missing`: target aTB unavailable/incomplete
- `insufficient_neighbors`: valid neighbor sample size `< min_sample_size` (default 5)
- `inlier`: sample sufficient and `outlier_score_max < z_max` (default 3.5)
- `outlier`: sample sufficient and `outlier_score_max >= z_max`

**Reliability heuristic**:
- `low`: sample_size < 8 OR any MAD zero OR <2 valid z dimensions
- `medium`: sample_size >= 8 and 2+ valid dimensions and no MAD zero
- `high`: sample_size >= 15 and 3 valid dimensions and stable MADs

**Case file target**:
- ChemAgent writes full object to `risk_scores.atb_neighbor_consistency`.
- ReadyAgent may mirror a scalar shortcut to `risk_scores.readiness_atb_neighbor_flag` for rationale/readability.

### Case file action plan semantics (LLM-friendly)
The Case File action plan is designed to be **directly executable by an LLM** (as a controller/reasoner). It is an ordered list of **structured action objects**, plus a short rationale list.

**Definitions**:
- `evidence_readiness.current_gate.reasoning_mode ∈ {"blocked","normal","conservative"}`:
  - `blocked`: do not run the reasoner yet; must collect blocking evidence first.
  - `normal`: run the reasoner normally; evidence is sufficient and consistent with neighborhood.
  - `conservative`: reasoner may run, but must explicitly hedge and request escalation evidence.
- `action_plan`: ordered list of action objects (not strings).
- `action_rationale`: ordered short strings explaining why these actions were chosen and prioritized.

**Decision rules (pre-UQ / SMILES-first)**:
- ReadyAgent is the sole gate owner.
- If `risk_scores.atb_neighbor_consistency.flag=="outlier"` and reliability is `medium|high`:
  - use/keep `reasoning_mode="conservative"` (do not force hard block by this signal alone),
  - add non-blocking verification actions (e.g., `literature_search_web`, `request_manual_pdf`, `request_min_experiment_emission`).
- If flag is `target_missing` or `insufficient_neighbors`:
  - include rationale token, but do not overreact beyond existing gating policy.
- If flag is `inlier` and other evidence is healthy:
  - keep normal path.

**Important**:
- aTB-outlier does NOT change structure retrieval; it only changes `reasoning_mode` and the recommended next actions.

## Risks & Guardrails
- **Provenance**: every EvidenceClaim must include source, timestamp, and extraction method.
- **Condition mismatch**: keep measurement conditions explicit; avoid merging incompatible conditions.
- **No overwrites**: evidence appends only; never mutate private_clean or experimental facts.
- **Traceability**: all graph edges must map back to evidence_table row IDs.

## Multi-Agent Architecture (V1)

### Why lightweight orchestrator first (not LangChain-first)
- We prioritize a minimal, explicit control loop over a framework-heavy stack to keep case-file semantics and writeback rules deterministic.
- Current V1 needs are narrow and auditable: read case → select actions → run bounded agents → write patch + artifacts + run logs.
- This keeps migration cost low while the literature lane is still unstable at the citation/provenance boundary.
- Migration to LangGraph/Temporal is enabled later when one of these conditions is met:
  - multi-tenant scheduling and retries become operational bottlenecks,
  - cross-run durable checkpoints and human-in-the-loop workflows need first-class orchestration,
  - agent graph branching/parallel fan-out exceeds current explicit policy loop complexity.

### Orchestrator responsibilities
- Read `cases/{case_id}.json` as the single source of truth.
- Interpret `current_gate`, `reasoning_mode`, and structured `action_plan` to decide execution order and stop conditions.
- Invoke agent plugins through a registry, pass normalized inputs, collect outputs.
- Apply validated patch writes back to case file, persist artifacts/raw dumps, append `agent_runs[]`.
- Emit run-level summary for reports/post-UQ handoff (no direct mechanism/hypothesis node writeback in V1).

### Agent I/O contract (uniform)
- **Input**: `{case, context, runtime_config}`.
  - `case`: full case file snapshot at dispatch time.
  - `context`: retrieval/evidence payloads (graph subgraph, aTB pack, literature candidates).
  - `runtime_config`: deterministic knobs (timeouts, budgets, mode strict/relaxed/off).
- **Output**: `{patch, artifacts, warnings, metrics, status}`.
  - `patch`: JSON-merge style delta for case file fields only.
  - `artifacts`: list of produced files (path + type + checksum optional).
  - `warnings`: non-fatal quality/degradation notes.
  - `metrics`: counters/timing/token-usage (optional).
  - `status`: `success|partial|failed|skipped`.

### Writeback mechanism and policy gates
- V1 write targets: case file always; evidence table only under policy gate.
- `evidence_writeback_policy`:
  - `strict`: requires provenance-complete citations/sources and locator evidence before `literature_claim` writeback.
  - `relaxed`: candidate-only flow; update case literature candidates but do NOT write to `evidence_table`.
  - `off`: no evidence-table writeback regardless of available evidence.
- Required audit fields on all write-capable outputs: `provenance`, `confidence`, `timestamp`, `raw_dump_path` (or equivalent raw reference).

### Literature degradation strategy (agent slot)
- When source passthrough is unstable or citations are insufficient:
  - keep outputs in case as `literature.candidates` with unverified markers,
  - do not write `evidence_table` claims,
  - route next action to wetlab/minimum-experiment or manual curation path.
- Literature deep-search and MinerU extraction stay as a parallel agent workstream; orchestrator keeps a stable slot/interface.

### Suggested repository layout (doc-level blueprint)
- `src/core/`
  - `patching.py`: RFC6902 apply + whitelist/append-only enforcement
  - `hashing.py`: stable input/output hash helpers
  - `artifacts.py`: replay bundle writer + manifest
  - `io.py`: case/artifact read-write helpers
- `src/orchestration/`
  - `orchestrator.py`: deterministic multi-agent loop runner
  - `registry.py`: agent construction and ordering
  - `policies.py`: gate-aware branching policy (reasoning conditional)
  - `run_one.py`: single command entrypoint for one sample
- `src/agents/`
  - `DataAgent` (structural priors only)
  - `ChemAgent` (aTB + offline_pdf extraction + staging/writeback)
  - `ReadyAgent` (gate owner)
  - `ReasoningAgent` (LLM master; stub fallback allowed)
  - `JudgeAgent` (post-UQ critique + suggestions)
- `src/tools/`
  - `llm_client.py`: OpenAI-compatible Responses wrapper
  - `mineru_runner.py`: MinerU resolve/run adapter

### Agent input/output/degradation map
- `case_builder`
  - Input: `query.smiles`, optional aliases/code from test row
  - Output: base case file fields (`query`, baseline `evidence_readiness`, initial `action_plan`)
  - Degrade: invalid SMILES -> `current_gate.blocked` + warning + no downstream reasoning
- `graph_retriever`
  - Input: `query.inchikey`, retrieval budgets
  - Output: subgraph context artifact + case context refs
  - Degrade: missing inchikey/graph miss -> empty subgraph + warning
- `atb_pack`
  - Input: `query.inchikey`, neighbor list, atb cache/tables
  - Output: target/neighbor aTB summaries, `risk_scores.atb_neighbor_consistency`
  - Degrade: `cache_status` absent/failed/partial -> write status and block/route actions per policy
- `literature_scout` (slot)
  - Input: inchikey + aliases + writeback policy
  - Output: candidates or verified sources, raw dumps, warnings
  - Degrade: sources unstable -> candidate-only writeback in case; never evidence table in relaxed mode
- `mineru_extractor` (slot)
  - Input: verified/landing URLs or PDFs
  - Output: extracted structured claims artifact, optional strict writeback-ready payload
  - Degrade: missing PDF/locator -> warning + skip
- `master_reasoner`
  - Input: case + assembled context (graph, aTB, literature candidate/verified status)
  - Output: `master_output` artifact and case summary patch
  - Degrade: if blocked gate -> skip and write rationale
- `post_uq_reviewer`
  - Input: case + master output + evidence coverage/conflict signals
  - Output: updated `current_gate`, `action_plan`, `post_uq` fields
  - Degrade: insufficient evidence -> conservative/blocked mode with explicit next actions

### READY_AGENT (independent readiness controller)
- Role:
  - Run after Data Agent / Chem Agent updates.
  - Read full case JSON and be the only writer of:
    - `current_gate`
    - `action_rationale`
    - `action_plan`
    - optional `risk_scores.readiness_*`
- Hard write scope:
  - RFC6902 patch paths limited to:
    - `/current_gate/*`
    - `/action_rationale`
    - `/action_plan`
    - `/risk_scores/readiness_*`
  - READY_AGENT must not write `target_fields` or other evidence payload fields.
- Gate states:
  - `blocked_input_missing`
  - `needs_manual`
  - `ready_for_reasoning`
  - `ready_conservative`
- Decision checks (ordered):
  1) Emission availability + required provenance (`source_ref`, `source_locator`, `confidence`)
  2) Anti-leakage for `emission_aggr_nm` (must include aggregation condition signal)
  3) Identity metadata (`identity_match`, `identity_match_confidence`, optional `matched_entity_in_paper`)
  4) Rationale/gate consistency
  5) aTB downgrade logic (aTB failure -> `ready_conservative` if emission evidence is otherwise sufficient)
- Action-plan policy:
  - `blocked_input_missing` -> `request_manual_pdf` (blocking)
  - extraction/manual gap -> `rerun_offline_pdf_extractor` or `manual_extract` (blocking)
  - identity uncertainty -> `manual_identity_verify_from_pdf`
  - ready states -> priority=1 `run_master_reasoner` / `run_master_reasoner_stub`
  - aTB failures in ready states -> queue `retry_target_atb`
- CLI integration (current):
  - `case-run` is the official release entrypoint.
  - `case-e0`, `case-e2e`, `case-e2e-atb` are compatibility aliases and forward to `case-run`.
  - READY_AGENT is the final writer for `current_gate`, `action_rationale`, and `action_plan`.

### Single-sample execution plan (test.csv, aTB-success lane)
- Target: run one `test.csv` sample end-to-end with release runtime in
  `run_lane=atb_cache_only` (skip unfinished literature/wet-lab).
- Steps:
  1) Resolve input (`--code` from `test.csv`, `--row-index`, or direct `--smiles`).
  2) Build initial case and run orchestrator sequence:
     Data -> Chem(aTB cache lane) -> Ready -> (conditional) Reasoning -> Judge -> Ready.
  3) For each step: validate RFC6902 patch, append `agent_runs[]`, persist replay artifacts.
  4) If reasoning is skipped by gate, step must carry structured `status_reason_code=gate_blocked_reasoning`.
- Acceptance output:
  - final case file with complete `agent_runs[]` lineage,
  - `artifacts/{run_id}/00..06 + manifest.json`,
  - optional stage snapshots (`data_agent_case`, `chem_agent_case`, `ready_agent_case`),
  - no `evidence_table` writeback.

### One-shot command (release)
- Official:
  - `python -m src.cli case-run --test-csv data/test.csv --code <CODE> --run-lane atb_cache_only`
- Optional:
  - add `--emit-stage-snapshots --stage-snapshots-dir cases/stage_snapshots` for per-stage case snapshots.
  - snapshot `before_case_e0`
  - run E0 writeback (`sidecar_only` or `mineru_llm`)
  - emit compact JSON summary with before/after case paths + run artifact location
- This command is the default operator path for “single-line run-through” validation.
- Operator output modes:
  - `--artifact-mode full` (debug): keep `artifacts/{run_id}/00..06 + manifest`.
  - `--artifact-mode final_case_only` (default for clean runs): keep only two artifacts:
    - compact reasoning-only final case file (`cases/.../{case_id}.json`)
    - stable run log (`artifacts/.../{case_id}.run_log.json`) including LLM request/response trace.

### Implementation phases (reordered: Example A-first)
- **E0 — Patch + Staging + Replay (current milestone)**
  - Deliverables: minimal linear runner, RFC6902 patch output, full replay artifacts, idempotency, 4-state gate.
  - Scope: offline_pdf lane only; `master/post-uq` stubs only.
  - Hard guard: evidence-table writeback disabled.
- **E1 — Stability hardening**
  - Deliverables: deterministic reruns, stronger failure categorization, replay verification utilities.
  - Scope: keep linear runner; no registry/policy abstraction yet.
- **E2 — Abstraction after closure stability**
  - Deliverables: extract registry/policy/types from proven E0/E1 behavior.
  - Scope: preserve behavior while refactoring control flow.

### E0 output contract (locked)
- `agent_patch` (RFC6902): `artifacts/{run_id}/03_agent_patch.json`
  - Format: list of `{op,path,value}`.
  - Allowed ops: `add | replace | test`.
  - Allowed paths:
    - `/target_fields/emission_aggr_nm`
    - `/target_fields/emission_solid_or_film_nm`
    - `/target_fields_provenance/*`
    - `/evidence_acquire/emission/*`
    - `/evidence_candidates_staging/-` (append-only)
    - `/agent_runs/-` (append-only)
    - `/history/-` (append-only)
    - `/current_gate/*`
    - `/reasons/-`
    - `/next_actions/-`
- `evidence_candidates_staging[]` in case file is the canonical E0 candidate staging store.
- Replay artifacts directory:
  - `00_input_snapshot.json`
  - `01_extractor_raw.json`
  - `02_candidates_normalized.json`
  - `03_agent_patch.json`
  - `04_case_before.json`
  - `05_case_after.json`
  - `06_case_diff.json`
  - `manifest.json` (sha256 of each artifact)
- Stable run log (always emitted, independent of replay mode):
  - `{artifacts_dir}/{case_id}.run_log.json`
  - Includes: run metadata, gate result, reason codes, selected writeback fields, extractor diagnostics, and LLM request/response capture.
- State synchronization contract:
  - E0 must keep top-level and nested readiness state aligned:
    - `current_gate.*` <-> `evidence_readiness.current_gate.*`
    - `evidence_readiness.literature.status/sources/last_update/notes` reflect E0 extraction outcome.
- `inputs_hash`: sha256 of canonical bytes of `00_input_snapshot.json`; copied into every `agent_runs[].inputs_hash` for replay linkage.

### E0 gate state machine (locked)
- `blocked_input_missing`
  - Trigger: `mode=offline_pdf` and no valid `inputs.offline_pdfs`.
- `failed_extract`
  - Trigger: extractor/parsing exception (timeout, malformed output, missing extractor payload).
- `extracted_no_writeback`
  - Trigger: extraction succeeded but no writeback-eligible fields:
    - no candidates, or
    - all candidates rejected, or
    - candidates missing required provenance / identity constraints.
- `ready_for_reasoning`
  - Trigger:
    - `strictness=strict`: at least one verified target field is written with complete provenance.
    - `strictness=relaxed`: at least one non-rejected target field is written with minimal provenance (`source_locator` required; page may be missing).
- Parallel append-only diagnostics:
  - `reasons[]`: machine-readable reason codes
  - `next_actions[]`: suggested actions (`request_manual_pdf`, `manual_extract`, `run_master_reasoner_stub`)

### E0 idempotency key (locked)
- Definition:
  - `idempotency_key = sha256(canonical_json(key_material))`
- `key_material` includes:
  - `case_id`
  - `mode`
  - sorted `offline_pdfs[]` (`path_or_id + sha256`)
  - `extractor_name`, `extractor_version`
  - `extractor_config_hash`
  - `normalizer_config_hash`
  - `mapping_version`
  - `pdf_page_selection_hash` (empty string if not used)
- Behavior:
  - If key unchanged and `--force` is not set:
    - skip extraction/writeback/staging updates,
    - append only `agent_runs` with `status=skipped`.

### E0 field mapping and deterministic selection (locked)
- Targets:
  - `emission_aggr_nm`
  - `emission_solid_or_film_nm`
- Candidate filtering:
  - non-nm and non-convertible units -> rejected
  - invalid numeric value -> rejected
  - `identity_match=unmatched` -> rejected
  - `emission_aggr_nm` accepts only explicit aggregate context (anti-leakage)
- Writeback eligibility:
  - `strict`: only `verification_status=verified`.
  - `relaxed` (default): allow non-rejected candidates for case-file writeback when locator exists, even if page is unavailable.
- `emission_solid_or_film_nm` deterministic tie-break:
  1) `verification_status=verified`
  2) `value_source_kind`: `table > figure > text`
  3) condition priority: `film > solid/powder > crystal`
  4) confidence (desc)
  5) page (asc)
  6) `candidate_id` (lexicographic)

### E0 offline_pdf verification semantics (locked)
- `verified` in offline_pdf mode means:
  - locator is present; page is preferred but not mandatory when locator is clearly structured (`Table`/`Fig`/`Figure`),
  - value parsed to nm,
  - condition mapped to target field,
  - identity is not unmatched.
- This is independent from external citations/web-search provenance.

### E0 writeback boundary (hard guard)
- `WRITEBACK_EVIDENCE_TABLE = false` in E0 runner.
- Any request to enable evidence-table writeback must fail fast.
- Primary safety check is code-path gating, not file mtime:
  - all evidence-table writes must route through a single internal writer hook,
  - E0 never calls that hook in normal runs,
  - tests monkeypatch the hook and assert it is not invoked.
- `mtime` can be kept only as a secondary smoke signal (non-authoritative).

### Execution-ready criteria (E0)
- Patch whitelist/ops are enforced and fail fast on violations.
- Gate transitions are deterministic for all failure/success states.
- Idempotent rerun does not duplicate staging/field writeback.
- Replay artifacts are complete and hash-linked.

### Emission Evidence Acquisition Mode (offline_pdf vs web_search)

#### Config switch
- `evidence_acquire.emission.mode`
  - Allowed: `offline_pdf` | `web_search`
  - Default (current phase): `offline_pdf`
- `evidence_acquire.emission.strictness`
  - Allowed: `strict` | `relaxed`
  - Default (current phase): `relaxed`

#### `offline_pdf` mode (current unblocker lane)
- Input:
  - `case.query.inchikey` / `case.query.smiles`
  - `case.inputs.offline_pdfs[]` (local path or file id)
- Flow:
  - PDF -> `mineru_extract_pdf` -> extraction JSON -> emission field mapper -> case file patch
  - Target writeback fields:
    - `target_fields.emission_aggr_nm`
    - `target_fields.emission_solid_or_film_nm`
    - `target_fields_provenance.*` (source type, locator, confidence, timestamp, raw dump ref)
- Degradation:
  - If PDF parse/extraction fails, write:
    - `evidence_readiness.literature.status="failed_offline_pdf"`
  - Add actions:
    - `request_manual_pdf`
    - `manual_extract`

#### `web_search` mode (agent slot; parallel workstream)
- Input:
  - `inchikey` + aliases from case file
- Output:
  - candidate papers list (`doi/title/url/pdf_url/source_type/source_ref`)
- Current status:
  - Implemented as interface/policy slot in this repo.
  - Search/deep extraction stability is owned by teammate workstream.
  - In unstable citation/source passthrough conditions, remain in candidate-only behavior.

#### Orchestrator switch/fallback rules
- If `mode=offline_pdf` and `inputs.offline_pdfs` is empty:
  - do not run extractor;
  - enqueue `request_manual_pdf`.
- If `mode=web_search` and web-search agent unavailable/fails:
  - fallback to `offline_pdf` when offline PDFs are provided;
  - otherwise enqueue `request_manual_pdf` (and optional manual_extract).

#### Writeback policy for emission completion
- `offline_pdf` lane:
  - Stable human-provided PDF input allows strict case-file writeback of emission fields.
  - EvidenceClaim writeback is allowed only under V1 guardrail (EvidenceClaim only, no mechanism/hypothesis node writeback) and strict provenance completeness.
- `web_search` lane (current unstable period):
  - Use `relaxed` behavior by default.
  - Write only candidates to case file (`literature.candidates` / `evidence_readiness.literature.candidates`).
  - Do not write `evidence_table` until strict provenance requirements are met.
- `strictness` interpretation:
  - `strict`: must trace to PDF/table/page/section locator.
  - `relaxed`: candidate-only case updates; no evidence-table writeback.

#### Single-sample text data flow (Example A)
- `test.csv` sample
  -> `create_case_from_smiles`
  -> (`evidence_acquire.emission.mode` switch)
  -> `mineru_extract_pdf` (offline_pdf lane)
  -> `emission_extract`
  -> `update_case_file` (target_fields + provenance + agent_runs)
  -> `ready_for_reasoning`
  -> `master_reasoner`

#### Example A minimal case fields (offline_pdf lane)
```json
{
  "query": {"smiles": "...", "inchikey": "..."},
  "inputs": {
    "offline_pdfs": [
      {"path_or_id": "inputs/papers/sample.pdf", "sha256": "...", "provided_by": "human"}
    ]
  },
  "evidence_acquire": {"emission": {"mode": "offline_pdf", "strictness": "strict"}},
  "target_fields": {
    "emission_aggr_nm": 621.0,
    "emission_solid_or_film_nm": 651.0
  },
  "target_fields_provenance": {
    "emission_aggr_nm": {"source_type": "offline_pdf", "source_locator": "Table 1, p2", "confidence": 0.94},
    "emission_solid_or_film_nm": {"source_type": "offline_pdf", "source_locator": "Table 1, p2", "confidence": 0.91}
  }
}
```
## 2026-02 Addendum: Emission Evidence Acquisition Mode (offline_pdf vs web_search)

This addendum defines a switch in the Multi-Agent / Case File flow for emission evidence acquisition. It is intentionally doc-only and architecture-level.

### Config switch

- `evidence_acquire.emission.mode` allowed values: `offline_pdf | web_search`
- Default (current phase): `offline_pdf`
- `evidence_acquire.emission.strictness` allowed values: `strict | relaxed`
- Current default strictness: `relaxed` for web-search-derived candidates; `strict` can be used for verified offline PDF extraction.

### Mode behavior

#### 1) `offline_pdf` (current unblocker path)

Input:
- `case.inchikey` / `case.smiles`
- `case.inputs.offline_pdfs[]` (local path or file id)

Flow:
- PDF -> MinerU (or equivalent parser) -> extraction JSON -> case-file patch
- Write emission targets into case file:
  - `target_fields.emission_aggr_nm`
  - `target_fields.emission_solid_or_film_nm`
- Write provenance into:
  - `target_fields_provenance.*` with `source_type=offline_pdf`, locator (page/table/section), confidence, timestamp

Degrade-on-failure:
- If PDF parse/extract fails, write:
  - `case.evidence_readiness.literature.status = "failed_offline_pdf"`
- Append action items:
  - `request_manual_pdf` and/or `manual_extract`

#### 2) `web_search` (agent slot, parallel track)

Input:
- `inchikey` + aliases from case file

Output:
- Candidate papers list (`literature.candidates[]`)

Current state:
- Online search chain works, but citations/sources pass-through is not consistently stable.
- Therefore the repository keeps this as an interface + policy slot.
- This path must not block the offline PDF lane.

### Orchestrator mode-switch policy

- If `mode=offline_pdf` and `inputs.offline_pdfs` is empty:
  - do not run extraction
  - set action plan to include `request_manual_pdf`
- If `mode=web_search` and web-search agent fails/unavailable:
  - fallback to `offline_pdf` when PDF input exists
  - otherwise request manual path (`request_manual_pdf` / `manual_extract`)

### Strict/relaxed writeback policy

- `strict`:
  - requires traceable evidence (PDF + page/table/section or equivalent locator)
  - allows strict emission writeback to case file
  - EvidenceClaim writeback is allowed under V1 guardrail (EvidenceClaim-only; no mechanism/hypothesis writeback)
- `relaxed`:
  - allows candidate accumulation in `literature.candidates[]`
  - does not write to `evidence_table` until strict traceability is satisfied

### Single-sample textual data flow (Example A lane)

`test.csv sample -> create_case_from_smiles -> mode switch -> mineru_extract_pdf -> emission_extract -> update_case_file -> ready_for_reasoning -> master_reasoner`

### Example A: offline_pdf single-sample minimal fields

```yaml
case_id: "case_test_001"
inputs:
  smiles: "..."
  offline_pdfs:
    - path_or_id: "data/manual_pdfs/sample_001.pdf"
      sha256: "optional_hash"
evidence_acquire:
  emission:
    mode: "offline_pdf"
    strictness: "strict"
target_fields:
  emission_aggr_nm: 523.0
  emission_solid_or_film_nm: 548.0
target_fields_provenance:
  emission_aggr_nm:
    source_type: "offline_pdf"
    source_locator: "Table 1, p.2"
    confidence: 0.92
  emission_solid_or_film_nm:
    source_type: "offline_pdf"
    source_locator: "Figure 3 caption, p.4"
    confidence: 0.88
evidence_readiness:
  literature:
    status: "verified"
```

## 2026-03 Addendum: Testset Mechanism Benchmark (v0, evaluation-only)

This addendum introduces a reproducible testset benchmark pipeline without changing
runtime reasoning strategy or schema.

### Scope

- Input: `data/test.csv`
- Ground truth column: `mechanism_id`
- Runtime path: release `run_one`/`case-run` equivalent
- Default lane: `atb_cache_only`
- Default mode: single-pass (`iterative=false`)
- No evidence table writeback; keep existing no-touch guardrails.

### Prediction read path

Evaluator reads predicted mechanism label from case JSON:

1. Primary: `/master_reasoning/mechanism_claim/primary_hypothesis/mechanism_label`
2. Fallback: `/reasoning/master_reasoning/mechanism_claim/primary_hypothesis/mechanism_label`

### Deterministic run contract

Benchmark runner records fixed model parameters in report:

- `model`
- `base_url`
- `reasoning_effort`
- `temperature` (default `0.0`)
- `llm_use_json_schema` (default `false`)
- `seed_supported=false`, `seed=null` (current client has no seed API)

### Label normalization for evaluation only

A dedicated `src/eval/label_normalizer.py` aligns GT/pred labels for scoring only.
It does not mutate runtime outputs.

Canonical labels:
- `TICT`, `ICT`, `ESIPT`, `neutral aromatic`, `other`, `unknown`

Locked mappings:
- `tict-like` -> `TICT`
- `ict-like` -> `ICT`
- `clusterluminescence` -> `unknown`
- `ESIPT+ICT/TICT` -> `unknown`

### Status codes per sample

- `ok`: run succeeded, GT present, prediction present
- `failed_run`: runtime exception
- `missing_pred`: run succeeded, prediction missing
- `missing_gt`: GT missing/empty

### Metrics (v0)

Computed on `status=ok` subset:
- `top1_accuracy`
- `macro_f1`
- `per_class_precision_recall_f1`
- `confusion_matrix`

Computed on full set:
- `coverage = (run succeeded and prediction extracted) / total_rows`
- `unknown_rate = predicted_unknown / covered_rows`

### Output artifacts

- `artifacts/eval/<timestamp>/predictions.csv`
- `artifacts/eval/<timestamp>/evaluation_report.json`
- `artifacts/eval/<timestamp>/evaluation_report.md`
- Optional: `failed_cases_index.json` (path index only, no large-file copy)
