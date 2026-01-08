# doc/schemas.md

## Core Artifact Schemas (V0)

> This file defines column schemas for all parquet artifacts in V0.
> Use this as the single source of truth when implementing data pipelines.

---

## 1. `data/private_clean.parquet`

Standardized private dataset with unit normalization and missing masks.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| id | int64 | No | Original row ID |
| code | string | Yes | Molecule code |
| smiles | string | No | Original SMILES (as-is from CSV) |
| canonical_smiles | string | No | RDKit-canonicalized SMILES |
| inchikey | string | No | InChIKey from canonical SMILES |
| molecular_weight | float64 | Yes | MW (g/mol) |
| absorption | string | Yes | Raw absorption field (unparsed) |
| absorption_peak_nm | float64 | Yes | Parsed peak wavelength (nm), if extractable |
| emission_sol | float64 | Yes | Emission wavelength in solution (nm) |
| emission_solid | float64 | Yes | Emission wavelength in solid (nm) |
| emission_aggr | float64 | Yes | Emission wavelength in aggregate (nm) |
| emission_crys | float64 | Yes | Emission wavelength in crystal (nm) |
| qy_sol | float64 | Yes | Quantum yield in solution, normalized [0,1] |
| qy_solid | float64 | Yes | Quantum yield in solid, normalized [0,1] |
| qy_aggr | float64 | Yes | Quantum yield in aggregate, normalized [0,1] |
| qy_crys | float64 | Yes | Quantum yield in crystal, normalized [0,1] |
| qy_sol_raw | float64 | Yes | Raw qy_sol (percent, 0–100) |
| qy_solid_raw | float64 | Yes | Raw qy_solid (percent) |
| qy_aggr_raw | float64 | Yes | Raw qy_aggr (percent) |
| qy_crys_raw | float64 | Yes | Raw qy_crys (percent) |
| qy_unit_inferred | string | No | "percent" (constant for this dataset) |
| tau_sol | float64 | Yes | Lifetime in solution (ns) |
| tau_solid | float64 | Yes | Lifetime in solid (ns) |
| tau_aggr | float64 | Yes | Lifetime in aggregate (ns) |
| tau_crys | float64 | Yes | Lifetime in crystal (ns) |
| tau_sol_raw | float64 | Yes | Raw tau_sol |
| tau_solid_raw | float64 | Yes | Raw tau_solid |
| tau_aggr_raw | float64 | Yes | Raw tau_aggr |
| tau_crys_raw | float64 | Yes | Raw tau_crys |
| tau_sol_outlier | bool | Yes | True if tau_sol > outlier threshold |
| tau_solid_outlier | bool | Yes | True if tau_solid > outlier threshold |
| tau_aggr_outlier | bool | Yes | True if tau_aggr > outlier threshold |
| tau_crys_outlier | bool | Yes | True if tau_crys > outlier threshold |
| tested_solvent | string | Yes | Solvent used in testing |
| mechanism_id | int64 | Yes | Mechanism label (unreliable) |
| features_id | int64 | Yes | Features label |
| emission_sol_missing | bool | No | True if emission_sol is null |
| emission_solid_missing | bool | No | True if emission_solid is null |
| emission_aggr_missing | bool | No | True if emission_aggr is null |
| emission_crys_missing | bool | No | True if emission_crys is null |
| qy_sol_missing | bool | No | True if qy_sol is null |
| qy_solid_missing | bool | No | True if qy_solid is null |
| qy_aggr_missing | bool | No | True if qy_aggr is null |
| qy_crys_missing | bool | No | True if qy_crys is null |
| tau_sol_missing | bool | No | True if tau_sol is null |
| tau_solid_missing | bool | No | True if tau_solid is null |
| tau_aggr_missing | bool | No | True if tau_aggr is null |
| tau_crys_missing | bool | No | True if tau_crys is null |
| absorption_missing | bool | No | True if absorption is null |
| tested_solvent_missing | bool | No | True if tested_solvent is null |

---

## 2. `data/molecule_table.parquet`

Unique molecules by InChIKey with ID mapping.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key**, unique |
| canonical_smiles | string | No | RDKit-canonicalized SMILES |
| id_list | list[int64] | No | List of original IDs mapping to this molecule |
| n_records | int64 | No | Count of records for this molecule |

---

## 3. `data/rdkit_features.parquet`

RDKit-computed molecular descriptors.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| mw | float64 | No | Molecular weight |
| logp | float64 | Yes | Crippen LogP |
| tpsa | float64 | Yes | Topological polar surface area |
| n_rotatable_bonds | int64 | Yes | Rotatable bond count |
| n_hbd | int64 | Yes | H-bond donors |
| n_hba | int64 | Yes | H-bond acceptors |
| n_rings | int64 | Yes | Ring count |
| n_aromatic_rings | int64 | Yes | Aromatic ring count |
| n_heavy_atoms | int64 | Yes | Heavy atom count |
| ecfp_2048 | list[int8] | Yes | ECFP4 fingerprint (2048 bits, packed) |

---

## 4. `data/atb_features.parquet`

aTB-computed micro-physical descriptors.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| run_status | string | No | "success" / "failed" / "pending" |
| fail_stage | string | Yes | null if success; else "opt"/"excit"/"neb"/"volume"/"feature_parse" |
| s0_volume | float64 | Yes | S0 molecular volume (Å³) |
| s1_volume | float64 | Yes | S1 molecular volume (Å³) |
| delta_volume | float64 | Yes | S1 - S0 volume |
| s0_homo_lumo_gap | float64 | Yes | S0 HOMO-LUMO gap (eV) |
| s1_homo_lumo_gap | float64 | Yes | S1 HOMO-LUMO gap (eV) |
| delta_gap | float64 | Yes | S1 - S0 gap |
| s0_dihedral_avg | float64 | Yes | Average dihedral angle (S0) |
| s1_dihedral_avg | float64 | Yes | Average dihedral angle (S1) |
| delta_dihedral | float64 | Yes | S1 - S0 dihedral |
| s0_charge_dipole | float64 | Yes | Dipole moment (S0) |
| s1_charge_dipole | float64 | Yes | Dipole moment (S1) |
| delta_dipole | float64 | Yes | S1 - S0 dipole |
| excitation_energy | float64 | Yes | Vertical excitation energy (eV) |

---

## 5. `data/atb_qc.parquet`

aTB run quality control / audit log.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| run_status | string | No | "success" / "failed" / "pending" |
| fail_stage | string | Yes | Stage where failure occurred |
| error_msg | string | Yes | Truncated error message (max 500 chars) |
| runtime_sec | float64 | Yes | Total runtime in seconds |
| atb_version | string | Yes | aTB pipeline version used |
| timestamp | string | No | ISO 8601 timestamp of run |

---

## 6. `data/X_full.parquet`

Merged feature matrix (experimental + RDKit + aTB).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| id | int64 | No | Representative original ID |
| *...all numeric columns from private_clean (emission, qy, tau)...* | float64 | Yes | Experimental observables |
| *...all columns from rdkit_features (except inchikey)...* | varies | Yes | RDKit descriptors |
| *...all columns from atb_features (except inchikey, run_status, fail_stage)...* | float64 | Yes | aTB descriptors |
| *..._missing columns...* | bool | No | Missing indicators |
| atb_available | bool | No | True if aTB ran successfully |

**Note**: Numeric features are z-score normalized. Scaler saved to `data/scaler.pkl`.

---

## 7. `data/uq_scores.parquet`

Uncertainty quantification scores and router decisions.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | **Primary key** |
| id | int64 | No | Representative original ID |
| coverage | float64 | No | Combined coverage score [0,1] |
| coverage_sim | float64 | No | Feature-space similarity to anchors [0,1] |
| coverage_meta | float64 | No | Metadata completeness [0,1] |
| novelty | float64 | No | Novelty/outlierness score [0,1] |
| aleatoric | float64 | No | Aleatoric uncertainty (prototype entropy) [0,1] |
| router_action | string | No | "Known/Stable" / "In-domain ambiguous" / "Evidence-insufficient" / "Novelty-candidate" |
| recommended_next_steps | string | Yes | JSON array of recommended actions |
| top_k_neighbors | string | No | JSON array of {inchikey, distance} for top-k anchors |

---

## 8. `data/hypothesis_log.parquet` (or `.jsonl`)

Log of novelty candidates with provenance.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| inchikey | string | No | Molecule InChIKey |
| id | int64 | No | Original ID |
| timestamp | string | No | ISO 8601 when logged |
| coverage | float64 | No | Coverage score |
| novelty | float64 | No | Novelty score |
| aleatoric | float64 | No | Aleatoric score |
| top_k_neighbors | string | No | JSON array of neighbor inchikeys + distances |
| key_descriptors | string | No | JSON object of notable descriptor values |
| recommended_next_steps | string | No | JSON array of suggested actions |
| hypothesis_status | string | No | "open" (initial) / "under_review" / "validated" / "refuted" |

---

## 9. `data/run_manifest.json`

Pipeline run metadata for reproducibility.

```json
{
  "run_id": "uuid",
  "timestamp": "ISO 8601",
  "git_commit": "hash or 'untracked'",
  "python_version": "3.x.x",
  "rdkit_version": "x.x.x",
  "atb_version": "x.x.x",
  "encoding_used": "utf-8",
  "n_molecules_input": 1226,
  "n_molecules_processed": ...,
  "config_snapshot": { ... }
}
```
