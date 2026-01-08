"""
src/agents/atb_agent.py

aTB Agent: Cache management and status tracking for aTB computations.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from src.utils.logging import get_logger

logger = get_logger(__name__)


class ATBAgent:
    """Agent for aTB cache management and status tracking."""

    def __init__(self, cache_dir: str = "cache/atb"):
        """
        Initialize ATBAgent.

        Args:
            cache_dir: Base directory for aTB cache (default: "cache/atb")
        """
        self.cache_dir = Path(cache_dir)

    def get_cache_path(self, inchikey: str) -> Path:
        """
        Get cache directory path for a given InChIKey.

        Uses 2-character prefix for filesystem efficiency:
        cache/atb/{inchikey[:2]}/{inchikey}/

        Args:
            inchikey: InChIKey string

        Returns:
            Path to cache directory
        """
        if not inchikey or len(inchikey) < 2:
            raise ValueError(f"Invalid InChIKey: {inchikey}")

        prefix = inchikey[:2]
        cache_path = self.cache_dir / prefix / inchikey
        return cache_path

    def check_cache(self, inchikey: str) -> bool:
        """
        Check if cache exists for a given InChIKey.

        Cache is considered to exist if status.json file is present.

        Args:
            inchikey: InChIKey string

        Returns:
            True if cache exists, False otherwise
        """
        cache_path = self.get_cache_path(inchikey)
        status_file = cache_path / "status.json"
        exists = status_file.exists()

        logger.debug(f"Cache check for {inchikey}: {'hit' if exists else 'miss'}")
        return exists

    def load_status(self, inchikey: str) -> Optional[Dict[str, Any]]:
        """
        Load status.json from cache.

        Args:
            inchikey: InChIKey string

        Returns:
            Status dictionary, or None if not found

        Raises:
            FileNotFoundError: If cache doesn't exist
            json.JSONDecodeError: If status.json is malformed
        """
        cache_path = self.get_cache_path(inchikey)
        status_file = cache_path / "status.json"

        if not status_file.exists():
            raise FileNotFoundError(f"status.json not found for {inchikey} at {status_file}")

        with open(status_file, "r") as f:
            status = json.load(f)

        logger.info(f"Loaded status for {inchikey}: run_status={status.get('run_status', 'unknown')}")
        return status

    def mark_pending(self, inchikey: str, smiles: Optional[str] = None) -> Path:
        """
        Create placeholder status.json with run_status="pending".

        This is used when cache doesn't exist yet but we want to mark
        the molecule for future computation.

        Adheres strictly to status.json schema from doc/process.md:
        - inchikey, run_status, fail_stage, error_msg, timestamp, atb_version, runtime_sec

        Args:
            inchikey: InChIKey string
            smiles: Optional SMILES string (stored separately if needed)

        Returns:
            Path to created status.json
        """
        cache_path = self.get_cache_path(inchikey)
        cache_path.mkdir(parents=True, exist_ok=True)

        status_file = cache_path / "status.json"

        # Create placeholder status - STRICT SCHEMA COMPLIANCE
        # Only include fields defined in doc/process.md P2 status.json schema
        status = {
            "inchikey": inchikey,
            "run_status": "pending",
            "fail_stage": None,
            "error_msg": None,
            "timestamp": datetime.now().isoformat(),
            "atb_version": None,
            "runtime_sec": None
        }

        with open(status_file, "w") as f:
            json.dump(status, f, indent=2)

        # Optionally store SMILES separately for reference (not in status.json schema)
        if smiles:
            smiles_file = cache_path / "canonical_smiles.txt"
            with open(smiles_file, "w") as f:
                f.write(smiles)

        logger.info(f"Created pending status for {inchikey} at {status_file}")
        return status_file

    def load_features(self, inchikey: str) -> Optional[Dict[str, Any]]:
        """
        Load features.json from cache if available.

        Args:
            inchikey: InChIKey string

        Returns:
            Features dictionary, or None if not found
        """
        cache_path = self.get_cache_path(inchikey)
        features_file = cache_path / "features.json"

        if not features_file.exists():
            logger.debug(f"features.json not found for {inchikey}")
            return None

        with open(features_file, "r") as f:
            features = json.load(f)

        logger.info(f"Loaded features for {inchikey}")
        return features

    def get_cache_summary(self, inchikey: str) -> Dict[str, Any]:
        """
        Get summary of cache state for a molecule.

        Args:
            inchikey: InChIKey string

        Returns:
            Dictionary with cache status, paths, and available files
        """
        cache_path = self.get_cache_path(inchikey)
        status_file = cache_path / "status.json"
        features_file = cache_path / "features.json"

        cache_exists = status_file.exists()
        status = None
        features_available = False

        if cache_exists:
            try:
                status = self.load_status(inchikey)
                features_available = features_file.exists()
            except Exception as e:
                logger.warning(f"Failed to load status for {inchikey}: {e}")

        return {
            "cache_exists": cache_exists,
            "cache_path": str(cache_path),
            "status_file": str(status_file),
            "features_file": str(features_file) if features_available else None,
            "run_status": status.get("run_status") if status else None,
            "fail_stage": status.get("fail_stage") if status else None,
            "features_available": features_available
        }


# Convenience functions for standalone usage
def get_cache_path(inchikey: str, cache_dir: str = "cache/atb") -> Path:
    """Get cache path for InChIKey (convenience function)."""
    agent = ATBAgent(cache_dir=cache_dir)
    return agent.get_cache_path(inchikey)


def check_cache(inchikey: str, cache_dir: str = "cache/atb") -> bool:
    """Check if cache exists (convenience function)."""
    agent = ATBAgent(cache_dir=cache_dir)
    return agent.check_cache(inchikey)


def mark_pending(inchikey: str, smiles: Optional[str] = None, cache_dir: str = "cache/atb") -> Path:
    """Mark molecule as pending (convenience function)."""
    agent = ATBAgent(cache_dir=cache_dir)
    return agent.mark_pending(inchikey, smiles)
