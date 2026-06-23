"""Process Restart category conftest.

Opts the Process Restart test category into the cross-category session-level
prerequisites defined in ``tests/transceiver/conftest.py``.

Per the prerequisite matrix in ``docs/testplan/transceiver/test_plan.md``,
Process Restart consumes the ``presence_verified, gold_fw_verified, links_verified`` gates.
"""

import pytest
from tests.transceiver.conftest import health_check_events
from tests.transceiver.common.health_checks import run_pre_check, run_post_check



# OUTLINE FROM test_plan.md
# # Opt into the cross-category session gates this category consumes.
# @pytest.fixture(autouse=True, scope="session")
# def _category_session_prerequisites(presence_verified, gold_fw_verified, links_verified):
#     return

# # Per-test pre/post checks, layered on top of the parent's xcvrd/core checks.
# @pytest.fixture(autouse=True)
# def _category_per_test_checks(request, duthost, port_attributes_dict):
#     pre_checks = [
#         ("category_pre_check_name", <bool>, "<details on failure>"),
#     ]
#     run_pre_check(request, pre_checks, health_check_events)
#     yield
#     post_checks = [
#         ("category_post_check_name", <bool>, "<details on failure>"),
#     ]
#     run_post_check(request, post_checks, health_check_events)


# verify_health(expect_pid_change=[]) needs list of processes that we expect to restart