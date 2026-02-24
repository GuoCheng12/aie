# Multi-agent Refactor Plan (V1-MA-P1)

## Scope

Build a production-like multi-agent runtime with:
- patch-scoped writes,
- idempotent step execution,
- replay artifacts,
- offline_pdf Chem lane (MinerU + LLM),
- Ready Agent gate ownership.

No evidence_table writeback in this plan.

## Work breakdown

1. **Core runtime primitives**
   - `src/core/patching.py`: RFC6902 apply + whitelist/append-only validation
   - `src/core/hashing.py`: stable hashes for idempotency/inputs
   - `src/core/artifacts.py`: per-step replay bundles + manifests
   - `src/core/io.py`: case/artifact I/O

2. **Tools**
   - `src/tools/llm_client.py`: OpenAI-compatible Responses wrapper with strict JSON schema mode
   - `src/tools/mineru_runner.py`: MinerU resolve/run adapter

3. **Agents**
   - Data Agent: canonicalize, neighbors, structural priors
   - Chem Agent: aTB cache pack + offline_pdf extraction + staging/target writeback
   - Ready Agent: gate/action owner (reuse rule engine)
   - Reasoning Agent: LLM/stub master output namespace
   - Judge Agent: post_uq + action suggestion appends

4. **Orchestrator**
   - `src/orchestration/registry.py` + `src/orchestration/policies.py`
   - `src/orchestration/orchestrator.py`: fixed loop with conditional reasoning step
   - `src/orchestration/run_one.py`: one-command entrypoint

5. **Tests**
   - patch whitelist/append-only enforcement
   - Ready Agent gate behavior
   - integration run of one sample with mocked external dependencies

## Acceptance checks

- All agent updates are applied only through validated RFC6902 patches.
- No non-Ready agent can modify `current_gate` or `action_rationale`.
- Every step appends `agent_runs[]` with `inputs_hash` + `idempotency_key`.
- Each step emits replay artifacts:
  - `00_input_snapshot.json`
  - raw outputs
  - `03_patch.json`
  - `04_case_before.json`
  - `05_case_after.json`
  - `06_case_diff.json`
  - `manifest.json`
- Demo command runs and outputs:
  - updated case json path
  - artifacts path
  - concise run summary JSON

## Repo cleanup candidates (post-refactor archive)

These are legacy/experimental and not on the structure-only mainline:
- `src/features/anchor_hybrid_ecfp_atb_partial.py`
- `src/features/anchor_two_stage_partial_atb.py`
- `src/features/m_sweep_two_stage_partial_atb.py`
- `src/features/validate_anchor_space_hybrid_partial_atb.py`
- `src/features/validate_two_stage_partial_atb.py`
- corresponding data artifacts/manifests:
  - `data/anchor_neighbors_hybrid_partial_atb.parquet`
  - `data/anchor_neighbors_two_stage_partial_atb.parquet`
  - `data/anchor_hybrid_partial_atb_manifest.json`
  - `data/anchor_neighbors_two_stage_partial_atb_manifest.json`
  - `data/m_sweep_results.json`

