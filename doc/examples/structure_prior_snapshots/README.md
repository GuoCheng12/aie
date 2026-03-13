# Structure Prior Snapshots

These snapshots capture the current rule-generated R0 prior objects for two reference molecules under the current main benchmark setup.

- Source split: `/Users/wuguocheng/workshop/Uncertainty_aware_AIE/data/split_list/1_level.csv`
- Reference view: `/Users/wuguocheng/workshop/Uncertainty_aware_AIE/data/reference_indices/split_levels_v2/views/leave_level_1`
- Export objects per molecule:
  - `context.json`
  - `structure_prior_profile.json`
  - `structure_motif_profile.json`
  - `structure_fact_sheet.json`
  - `prior_reliability_profile.json`
  - `candidate_slate_v2.json`

Notes:
- These files are deterministic exports from the current code path; they are not LLM-generated.
- `structure_fact_sheet` is a compact merge of `structure_prior_profile` and `structure_motif_profile`.
- `prior_reliability_profile` and `candidate_slate_v2` depend on the current `leave_level_1` reference view and top-k retrieval settings.
