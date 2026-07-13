"""The release state machine is exactly the states the runner actually uses.

LOADING, VALIDATING and DISABLED were declared but never assigned, which made the
lifecycle look bigger than it is and left dead branches in health.py and the
runner. This pins the machine to the six real states so they cannot creep back
without a runner phase that actually sets them.
"""

from __future__ import annotations

from skycat.constants import CatalogReleaseState
from skycat.health import _TRANSIENT_STATES


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
