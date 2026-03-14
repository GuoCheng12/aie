"""
src/uq/mechanism_label_map.py

Build molecule-level mechanism label map from record-level mechanism_id.

Rule: MODE of non-null mechanism_id per inchikey; ties -> "unknown"
"""

import argparse
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import numpy as np
from collections import Counter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def build_mechanism_label_map(
    private_clean: pd.DataFrame,
    *,
    source_view: str | None = None,
    allowed_labels: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """
    Build molecule-level mechanism label map from record-level data.
    
    For each inchikey:
    - Take MODE of non-null/non-empty mechanism_id
    - If all missing -> "unknown"
    - If tie (multiple modes with same count) -> "unknown"
    
    Returns DataFrame with columns:
        inchikey, mechanism_label, label_source, n_records, n_nonnull, is_tied
    """
    logger.info("Building mechanism label map...")
    
    results = []
    
    for inchikey, group in private_clean.groupby('inchikey'):
        n_records = len(group)
        
        # Get non-null mechanism_id values
        mech_ids = group['mechanism_id'].dropna()
        # Also filter out empty strings if any
        mech_ids = mech_ids[mech_ids.astype(str).str.strip() != '']
        n_nonnull = len(mech_ids)
        
        if n_nonnull == 0:
            # All missing
            mechanism_label = "unknown"
            label_source = "all_missing"
            is_tied = False
        else:
            # Count occurrences
            counter = Counter(mech_ids.astype(str).tolist())
            most_common = counter.most_common()
            
            # Check for tie
            max_count = most_common[0][1]
            modes = [label for label, count in most_common if count == max_count]
            
            if len(modes) > 1:
                # Tie - use "unknown"
                mechanism_label = "unknown"
                label_source = "tie"
                is_tied = True
            else:
                mechanism_label = modes[0]
                label_source = "mode"
                is_tied = False
        
        difficulty_levels: List[int] = []
        if "difficulty_level" in group.columns:
            raw_levels = pd.to_numeric(group["difficulty_level"], errors="coerce").dropna().astype(int).tolist()
            difficulty_levels = sorted(set(raw_levels))

        results.append({
            'inchikey': inchikey,
            'mechanism_label': mechanism_label,
            'label_source': label_source,
            'n_records': n_records,
            'n_nonnull': n_nonnull,
            'is_tied': is_tied,
            'difficulty_levels': difficulty_levels,
            'source_view': source_view,
        })
    
    label_map = pd.DataFrame(results)
    if allowed_labels is not None:
        allowed = {str(label).strip().lower() for label in allowed_labels if str(label).strip()}
        label_map = label_map[
            label_map["mechanism_label"].astype(str).str.strip().str.lower().isin(allowed)
        ].copy()
    
    # Log statistics
    logger.info(f"  Total molecules: {len(label_map)}")
    logger.info(f"  Labels from mode: {(label_map['label_source'] == 'mode').sum()}")
    logger.info(f"  Labels from tie: {(label_map['label_source'] == 'tie').sum()}")
    logger.info(f"  Labels from all_missing: {(label_map['label_source'] == 'all_missing').sum()}")
    
    # Label distribution
    label_counts = label_map['mechanism_label'].value_counts()
    logger.info(f"  Label distribution (top 10):")
    for label, count in label_counts.head(10).items():
        logger.info(f"    {label}: {count}")
    
    return label_map


def run_build_label_map(
    private_clean_path: str = "data/private_clean.parquet",
    output_path: str = "data/mechanism_label_map.parquet",
    source_view: str | None = None,
    allowed_labels: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Build and save mechanism label map."""
    
    logger.info("=" * 60)
    logger.info("Building Mechanism Label Map")
    logger.info("=" * 60)
    
    # Load data
    logger.info(f"Loading private_clean from {private_clean_path}")
    private_clean = pd.read_parquet(private_clean_path)
    logger.info(f"  Loaded {len(private_clean)} records")
    
    # Build label map
    label_map = build_mechanism_label_map(
        private_clean,
        source_view=source_view,
        allowed_labels=allowed_labels,
    )
    
    # Save
    logger.info(f"Saving label map to {output_path}")
    label_map.to_parquet(output_path, index=False)
    
    logger.info("=" * 60)
    logger.info("Done!")
    logger.info("=" * 60)
    
    return label_map


def main():
    parser = argparse.ArgumentParser(description="Build mechanism label map")
    parser.add_argument('--private-clean', type=str, default='data/private_clean.parquet',
                       help='Path to private_clean.parquet')
    parser.add_argument('--output', type=str, default='data/mechanism_label_map.parquet',
                       help='Output path')
    parser.add_argument('--source-view', type=str, default=None,
                       help='Optional source view name for audit fields')
    parser.add_argument(
        '--allowed-labels',
        type=str,
        default="",
        help='Optional comma-separated allowed labels; when set, other labels are excluded from the output map.',
    )
    
    args = parser.parse_args()
    allowed = [v.strip() for v in str(args.allowed_labels).split(",") if v.strip()]
    run_build_label_map(
        args.private_clean,
        args.output,
        source_view=args.source_view,
        allowed_labels=allowed or None,
    )


if __name__ == "__main__":
    main()
