"""The release state machine is exactly the states the runner actually uses.

LOADING, VALIDATING and DISABLED were declared but never assigned, which made the
lifecycle look bigger than it is and left dead branches in health.py and the
runner. This pins the machine to the six real states so they cannot creep back
without a runner phase that actually sets them.
"""

from __future__ import annotations

import skycat.validation.vsx
from skycat.constants import CatalogReleaseState, IngestionRunStatus
from skycat.health import _IN_FLIGHT_RUN_STATUS
from skycat.validation import _FAMILY_VALIDATORS


def test_the_state_machine_is_six_states():
    assert {s.value for s in CatalogReleaseState} == {
        "registered",  # row exists, nothing imported
        "staging",     # legacy: no longer assigned, kept to name stranded rows
        "ready",       # imported, validated, indexed; not serving
        "active",      # serving (at most one per family)
        "superseded",  # previously active; retained for rollback
        "failed",      # import failed; can never auto-activate
    }


def test_an_import_in_flight_is_a_run_status_not_a_release_state():
    """The stuck-import check keys on the attempt, not on the release.

    It used to key on a release in STAGING, which meant the runner had to demote
    a perfectly good READY/SUPERSEDED release before touching any data — and a
    failure then stranded it there, unable to activate, with its partition
    intact. The release row now describes the partition on disk for the whole
    import, so "in flight" lives on the per-attempt `ingestion_run` row.
    """
    assert _IN_FLIGHT_RUN_STATUS == IngestionRunStatus.RUNNING.value


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
