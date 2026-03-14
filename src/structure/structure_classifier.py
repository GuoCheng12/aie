"""Training and inference helpers for StructureAgent candidate distributions."""

from __future__ import annotations

import copy
import json
import pickle
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MaxAbsScaler
from sklearn.utils.class_weight import compute_class_weight

from src.data.rdkit_descriptors import compute_basic_descriptors
from src.eval.label_normalizer import CANONICAL_LABELS, normalize_label
from src.reasoning.structure_prior_profile import compute_structure_prior_profile
from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count, count_tanimoto
from src.structure.motif_detector import detect_structure_motifs
from src.structure.scaffold_retrieval import extract_murcko_scaffold


@dataclass
class StructureClassifierBundle:
    model: Any
    vectorizer: DictVectorizer
    labels: List[str]
    calibration_report: Dict[str, Any]
    feature_spec: Dict[str, Any]
    scaler: Any = None
    training_history: List[Dict[str, Any]] = field(default_factory=list)


DEFAULT_MODEL_ROOT = Path("artifacts/structure_agent/latest")
MODEL_FILENAME = "model.pkl"
FEATURE_SPEC_FILENAME = "feature_spec.json"
LABEL_MAP_FILENAME = "label_map.json"
CALIBRATION_FILENAME = "calibration_report.json"
TRAIN_HISTORY_FILENAME = "train_history.json"


def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except Exception:
        return 0.0
    if out != out:
        return 0.0
    return out


def _normalize_mechanism_label(raw: Any) -> str:
    return normalize_label(str(raw or "").strip())


def _maybe_parse_json_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if isinstance(value, str):
        txt = value.strip()
        if not txt:
            return {}
        try:
            parsed = json.loads(txt)
            if not isinstance(parsed, dict):
                return {}
            normalized: Dict[Any, Any] = {}
            for key, val in parsed.items():
                try:
                    normalized[int(key)] = val
                except Exception:
                    normalized[key] = val
            return normalized
        except Exception:
            return {}
    return {}


def _top_rows(label_probs: Mapping[str, float], topk: int) -> List[Dict[str, float]]:
    return [
        {"label": label, "prob": prob}
        for label, prob in sorted(label_probs.items(), key=lambda kv: kv[1], reverse=True)
        if prob > 0
    ][: max(1, int(topk))]


def _predict_proba_for_labels(model: Any, X, labels: List[str]) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        probs = np.asarray(model.predict_proba(X), dtype=float)
        probs = np.nan_to_num(probs, nan=0.0, posinf=0.0, neginf=0.0)
        model_labels = [str(label) for label in getattr(model, "classes_", labels)]
        if model_labels == labels:
            row_sums = probs.sum(axis=1, keepdims=True)
            row_sums[row_sums <= 0.0] = 1.0
            return probs / row_sums
        arr = np.zeros((X.shape[0], len(labels)), dtype=float)
        for idx, label in enumerate(labels):
            if label in model_labels:
                arr[:, idx] = probs[:, model_labels.index(label)]
        row_sums = arr.sum(axis=1, keepdims=True)
        row_sums[row_sums <= 0.0] = 1.0
        return arr / row_sums
    preds = [str(label) for label in model.predict(X)]
    arr = np.zeros((X.shape[0], len(labels)), dtype=float)
    for row_idx, pred in enumerate(preds):
        if pred in labels:
            arr[row_idx, labels.index(pred)] = 1.0
    return arr


def _safe_log_loss(y_true: List[str], probs: np.ndarray, labels: List[str]) -> float:
    try:
        clipped = np.clip(np.asarray(probs, dtype=float), 1e-7, 1.0 - 1e-7)
        clipped /= clipped.sum(axis=1, keepdims=True)
        return float(log_loss(y_true, clipped, labels=labels))
    except Exception:
        return float("nan")


def _transform_for_model(bundle_or_scaler: Any, X):
    scaler = bundle_or_scaler if isinstance(bundle_or_scaler, MaxAbsScaler) else getattr(bundle_or_scaler, "scaler", None)
    return scaler.transform(X) if scaler is not None else X


def _split_train_valid_indices(
    y: List[str],
    *,
    validation_fraction: float,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray]:
    indices = np.arange(len(y))
    label_counts = Counter(y)
    can_stratify = (
        len(label_counts) >= 2
        and min(label_counts.values()) >= 2
        and len(indices) >= max(10, 2 * len(label_counts))
        and 0.0 < float(validation_fraction) < 0.5
    )
    if not can_stratify:
        return indices, np.array([], dtype=int)
    train_idx, valid_idx = train_test_split(
        indices,
        test_size=float(validation_fraction),
        random_state=int(random_state),
        stratify=y,
    )
    return np.asarray(train_idx, dtype=int), np.asarray(valid_idx, dtype=int)


def featurize_structure_record(record: Mapping[str, Any]) -> Dict[str, float]:
    features: Dict[str, float] = {}
    desc = record.get("descriptor_snapshot") if isinstance(record.get("descriptor_snapshot"), dict) else None
    if not isinstance(desc, dict):
        desc = compute_basic_descriptors(str(record.get("canonical_smiles") or ""))
    for key, value in desc.items():
        if value is None:
            continue
        features[f"desc:{key}"] = _safe_float(value)

    for bit, count in (record.get("morgan_count") or {}).items():
        features[f"morgan:{int(bit)}"] = float(count)
    for bit, count in (record.get("feature_morgan_count") or {}).items():
        features[f"fmorgan:{int(bit)}"] = float(count)

    motif = record.get("structure_motif_profile") or {}
    if isinstance(motif, dict):
        for key in (
            "donor_sites",
            "acceptor_sites",
            "possible_intramolecular_hbond_pairs",
            "tautomerizable_motif_candidates",
            "fused_aromatic_core",
        ):
            if key in motif:
                value = motif.get(key)
                if isinstance(value, bool):
                    features[f"motif:{key}"] = 1.0 if value else 0.0
                else:
                    features[f"motif:{key}"] = _safe_float(value)
        for key in (
            "intramolecular_hbond_motif",
            "tautomerizable_motif",
            "donor_acceptor_path_strength",
            "aromatic_scaffold_type",
            "flexibility_regime",
            "motif_density",
            "conjugation_span_bucket",
        ):
            val = str(motif.get(key) or "unknown")
            features[f"motifcat:{key}={val}"] = 1.0

    prior = record.get("structure_prior_profile") or {}
    if isinstance(prior, dict):
        for key in (
            "donor_acceptor_topology",
            "intramolecular_hbond_candidates",
            "aromatic_core_density",
            "flexibility_proxy",
            "conjugation_proxy",
        ):
            val = str(prior.get(key) or "unknown")
            features[f"prior:{key}={val}"] = 1.0

    scaffold_info = record.get("scaffold_info") or {}
    scaffold = str((scaffold_info.get("generic_scaffold_smiles") or scaffold_info.get("murcko_scaffold_smiles") or "")[:120])
    if scaffold:
        features[f"scaffold:{scaffold}"] = 1.0
    return features


def build_reference_rows(
    *,
    data_dir: str | Path = "data",
) -> List[Dict[str, Any]]:
    data_root = Path(data_dir)
    pool_path = data_root / "structure_reference_pool_main_prior.parquet"
    if not pool_path.exists():
        pool_path = data_root / "structure_reference_pool.parquet"
    if pool_path.exists():
        pool_df = pd.read_parquet(pool_path)
        rows: List[Dict[str, Any]] = []
        for rec in pool_df.to_dict(orient="records"):
            smiles = str(rec.get("canonical_smiles") or "").strip()
            if not smiles:
                continue
            scaffold_info = {
                "murcko_scaffold_smiles": rec.get("murcko_scaffold_smiles"),
                "generic_scaffold_smiles": rec.get("generic_scaffold_smiles"),
            }
            rows.append(
                {
                    "inchikey": rec.get("inchikey"),
                    "canonical_smiles": smiles,
                    "mechanism_label": _normalize_mechanism_label(rec.get("mechanism_label")),
                    "descriptor_snapshot": _maybe_parse_json_mapping(rec.get("descriptor_snapshot")),
                    "morgan_count": _maybe_parse_json_mapping(rec.get("morgan_count")),
                    "feature_morgan_count": _maybe_parse_json_mapping(rec.get("feature_morgan_count")),
                    "scaffold_info": scaffold_info,
                    "murcko_scaffold_smiles": scaffold_info.get("murcko_scaffold_smiles"),
                    "generic_scaffold_smiles": scaffold_info.get("generic_scaffold_smiles"),
                    "scaffold_feature_morgan_count": compute_feature_morgan_count(
                        scaffold_info.get("generic_scaffold_smiles")
                        or scaffold_info.get("murcko_scaffold_smiles")
                        or smiles
                    ),
                    "structure_motif_profile": _maybe_parse_json_mapping(rec.get("structure_motif_profile")),
                    "structure_prior_profile": _maybe_parse_json_mapping(rec.get("structure_prior_profile")),
                }
            )
        if rows:
            return rows

    molecule_df = pd.read_parquet(data_root / "molecule_table.parquet")
    label_df = pd.read_parquet(data_root / "mechanism_label_map.parquet")[["inchikey", "mechanism_label"]]
    desc_df = pd.read_parquet(data_root / "rdkit_features.parquet")

    label_df = label_df[label_df["inchikey"].astype(str).str.strip() != ""]
    merged = molecule_df.merge(label_df, on="inchikey", how="left").merge(desc_df, on="inchikey", how="left", suffixes=("", "_desc"))

    rows: List[Dict[str, Any]] = []
    for row in merged.to_dict(orient="records"):
        smiles = str(row.get("canonical_smiles") or "").strip()
        if not smiles:
            continue
        label = _normalize_mechanism_label(row.get("mechanism_label"))
        descriptors = {
            "mw": row.get("mw"),
            "logp": row.get("logp"),
            "tpsa": row.get("tpsa"),
            "n_rotatable_bonds": row.get("n_rotatable_bonds"),
            "n_hbd": row.get("n_hbd"),
            "n_hba": row.get("n_hba"),
            "n_rings": row.get("n_rings"),
            "n_aromatic_rings": row.get("n_aromatic_rings"),
            "n_heavy_atoms": row.get("n_heavy_atoms"),
        }
        scaffold_info = extract_murcko_scaffold(smiles)
        motif_profile = detect_structure_motifs(smiles, descriptors)
        structure_prior_profile = compute_structure_prior_profile(smiles, descriptors)
        rows.append(
            {
                "inchikey": row.get("inchikey"),
                "canonical_smiles": smiles,
                "mechanism_label": label,
                "code": row.get("code"),
                "descriptor_snapshot": descriptors,
                "morgan_count": compute_morgan_count(smiles),
                "feature_morgan_count": compute_feature_morgan_count(smiles),
                "scaffold_info": scaffold_info,
                "murcko_scaffold_smiles": scaffold_info.get("murcko_scaffold_smiles"),
                "generic_scaffold_smiles": scaffold_info.get("generic_scaffold_smiles"),
                "scaffold_feature_morgan_count": compute_feature_morgan_count(scaffold_info.get("generic_scaffold_smiles") or scaffold_info.get("murcko_scaffold_smiles") or smiles),
                "structure_motif_profile": motif_profile,
                "structure_prior_profile": structure_prior_profile,
            }
        )
    return rows


def train_structure_classifier(
    *,
    data_dir: str | Path = "data",
    outdir: str | Path = DEFAULT_MODEL_ROOT,
    labels: Optional[List[str]] = None,
    calibration_method: str = "sigmoid",
    epochs: int = 60,
    validation_fraction: float = 0.2,
    random_state: int = 42,
    batch_size: int = 64,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    allowed_labels = list(labels or CANONICAL_LABELS)
    rows = build_reference_rows(data_dir=data_dir)
    rows = [row for row in rows if row.get("mechanism_label") in allowed_labels]
    if not rows:
        raise ValueError("no_training_rows")

    X_dict = [featurize_structure_record(row) for row in rows]
    y = [str(row.get("mechanism_label") or "unknown") for row in rows]

    vectorizer = DictVectorizer(sparse=True)
    X = vectorizer.fit_transform(X_dict)
    labels_sorted = sorted(set(y))
    train_idx, valid_idx = _split_train_valid_indices(
        y,
        validation_fraction=validation_fraction,
        random_state=random_state,
    )
    X_train = X[train_idx]
    y_train = [y[idx] for idx in train_idx]
    X_valid = X[valid_idx] if len(valid_idx) else None
    y_valid = [y[idx] for idx in valid_idx] if len(valid_idx) else []

    scaler = MaxAbsScaler(copy=True)
    X_train_scaled = scaler.fit_transform(X_train)
    X_valid_scaled = scaler.transform(X_valid) if X_valid is not None else None

    base_model = SGDClassifier(
        loss="log_loss",
        penalty="l2",
        alpha=1e-5,
        learning_rate="constant",
        eta0=0.01,
        max_iter=1,
        tol=None,
        random_state=random_state,
        average=True,
    )
    class_weight_values = compute_class_weight(
        class_weight="balanced",
        classes=np.asarray(labels_sorted),
        y=np.asarray(y_train),
    )
    base_model.set_params(
        class_weight={label: float(weight) for label, weight in zip(labels_sorted, class_weight_values)}
    )
    training_history: List[Dict[str, Any]] = []
    rng = np.random.default_rng(random_state)
    best_model = None
    best_loss = None
    batch_size = max(8, int(batch_size))
    for epoch in range(1, max(1, int(epochs)) + 1):
        order = rng.permutation(X_train_scaled.shape[0])
        first_batch = epoch == 1
        for start in range(0, X_train_scaled.shape[0], batch_size):
            batch_indices = order[start : start + batch_size]
            batch_x = X_train_scaled[batch_indices]
            batch_y = [y_train[idx] for idx in batch_indices]
            if first_batch:
                base_model.partial_fit(batch_x, batch_y, classes=np.asarray(labels_sorted))
                first_batch = False
            else:
                base_model.partial_fit(batch_x, batch_y)

        train_probs = _predict_proba_for_labels(base_model, X_train_scaled, labels_sorted)
        train_loss = _safe_log_loss(y_train, train_probs, labels_sorted)
        train_accuracy = float(accuracy_score(y_train, base_model.predict(X_train_scaled)))
        row: Dict[str, Any] = {
            "epoch": epoch,
            "train_loss": None if np.isnan(train_loss) else round(train_loss, 6),
            "train_accuracy": round(train_accuracy, 6),
        }
        if X_valid_scaled is not None and len(y_valid) > 0:
            valid_probs = _predict_proba_for_labels(base_model, X_valid_scaled, labels_sorted)
            valid_loss = _safe_log_loss(y_valid, valid_probs, labels_sorted)
            valid_accuracy = float(accuracy_score(y_valid, base_model.predict(X_valid_scaled)))
            row["valid_loss"] = None if np.isnan(valid_loss) else round(valid_loss, 6)
            row["valid_accuracy"] = round(valid_accuracy, 6)
        else:
            row["valid_loss"] = None
            row["valid_accuracy"] = None
        training_history.append(row)
        if progress_callback is not None:
            progress_callback(dict(row))

        score_value = row.get("valid_loss")
        if score_value is None:
            score_value = row.get("train_loss")
        if score_value is not None and (best_loss is None or float(score_value) < float(best_loss)):
            best_loss = float(score_value)
            best_model = copy.deepcopy(base_model)

    if best_model is None:
        best_model = base_model

    can_calibrate = X_valid is not None and len(y_valid) >= 2 and len(set(y_valid)) >= 2
    if can_calibrate:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message="The `cv='prefit'` option is deprecated",
                    category=FutureWarning,
                )
                calibrator = CalibratedClassifierCV(
                    estimator=FrozenEstimator(best_model),
                    method=calibration_method,
                    cv="prefit",
                )
                calibrator.fit(X_valid_scaled, y_valid)
            model: Any = calibrator
            calibration_method_used = calibration_method
            calibration_reliability = "high"
            calibration_notes = "held_out_validation"
        except Exception as exc:
            model = best_model
            calibration_method_used = "none"
            calibration_reliability = "medium"
            calibration_notes = f"calibration_failed:{exc.__class__.__name__}"
    else:
        model = best_model
        calibration_method_used = "none"
        calibration_reliability = "medium"
        calibration_notes = "uncalibrated"

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    best_epoch = None
    if training_history:
        score_rows = [row for row in training_history if row.get("valid_loss") is not None] or training_history
        best_epoch = min(
            score_rows,
            key=lambda row: float(row.get("valid_loss") if row.get("valid_loss") is not None else row.get("train_loss") or 1e9),
        ).get("epoch")
    bundle = StructureClassifierBundle(
        model=model,
        vectorizer=vectorizer,
        labels=labels_sorted,
        calibration_report={
            "method": calibration_method_used,
            "reliability": calibration_reliability,
            "train_rows": len(rows),
            "fit_rows": len(train_idx),
            "validation_rows": len(valid_idx),
            "classes": labels_sorted,
            "notes": calibration_notes,
        },
        feature_spec={
            "morgan_bits": 2048,
            "feature_morgan_bits": 2048,
            "uses_scaffold_feature": True,
            "uses_motif_features": True,
            "scaled_with": "max_abs",
        },
        scaler=scaler,
        training_history=training_history,
    )
    with open(out / MODEL_FILENAME, "wb") as fh:
        pickle.dump(bundle, fh)
    (out / FEATURE_SPEC_FILENAME).write_text(json.dumps(bundle.feature_spec, indent=2, sort_keys=True), encoding="utf-8")
    (out / LABEL_MAP_FILENAME).write_text(json.dumps({"labels": bundle.labels}, indent=2, sort_keys=True), encoding="utf-8")
    (out / CALIBRATION_FILENAME).write_text(json.dumps(bundle.calibration_report, indent=2, sort_keys=True), encoding="utf-8")
    (out / TRAIN_HISTORY_FILENAME).write_text(json.dumps(training_history, indent=2), encoding="utf-8")
    return {
        "outdir": str(out),
        "train_rows": len(rows),
        "labels": bundle.labels,
        "calibration": bundle.calibration_report,
        "epochs": max(1, int(epochs)),
        "validation_fraction": float(validation_fraction),
        "training_history": training_history,
        "best_epoch": best_epoch,
        "batch_size": batch_size,
    }


def load_structure_classifier(model_root: str | Path = DEFAULT_MODEL_ROOT) -> Optional[StructureClassifierBundle]:
    root = Path(model_root)
    model_path = root / MODEL_FILENAME
    if not model_path.exists():
        return None
    with open(model_path, "rb") as fh:
        bundle = pickle.load(fh)
    return bundle


def predict_structure_candidate_distribution(
    bundle: StructureClassifierBundle,
    record: Mapping[str, Any],
    *,
    topk: int = 5,
    allowed_labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    labels = list(allowed_labels or CANONICAL_LABELS)
    X = bundle.vectorizer.transform([featurize_structure_record(record)])
    X = _transform_for_model(bundle, X)
    probs_arr = _predict_proba_for_labels(bundle.model, X, labels)[0]
    probs = {str(label): float(prob) for label, prob in zip(labels, probs_arr)}
    label_probs = {label: round(float(probs.get(label, 0.0)), 6) for label in labels}
    total = sum(label_probs.values())
    if total > 0:
        label_probs = {k: round(v / total, 6) for k, v in label_probs.items()}
    top_rows = _top_rows(label_probs, topk)
    return {
        "version": "structure_candidate_dist_v1",
        "label_probs": label_probs,
        "top_candidates": top_rows,
        "top3": top_rows[:3],
        "calibration": {
            "method": str(bundle.calibration_report.get("method") or "unknown"),
            "reliability": str(
                bundle.calibration_report.get("reliability")
                or ("high" if bundle.calibration_report.get("method") not in {None, "none"} else "medium")
            ),
        },
    }


def retrieval_fallback_candidate_distribution(
    *,
    feature_neighbors: List[Mapping[str, Any]],
    scaffold_neighbors: List[Mapping[str, Any]],
    allowed_labels: Optional[List[str]] = None,
    topk: int = 5,
) -> Dict[str, Any]:
    labels = list(allowed_labels or CANONICAL_LABELS)
    scores = {label: 0.0 for label in labels}
    for row in feature_neighbors:
        label = _normalize_mechanism_label(row.get("mechanism_label"))
        scores[label] = scores.get(label, 0.0) + 0.6 * float(row.get("sim") or 0.0)
    for row in scaffold_neighbors:
        label = _normalize_mechanism_label(row.get("mechanism_label"))
        scores[label] = scores.get(label, 0.0) + 0.4 * float(row.get("sim") or 0.0)
    total = sum(scores.values())
    if total <= 0.0:
        scores = {label: (1.0 if label == "unknown" else 0.0) for label in labels}
        total = 1.0
    label_probs = {label: round(float(scores.get(label, 0.0)) / total, 6) for label in labels}
    top_rows = _top_rows(label_probs, topk)
    return {
        "version": "structure_candidate_dist_v1",
        "label_probs": label_probs,
        "top_candidates": top_rows,
        "top3": top_rows[:3],
        "calibration": {
            "method": "retrieval_fallback",
            "reliability": "low",
        },
    }


def feature_neighbors(
    target_record: Mapping[str, Any],
    reference_rows: Iterable[Mapping[str, Any]],
    *,
    topk: int = 10,
) -> List[Dict[str, Any]]:
    target_inchikey = str(target_record.get("inchikey") or "").strip()
    target_fp = target_record.get("feature_morgan_count") or {}
    rows: List[Dict[str, Any]] = []
    for idx, row in enumerate(reference_rows):
        if target_inchikey and str(row.get("inchikey") or "").strip() == target_inchikey:
            continue
        sim = count_tanimoto(target_fp, row.get("feature_morgan_count") or {})
        if sim <= 0.0:
            continue
        rows.append(
            {
                "case_index": idx,
                "inchikey": row.get("inchikey"),
                "sim": round(sim, 6),
                "mechanism_label": _normalize_mechanism_label(row.get("mechanism_label")),
                "canonical_smiles": row.get("canonical_smiles"),
            }
        )
    rows.sort(key=lambda row: row["sim"], reverse=True)
    return rows[: max(1, int(topk))]


__all__ = [
    "CALIBRATION_FILENAME",
    "DEFAULT_MODEL_ROOT",
    "FEATURE_SPEC_FILENAME",
    "LABEL_MAP_FILENAME",
    "StructureClassifierBundle",
    "TRAIN_HISTORY_FILENAME",
    "build_reference_rows",
    "feature_neighbors",
    "featurize_structure_record",
    "load_structure_classifier",
    "predict_structure_candidate_distribution",
    "retrieval_fallback_candidate_distribution",
    "train_structure_classifier",
]
