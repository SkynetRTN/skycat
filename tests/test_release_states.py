"""The release state machine is exactly the states the runner actually uses.

LOADING, VALIDATING and DISABLED were declared but never assigned, which made the
lifecycle look bigger than it is and left dead branches in health.py and the
runner. This pins the machine to the six real states so they cannot creep back
without a runner phase that actually sets them.
"""

from __future__ import annotations

import skycat.validation.vsx
from skycat.constants import CatalogReleaseState, IngestionRunStatus
from skycat.health import _TRANSIENT_STATES
from skycat.validation import _FAMILY_VALIDATORS


def test_the_state_machine_is_six_states():
    assert {s.value for s in CatalogReleaseState} == {
        "registered",  # row exists, nothing imported
        "staging",     # COPY into staging under way
        "ready",       # imported, validated, indexed; not serving
        "active",      # serving (at most one per family)
        "superseded",  # previously active; retained for rollback
        "failed",      # import failed; can never auto-activate
    }


def test_staging_is_the_only_transient_state():
    """Only STAGING can strand an import; health's stuck-import check keys on it."""
    assert _TRANSIENT_STATES == (CatalogReleaseState.STAGING.value,)


def test_ingestion_run_statuses_are_the_three_the_runner_sets():
    """CANCELLED was declared but never assigned — the same defect as the states."""
    assert {s.value for s in IngestionRunStatus} == {"running", "succeeded", "failed"}


def test_there_is_exactly_one_validator_registry():
    """A second, string-valued FAMILY_VALIDATORS shadowed this one in vsx.py.

    It listed only apass and vsx, so registering a new family there would have
    silently done nothing. Every family with a validation module must appear in
    the real (callable-valued) registry.
    """
    assert set(_FAMILY_VALIDATORS) == {"apass", "landolt", "stetson", "vsx"}
    assert all(callable(v) for v in _FAMILY_VALIDATORS.values())
    assert not hasattr(skycat.validation.vsx, "FAMILY_VALIDATORS")
