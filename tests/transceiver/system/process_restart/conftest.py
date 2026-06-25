"""Process Restart category conftest.

Opts the Process Restart test category into the cross-category session-level
prerequisites defined in ``tests/transceiver/conftest.py``.

Per the prerequisite matrix in ``docs/testplan/transceiver/test_plan.md``,
Process Restart consumes the ``presence_verified, gold_fw_verified, links_verified`` gates.
"""

import pytest
from tests.transceiver.conftest import health_check_events
from tests.transceiver.common.health_checks import run_pre_check, run_post_check

# Opt into the cross-category session gates this category consumes.
@pytest.fixture(autouse=True, scope="session")
def _category_session_prerequisites(presence_verified, gold_fw_verified, links_verified):
    return