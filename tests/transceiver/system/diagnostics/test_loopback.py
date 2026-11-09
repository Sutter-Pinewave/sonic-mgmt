"""System / Diagnostics - transceiver loopback validation.

Implements the transceiver loopback validation test from the Diagnostic Test
Cases section of ``docs/testplan/transceiver/system_test_plan.md``.

Every port with ``loopback_supported`` and a non-empty
``supported_loopback_modes`` is exercised. Ports are batched per mode: one
mode is enabled on every port that supports it, verified through the CMIS
page 13h loopback-control registers, then disabled and verified clear before
the next mode starts. Host-side-input loopback returns the DUT's own transmit
signal to its receiver, so on LLDP-enabled ports the data path is checked by
requiring the LLDP neighbor to become the DUT port itself.

Execution order::

  session start
    `- presence / gold FW / links gates (system/conftest.py)
  test_transceiver_loopback
    |- State Capture: loopback registers + admin/oper (pre-test validation:
    |                 complete capture, links up, no loopback already active)
    |- for each mode: enable -> verify active (+ LLDP self-neighbor for
    |                 host-side-input) -> disable -> verify cleared + link up
    `- preserved_port_state exit (always): restore drifted loopback/port state
         -> Standard Port Recovery and Verification -> verify state matches
  session end
    `- _system_post_session_checks (system/conftest.py)

Restoration runs before the Standard Port Recovery and Verification so a
mid-test failure cannot leave a loopback active while recovery is checked.
On a passing run restoration is a no-op, so the order matches the test plan.

Failures are accumulated across modes and ports and reported in a single
``pytest.fail`` at the end.
"""
import logging

import pytest
from natsort import natsorted

from tests.common.helpers.assertions import pytest_assert
from tests.transceiver.attribute_parser.attribute_keys import SYSTEM_ATTRIBUTES_KEY
from tests.transceiver.common import cli_helpers, scenario_ops
from tests.transceiver.common.cmis_helper import CMIS_LOOPBACK_MODE_OFFSET
from tests.transceiver.common.health_checks import capture_baseline
from tests.transceiver.common.port_selectors import select_attribute_ports
from tests.transceiver.common.prerequisites import wait_until_links_up
from tests.transceiver.common.state_management import (
    LOOPBACK_FACET,
    PORT_STATUS_FACET,
    capture_errors,
    capture_port_state,
    expect_port_state,
    loopback_lanes_by_mode,
    preserved_port_state,
    verify_port_state,
)
from tests.transceiver.common.topology import PeerConnection, resolve_lldp_peer_aliases
from tests.transceiver.common.verification import check_lldp_neighbors_present

logger = logging.getLogger(__name__)

# Modes whose loopback returns the DUT's own transmit signal to the DUT, so
# the LLDP neighbor on the port becomes the port itself.
_LLDP_SELF_NEIGHBOR_MODES = ("host-side-input",)
_LOOPBACK_POLL_INTERVAL_SEC = 2


def _build_loopback_plan(port_attributes_dict):
    """Group loopback-capable ports by supported mode and validate their configuration.

    Args:
        port_attributes_dict: resolved per-port attributes.

    Returns:
        tuple(dict, list[str]): ``{mode: [ports]}`` and a list of configuration
        errors (unknown modes, or a mode whose lane mask does not resolve).
    """
    ports = select_attribute_ports(
        port_attributes_dict, SYSTEM_ATTRIBUTES_KEY,
        predicate=lambda _port, attrs: bool(
            attrs[SYSTEM_ATTRIBUTES_KEY].get("loopback_supported")
            and attrs[SYSTEM_ATTRIBUTES_KEY].get("supported_loopback_modes")
        ),
    ).primary_ports
    plan = {}
    errors = []
    for port in ports:
        try:
            lanes_by_mode = loopback_lanes_by_mode(port_attributes_dict, port)
        except ValueError as error:
            errors.append(f"{port}: no valid lane mask for loopback: {error}")
            continue
        for mode in port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY]["supported_loopback_modes"]:
            if mode not in CMIS_LOOPBACK_MODE_OFFSET:
                errors.append(f"{port}: unknown loopback mode {mode!r}")
            elif mode not in lanes_by_mode:
                errors.append(f"{port}: {mode} needs a valid lane mask in BASE_ATTRIBUTES")
            else:
                plan.setdefault(mode, []).append(port)
    return plan, errors


def _check_lldp_self_neighbor(duthost, ports, port_attributes_dict):
    """Verify each LLDP-enabled port in ``ports`` sees itself as its LLDP neighbor.

    The expected port ID uses the DUT's running-config alias, resolved by the
    same helper that builds the ``port_peers`` fixture.

    Args:
        duthost: SONiC DUT host fixture.
        ports: ports with a DUT-facing loopback active.
        port_attributes_dict: resolved per-port attributes.

    Returns:
        list[str]: per-port failure strings; empty when every checked port
        reports the DUT hostname and its own port name or alias.
    """
    lldp_ports = [
        port for port in ports
        if port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY].get("verify_lldp_on_link_up", True)
    ]
    if not lldp_ports:
        logger.info("No LLDP-enabled ports for the loopback data-path check")
        return []
    resolved = resolve_lldp_peer_aliases(
        duthost, {port: PeerConnection(duthost.hostname, port) for port in lldp_ports},
    )
    failures = [
        f"data path (LLDP self-neighbor): {port}: {error}"
        for port, (_peer, error) in natsorted(resolved.items()) if error
    ]
    expected_peers = {port: peer for port, (peer, error) in resolved.items() if not error}
    results = check_lldp_neighbors_present(
        duthost,
        {port: port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY]["lldp_neighbor_wait_sec"] for port in expected_peers},
        expected_peers=expected_peers,
    )
    return failures + [
        f"data path (LLDP self-neighbor): {result['details']}"
        for result in results.values() if not result["passed"]
    ]


def _exercise_loopback_mode(duthost, mode, ports, port_attributes_dict, snapshot):
    """Enable, verify, disable, and re-verify one loopback mode on its ports.

    Args:
        duthost: SONiC DUT host fixture.
        mode: loopback mode under test.
        ports: ports supporting ``mode``, from :func:`_build_loopback_plan`.
        port_attributes_dict: resolved per-port attributes.
        snapshot: pre-test capture including ``LOOPBACK_FACET``.

    Returns:
        list[str]: failure strings prefixed with ``[<mode>]``.
    """
    ports = natsorted(ports)
    settle_sec = max(port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY]["loopback_settle_sec"] for port in ports)
    failures = []
    logger.info("Loopback %s: enabling on %d port(s) %s", mode, len(ports), ports)

    enable_errors = cli_helpers.sfputil_debug_loopback_batch(duthost, ports, mode, enable=True)
    failures += [err for err in enable_errors.values() if err]
    enabled = [port for port in ports if not enable_errors[port]]

    if enabled:
        expected = expect_port_state(snapshot, LOOPBACK_FACET, {port: {mode: "on"} for port in enabled})
        active_failures = scenario_ops.poll_ports_recovered(
            lambda: verify_port_state(
                duthost, expected, port_attributes_dict, ports=enabled, facets=[LOOPBACK_FACET], keys=[mode],
            ),
            settle_sec, _LOOPBACK_POLL_INTERVAL_SEC, label=f"Loopback {mode} enable",
        )
        failures += [f"not active after {settle_sec}s: {f}" for f in active_failures]

        if mode in _LLDP_SELF_NEIGHBOR_MODES:
            failures += _check_lldp_self_neighbor(duthost, enabled, port_attributes_dict)
        else:
            logger.info("Loopback %s: no host-observable data-path check for this mode", mode)

    logger.info("Loopback %s: disabling on %d port(s)", mode, len(ports))
    disable_errors = cli_helpers.sfputil_debug_loopback_batch(duthost, ports, mode, enable=False)
    failures += [err for err in disable_errors.values() if err]
    disabled = [port for port in ports if not disable_errors[port]]

    if disabled:
        cleared_failures = scenario_ops.poll_ports_recovered(
            lambda: verify_port_state(
                duthost, snapshot, port_attributes_dict, ports=disabled, facets=[LOOPBACK_FACET], keys=[mode],
            ),
            settle_sec, _LOOPBACK_POLL_INTERVAL_SEC, label=f"Loopback {mode} disable",
        )
        failures += [f"not cleared after {settle_sec}s: {f}" for f in cleared_failures]
        link_result = wait_until_links_up(
            duthost, {port: port_attributes_dict[port] for port in disabled},
            scenario_ops.scale_bulk_wait_for_ports(port_attributes_dict, disabled, "port_startup_wait_sec"),
        )
        failures += [f"link not restored after disable: {down}" for down in link_result["down"]]

    return [f"[{mode}] {failure}" for failure in failures]


@pytest.mark.disable_loganalyzer
def test_transceiver_loopback(duthost, port_attributes_dict, lport_to_first_subport_mapping, port_peers):
    """Every supported loopback mode activates, carries traffic where observable,
    and clears with the link and all recovery checks restored."""
    plan, config_errors = _build_loopback_plan(port_attributes_dict)
    pytest_assert(
        not config_errors,
        "Loopback attribute configuration invalid:\n  - " + "\n  - ".join(config_errors),
    )
    if not plan:
        pytest.skip("No port has loopback_supported=True with non-empty supported_loopback_modes")

    ports = natsorted({port for mode_ports in plan.values() for port in mode_ports})
    logger.info("Loopback plan: %s", plan)

    snapshot = capture_port_state(
        duthost, ports, port_attributes_dict, facets=(LOOPBACK_FACET, PORT_STATUS_FACET),
    )
    incomplete = capture_errors(snapshot)
    pytest_assert(not incomplete, "Pre-test: state capture incomplete:\n  - " + "\n  - ".join(incomplete))
    not_up = [
        f"{port}(admin={status['admin']}, oper={status['oper']})"
        for port, status in snapshot.values[PORT_STATUS_FACET.name].items()
        if status != {"admin": "up", "oper": "up"}
    ]
    pytest_assert(not not_up, "Pre-test: loopback ports not admin/oper up: " + "; ".join(not_up))
    already_looped = [
        f"{port}({mode}={state})"
        for port, states in snapshot.values[LOOPBACK_FACET.name].items()
        for mode, state in sorted(states.items()) if state != "off"
    ]
    pytest_assert(not already_looped, "Pre-test: loopback already active: " + "; ".join(already_looped))

    recovery_kwargs = {
        "link_up_timeout_sec": scenario_ops.scale_bulk_wait_for_ports(
            port_attributes_dict, ports, "port_startup_wait_sec",
        ),
        "health_baseline": capture_baseline(duthost),
        "lport_to_first_subport_mapping": lport_to_first_subport_mapping,
        "port_peers": port_peers,
    }
    failures = []
    with preserved_port_state(duthost, snapshot, port_attributes_dict, recovery_kwargs=recovery_kwargs) as preserved:
        for mode in sorted(plan):
            failures += _exercise_loopback_mode(duthost, mode, plan[mode], port_attributes_dict, snapshot)
    failures += preserved.failures

    if failures:
        pytest.fail(
            f"Transceiver loopback validation FAILED with {len(failures)} issue(s):\n  - "
            + "\n  - ".join(failures)
        )
