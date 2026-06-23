"""Process Restart category conftest.

Opts the Process Restart test category into the cross-category session-level
prerequisites defined in ``tests/transceiver/conftest.py``.

Per the prerequisite matrix in ``docs/testplan/transceiver/test_plan.md``,
Process Restart consumes the ``presence_verified, gold_fw_verified, links_verified`` gates.
"""

import pytest


@pytest.fixture(autouse=True, scope="session")
def _process_restart_session_prerequisites(presence_verified, gold_fw_verified, links_verified):
    """
    Autouse wrapper that pulls in the session-scoped prerequisite gates
    consumed by Process Restart tests.
    """
    return
