"""
src/cli.py

CLI for Uncertainty-aware AIE pipeline (Mode A orchestration).

Commands:
- fetch: Fetch record by id
- compute-atb: Check aTB cache and mark pending if missing
- run: Full orchestration (fetch + atb + report)
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, Any

from src.agents.data_agent import DataAgent
from src.agents.atb_agent import ATBAgent
from src.utils.logging import setup_logger

logger = setup_logger(__name__, level="INFO")

# STRICT ALLOWLIST: Fields safe to include in reports
# Excludes sensitive fields like "comment" which may contain private information
REPORT_FIELD_ALLOWLIST = [
    # Core identifiers
    "id", "code", "SMILES", "canonical_smiles", "inchikey",

    # Photophysical properties (emission/qy/tau × 4 conditions)
    "emission_sol", "emission_solid", "emission_aggr", "emission_crys",
    "qy_sol", "qy_solid", "qy_aggr", "qy_crys",
    "tau_sol", "tau_solid", "tau_aggr", "tau_crys",

    # Additional observables
    "absorption", "absorption_peak_nm", "tested_solvent",
    "color_in_powder", "molecular_weight",

    # Mechanism/feature IDs (NOT full text)
    "features_id", "mechanism_id", "AggIndex",

    # Stability metrics
    "photostability", "thermostability",

    # Solubility and pKa (numeric values only)
    "solubility_water", "solubility_dmso", "solubility_thf",
    "solubility_chloroform", "solubility_acetone", "pka",

    # Applications (categorical IDs)
    "application1", "application2", "application3", "application4",

    # Molar properties
    "molar_extinction", "molar_absorptivity",

    # Normalized/standardized fields
    "qy_sol_raw", "qy_solid_raw", "qy_aggr_raw", "qy_crys_raw",
    "tau_sol_raw", "tau_solid_raw", "tau_aggr_raw", "tau_crys_raw",
    "tau_sol_outlier", "tau_solid_outlier", "tau_aggr_outlier", "tau_crys_outlier",
    "tau_sol_log", "tau_solid_log", "tau_aggr_log", "tau_crys_log",
    "qy_unit_inferred", "qy_inferred_confidence",

    # Missing indicators
    "emission_sol_missing", "emission_solid_missing", "emission_aggr_missing", "emission_crys_missing",
    "qy_sol_missing", "qy_solid_missing", "qy_aggr_missing", "qy_crys_missing",
    "tau_sol_missing", "tau_solid_missing", "tau_aggr_missing", "tau_crys_missing",
    "absorption_missing", "tested_solvent_missing",
]

# BLOCKED FIELDS: Never include in reports (privacy/sensitivity)
REPORT_FIELD_BLOCKLIST = [
    "comment",  # May contain sensitive researcher notes or private information
]


def filter_record_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter record fields according to allowlist/blocklist.

    Args:
        record: Full record dictionary

    Returns:
        Filtered dictionary with only allowed fields
    """
    filtered = {}
    for key in REPORT_FIELD_ALLOWLIST:
        if key in record:
            filtered[key] = record[key]

    # Double-check blocklist (should already be excluded by allowlist)
    for blocked_key in REPORT_FIELD_BLOCKLIST:
        if blocked_key in filtered:
            logger.warning(f"Blocked field '{blocked_key}' found in filtered record, removing")
            del filtered[blocked_key]

    return filtered


def fetch_command(args):
    """Fetch record by id and display."""
    try:
        agent = DataAgent(data_dir=args.data_dir)
        record = agent.get_record_by_id(args.id)

        # Print formatted output
        if args.json:
            print(json.dumps(record, indent=2))
        else:
            print(f"Record id={args.id}")
            print(f"  InChIKey: {record.get('inchikey', 'N/A')}")
            print(f"  SMILES: {record.get('canonical_smiles', 'N/A')}")
            print(f"  Emission (sol): {record.get('emission_sol', 'N/A')}")
            print(f"  QY (sol): {record.get('qy_sol', 'N/A')}")
            print(f"  Tau (sol): {record.get('tau_sol', 'N/A')}")

    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to fetch record: {e}")
        sys.exit(1)


def compute_atb_command(args):
    """Check aTB cache and mark pending if missing."""
    try:
        # Fetch record to get InChIKey
        data_agent = DataAgent(data_dir=args.data_dir)
        record = data_agent.get_record_by_id(args.id)

        inchikey = record.get("inchikey")
        if not inchikey:
            logger.error(f"Record id={args.id} has no valid InChIKey (invalid SMILES)")
            sys.exit(1)

        # Check cache
        atb_agent = ATBAgent(cache_dir=args.cache_dir)
        cache_exists = atb_agent.check_cache(inchikey)

        if cache_exists:
            status = atb_agent.load_status(inchikey)
            print(f"Cache HIT for {inchikey}")
            print(f"  Status: {status.get('run_status', 'unknown')}")
            if status.get("fail_stage"):
                print(f"  Failed at: {status['fail_stage']}")
        else:
            print(f"Cache MISS for {inchikey}")
            print(f"  Marking as pending (no real aTB computation in Mode A)")
            smiles = record.get("canonical_smiles")
            status_file = atb_agent.mark_pending(inchikey, smiles)
            print(f"  Created: {status_file}")

    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to check aTB cache: {e}")
        sys.exit(1)


def run_command(args):
    """Full orchestration: fetch + atb + assemble + report."""
    try:
        # Step 1: Fetch record
        logger.info(f"[1/4] Fetching record id={args.id}")
        data_agent = DataAgent(data_dir=args.data_dir)
        record = data_agent.get_record_by_id(args.id)

        inchikey = record.get("inchikey")
        smiles = record.get("canonical_smiles")

        if not inchikey:
            logger.error(f"Record id={args.id} has no valid InChIKey (invalid SMILES)")
            sys.exit(1)

        # Step 2: Get missing summary
        logger.info("[2/4] Computing missing value summary")
        missing_summary = data_agent.get_missing_summary(record)

        # Step 3: Check aTB cache
        logger.info("[3/4] Checking aTB cache")
        atb_agent = ATBAgent(cache_dir=args.cache_dir)
        cache_summary = atb_agent.get_cache_summary(inchikey)

        atb_status = "miss"
        atb_features = None

        if cache_summary["cache_exists"]:
            atb_status = "hit"
            run_status = cache_summary.get("run_status")

            if run_status == "success" and cache_summary["features_available"]:
                atb_features = atb_agent.load_features(inchikey)
            elif run_status == "pending":
                atb_status = "pending"
            elif run_status == "failed":
                atb_status = "failed"
        else:
            # Mark as pending
            logger.info(f"Cache miss, marking {inchikey} as pending")
            atb_agent.mark_pending(inchikey, smiles)
            atb_status = "pending"

        # Step 4: Assemble output
        logger.info("[4/4] Assembling output")

        # Filter record fields using strict allowlist (excludes sensitive fields like "comment")
        filtered_fields = filter_record_fields(record)

        output = {
            "id": args.id,
            "inchikey": inchikey,
            "canonical_smiles": smiles,
            "record_fields": filtered_fields,
            "missing_summary": missing_summary,
            "atb_status": atb_status,
            "atb_features": atb_features,
            "paths": {
                "cache_dir": cache_summary["cache_path"],
                "status_file": cache_summary["status_file"],
                "features_file": cache_summary.get("features_file"),
                "report_path": f"reports/{args.id}.json"
            }
        }

        # Write to report file if requested
        if args.write_report:
            reports_dir = Path("reports")
            reports_dir.mkdir(exist_ok=True)
            report_path = reports_dir / f"{args.id}.json"

            with open(report_path, "w") as f:
                json.dump(output, f, indent=2)

            logger.info(f"Wrote report to {report_path}")

        # Print to stdout
        print(json.dumps(output, indent=2))

    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to run orchestration: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Uncertainty-aware AIE Pipeline CLI (Mode A)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.cli fetch --id 1
  python -m src.cli compute-atb --id 1
  python -m src.cli run --id 1 --write-report
        """
    )

    # Global arguments
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Data directory (default: data)"
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/atb",
        help="aTB cache directory (default: cache/atb)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch record by id")
    fetch_parser.add_argument("--id", type=int, required=True, help="Record id")
    fetch_parser.add_argument("--json", action="store_true", help="Output full JSON")
    fetch_parser.set_defaults(func=fetch_command)

    # compute-atb command
    compute_parser = subparsers.add_parser("compute-atb", help="Check aTB cache and mark pending")
    compute_parser.add_argument("--id", type=int, required=True, help="Record id")
    compute_parser.set_defaults(func=compute_atb_command)

    # run command
    run_parser = subparsers.add_parser("run", help="Full orchestration (fetch + atb + report)")
    run_parser.add_argument("--id", type=int, required=True, help="Record id")
    run_parser.add_argument("--write-report", action="store_true", help="Write report to reports/{id}.json")
    run_parser.set_defaults(func=run_command)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run command
    args.func(args)


if __name__ == "__main__":
    main()
