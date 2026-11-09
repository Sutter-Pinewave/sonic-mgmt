"""System / Diagnostics - media SI settings validation.

Implements the media SI settings validation test from the Diagnostic Test
Cases section of ``docs/testplan/transceiver/system_test_plan.md``.

Read-only. For every port whose ``media_si_settings`` attribute is non-empty,
the NPU SI handshake must report ``NPU_SI_SETTINGS_DONE`` and every configured
media SI field must match the value xcvrd published to APPL_DB ``PORT_TABLE``.
Lane values are sliced per logical port with ``host_lane_mask``, mirroring how
xcvrd splits module-wide media settings across breakout subports. The
Standard Port Recovery and Verification Procedure then runs with its SI
sub-check enabled.

Failures are accumulated per port and reported in a single ``pytest.fail``.
"""
import logging

import pytest

from tests.common.helpers.assertions import pytest_assert
from tests.transceiver.attribute_parser.attribute_keys import SYSTEM_ATTRIBUTES_KEY
from tests.transceiver.common import scenario_ops
from tests.transceiver.common.health_checks import capture_baseline
from tests.transceiver.common.port_selectors import select_attribute_ports
from tests.transceiver.common.prerequisites import check_links_up
from tests.transceiver.common.verification import (
    check_media_si_settings,
    standard_port_recovery_and_verification,
)

logger = logging.getLogger(__name__)


def test_media_si_settings(duthost, port_attributes_dict, lport_to_first_subport_mapping, port_peers):
    """Media SI settings defined in platform/HWSKU attributes match APPL_DB
    PORT_TABLE after the NPU SI sync completes, and all recovery checks pass."""
    ports = select_attribute_ports(
        port_attributes_dict, SYSTEM_ATTRIBUTES_KEY,
        predicate=lambda _port, attrs: bool(attrs[SYSTEM_ATTRIBUTES_KEY].get("media_si_settings")),
    ).primary_ports
    if not ports:
        pytest.skip("No port defines a non-empty media_si_settings attribute")
    tested_attributes = {port: port_attributes_dict[port] for port in ports}

    health_baseline = capture_baseline(duthost)
    link_check = check_links_up(duthost, tested_attributes)
    pytest_assert(link_check["passed"], f"Pre-test: {link_check['details']}")

    failures = [
        result["details"]
        for result in check_media_si_settings(duthost, ports, port_attributes_dict).values()
        if not result["passed"]
    ]

    result = standard_port_recovery_and_verification(
        duthost, ports, port_attributes_dict,
        link_up_timeout_sec=scenario_ops.scale_bulk_wait_for_ports(
            port_attributes_dict, ports, "port_startup_wait_sec",
        ),
        health_baseline=health_baseline,
        lport_to_first_subport_mapping=lport_to_first_subport_mapping,
        port_peers=port_peers,
        verify_si_settings=True,
    )
    if not result["passed"]:
        failures.append(f"[recovery] {result['details']}")

    if failures:
        pytest.fail(
            f"Media SI settings validation FAILED with {len(failures)} issue(s):\n  - "
            + "\n  - ".join(failures)
        )
