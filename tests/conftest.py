"""Skip checks whose standalone fixture files are absent from this checkout."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED_MODULES = {
    "test_cli", "test_demo", "test_desktop_foundation", "test_desktop_review",
    "test_desktop_widgets", "test_owner", "test_owner_samples", "test_service", "test_startup",
}
SEED_CASES = {
    "test_sat_agrees_with_independent_truth_table", "test_seed_decisions",
    "test_import_review_activation_and_restart_snapshot",
    "test_invalid_import_preserves_raw_bytes_and_active_snapshot",
    "test_corrupt_or_missing_revision_fails_closed", "test_pointer_failure_never_changes_active_revision",
    "test_mcp_actions_use_guarded_service_and_reject_overrides",
    "test_real_mcp_process_reports_observed_home_and_request",
    "test_legacy_audit_has_no_invented_historical_trace",
}
SEED_INDEPENDENT_CASES = {
    "test_rendered_nested_rule_and_actual_sat_input_agree",
    "test_initialization_does_not_discard_unsaved_owner_work",
    "test_bootstrap_checks_absence_under_the_publication_lock",
}


def pytest_collection_modifyitems(items):
    seed_missing = not (ROOT / "data/seed_policy.json").is_file()
    for item in items:
        module = Path(item.path).stem
        name = item.originalname or item.name
        needs_seed = (seed_missing and name not in SEED_INDEPENDENT_CASES
                      and (module in SEED_MODULES or name in SEED_CASES))
        if needs_seed:
            item.add_marker(pytest.mark.skip(reason="Requires fixture files not included in this checkout"))
