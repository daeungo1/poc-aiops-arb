"""합성 evidence만 사용하여 coverage spike gate report를 생성한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from enterprise.coverage import build_coverage_report, write_coverage_reports  # noqa: E402
from enterprise.registry import ControlRegistry  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the deterministic coverage spike gate report.")
    parser.add_argument(
        "--checklist",
        default="experiments/coverage_spike/checklists/azure_storage_production_readiness.yaml",
    )
    parser.add_argument(
        "--mapping",
        default="experiments/coverage_spike/mappings/azure_storage_production_readiness.yaml",
    )
    parser.add_argument("--fixtures-dir", default="experiments/coverage_spike/fixtures")
    parser.add_argument("--expected-dir", default="experiments/coverage_spike/expected")
    parser.add_argument("--reports-dir", default="experiments/coverage_spike/reports")
    return parser


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        registry = ControlRegistry.load(
            _resolve_path(arguments.checklist),
            _resolve_path(arguments.mapping),
        )
        report = build_coverage_report(
            registry.controls,
            fixtures_dir=_resolve_path(arguments.fixtures_dir),
            expected_dir=_resolve_path(arguments.expected_dir),
        )
        bundle = write_coverage_reports(
            report,
            _resolve_path(arguments.reports_dir),
        )
    except (AttributeError, KeyError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "coverage_spike_input_error",
                        "message": str(exc),
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    print(
        "coverage spike: "
        f"controls={report.total_controls}; "
        f"machine_verifiable={report.machine_verifiable.count}/{report.machine_verifiable.denominator}; "
        "managed_source="
        f"{report.managed_source_coverage.count}/{report.managed_source_coverage.denominator}; "
        "fixture_mismatches="
        f"{report.fixture_mismatches.count}/{report.fixture_mismatches.denominator}; "
        f"internal_totals_valid={str(report.internal_totals_valid).lower()}; "
        f"implementation_gate={report.implementation_gate.status}; "
        f"deployment_readiness={report.deployment_readiness.status}"
    )
    print(f"generation_id={bundle.generation_id}")
    print(f"json_report={bundle.json_path}")
    print(f"markdown_report={bundle.markdown_path}")
    print(f"current_manifest={bundle.current_manifest_path}")
    if report.implementation_gate.status != "passed":
        print(
            json.dumps(
                {
                    "error": {
                        "code": "coverage_spike_gate_failed",
                        "details": {
                            "failed_conditions": sorted(
                                condition
                                for condition, passed in report.implementation_gate.conditions.items()
                                if not passed
                            ),
                        },
                        "message": "Coverage spike implementation gate failed.",
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())