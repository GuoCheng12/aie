"""
src/agents/data_agent.py

Data Agent: Fetch records and molecules from parquet files.
"""

import pandas as pd
from pathlib import Path
from typing import Optional, Dict, Any, List

from src.utils.logging import get_logger

logger = get_logger(__name__)


class DataAgent:
    """Agent for fetching data from standardized parquet files."""

    def __init__(self, data_dir: str = "data"):
        """
        Initialize DataAgent.

        Args:
            data_dir: Directory containing parquet files (default: "data")
        """
        self.data_dir = Path(data_dir)
        self._private_clean_df: Optional[pd.DataFrame] = None
        self._molecule_table_df: Optional[pd.DataFrame] = None

    def _load_private_clean(self) -> pd.DataFrame:
        """Load private_clean.parquet (cached)."""
        if self._private_clean_df is None:
            path = self.data_dir / "private_clean.parquet"
            if not path.exists():
                raise FileNotFoundError(f"private_clean.parquet not found at {path}")
            self._private_clean_df = pd.read_parquet(path)
            logger.debug(f"Loaded private_clean.parquet: {len(self._private_clean_df)} rows")
        return self._private_clean_df

    def _load_molecule_table(self) -> pd.DataFrame:
        """Load molecule_table.parquet (cached)."""
        if self._molecule_table_df is None:
            path = self.data_dir / "molecule_table.parquet"
            if not path.exists():
                raise FileNotFoundError(f"molecule_table.parquet not found at {path}")
            self._molecule_table_df = pd.read_parquet(path)
            logger.debug(f"Loaded molecule_table.parquet: {len(self._molecule_table_df)} molecules")
        return self._molecule_table_df

    def get_record_by_id(self, record_id: int) -> Dict[str, Any]:
        """
        Fetch a single record by id from private_clean.parquet.

        Args:
            record_id: The record id to fetch

        Returns:
            Dictionary with record data

        Raises:
            ValueError: If id not found
        """
        df = self._load_private_clean()

        # Filter by id
        mask = df["id"] == record_id
        if not mask.any():
            raise ValueError(f"Record with id={record_id} not found in private_clean.parquet")

        row = df[mask].iloc[0]

        # Convert to dict and handle NaN values
        record = row.to_dict()

        # Convert numpy types to native Python types for JSON serialization
        for key, value in record.items():
            if pd.isna(value):
                record[key] = None
            elif hasattr(value, "item"):  # numpy scalar
                record[key] = value.item()
            elif isinstance(value, list) and len(value) > 0 and hasattr(value[0], "item"):
                record[key] = [v.item() for v in value]

        logger.info(f"Fetched record id={record_id}, inchikey={record.get('inchikey', 'N/A')}")
        return record

    def get_molecule_by_inchikey(self, inchikey: str) -> Dict[str, Any]:
        """
        Fetch molecule data by InChIKey from molecule_table.parquet.

        Args:
            inchikey: The InChIKey to fetch

        Returns:
            Dictionary with molecule data (inchikey, canonical_smiles, id_list, n_records)

        Raises:
            ValueError: If InChIKey not found
        """
        df = self._load_molecule_table()

        # Filter by inchikey
        mask = df["inchikey"] == inchikey
        if not mask.any():
            raise ValueError(f"Molecule with inchikey={inchikey} not found in molecule_table.parquet")

        row = df[mask].iloc[0]
        molecule = row.to_dict()

        # Convert numpy types to native Python types
        for key, value in molecule.items():
            if pd.isna(value):
                molecule[key] = None
            elif hasattr(value, "item"):
                molecule[key] = value.item()
            elif isinstance(value, list) and len(value) > 0 and hasattr(value[0], "item"):
                molecule[key] = [v.item() for v in value]

        logger.info(f"Fetched molecule inchikey={inchikey}, {molecule.get('n_records', 0)} records")
        return molecule

    def get_missing_summary(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compute missing value summary for a record.

        Args:
            record: Record dictionary

        Returns:
            Dictionary with n_missing and missing_fields list
        """
        # Critical fields with {field}_missing indicators
        critical_fields = [
            "emission_sol", "emission_solid", "emission_aggr", "emission_crys",
            "qy_sol", "qy_solid", "qy_aggr", "qy_crys",
            "tau_sol", "tau_solid", "tau_aggr", "tau_crys",
            "absorption", "tested_solvent"
        ]

        missing_fields: List[str] = []
        for field in critical_fields:
            missing_col = f"{field}_missing"
            if missing_col in record and record[missing_col] is True:
                missing_fields.append(field)

        return {
            "n_missing": len(missing_fields),
            "missing_fields": missing_fields
        }


# Convenience functions for standalone usage
def get_record_by_id(record_id: int, data_dir: str = "data") -> Dict[str, Any]:
    """Fetch record by id (convenience function)."""
    agent = DataAgent(data_dir=data_dir)
    return agent.get_record_by_id(record_id)


def get_molecule_by_inchikey(inchikey: str, data_dir: str = "data") -> Dict[str, Any]:
    """Fetch molecule by InChIKey (convenience function)."""
    agent = DataAgent(data_dir=data_dir)
    return agent.get_molecule_by_inchikey(inchikey)
