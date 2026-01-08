"""
src/data/loader.py

CSV loader with encoding fallback for the private AIE dataset.
"""

import pandas as pd
from typing import Tuple
from pathlib import Path

from src.utils.logging import get_logger

logger = get_logger(__name__)


def load_csv_with_fallback(csv_path: str) -> Tuple[pd.DataFrame, str]:
    """
    Load CSV with encoding fallback chain.

    Tries encodings in order: utf-8-sig → utf-8 → gb18030 → latin1

    Args:
        csv_path: Path to CSV file

    Returns:
        Tuple of (DataFrame, encoding_used)

    Raises:
        ValueError: If all encodings fail
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    encodings = ["utf-8-sig", "utf-8", "gb18030", "latin1"]

    for encoding in encodings:
        try:
            logger.info(f"Attempting to load {csv_path.name} with encoding: {encoding}")
            df = pd.read_csv(csv_path, encoding=encoding)
            logger.info(f"Successfully loaded with {encoding} encoding. Shape: {df.shape}")
            return df, encoding
        except (UnicodeDecodeError, UnicodeError) as e:
            logger.warning(f"Failed with {encoding}: {e}")
            continue
        except Exception as e:
            # Non-encoding errors should fail immediately
            logger.error(f"Unexpected error with {encoding}: {e}")
            raise

    # All encodings failed
    raise ValueError(
        f"Failed to load {csv_path} with any of the following encodings: {encodings}"
    )


def load_private_dataset(csv_path: str = "data/data.csv") -> Tuple[pd.DataFrame, str]:
    """
    Load the private AIE dataset with encoding fallback.

    Args:
        csv_path: Path to CSV file (default: data/data.csv)

    Returns:
        Tuple of (DataFrame, encoding_used)
    """
    logger.info(f"Loading private dataset from {csv_path}")
    df, encoding = load_csv_with_fallback(csv_path)

    # Basic validation
    logger.info(f"Dataset loaded: {len(df)} rows, {len(df.columns)} columns")
    logger.info(f"Columns: {list(df.columns)}")

    return df, encoding
