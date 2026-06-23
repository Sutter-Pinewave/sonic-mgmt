import logging
import re
import time
import pytest

from tests.transceiver.common.prerequisites import (
    standard_port_recovery_and_verification,
    check_links_up
)
import tests.transceiver.common.process_restart_helpers as pr_helpers

logger = logging.getLogger(__name__)

def test_system_pmon_restart(duthost, port_attributes_dict):
    """ 
    Implements the test described in docs\testplan\transceiver\system_test_plan.md

    Simple pmon restart:
        1. Verify all ports are operationally up and record link up time
        2. Restart pmon using 'docker exec pmon supervisorctl restart pmon'
        3. Wait for pmon_restart_settle_sec
        4. Execute Standard Port Recovery and Verification Procedure for all ports
        5. Verify pmon has been running for at least pmon_restart_settle_sec
    """
    ports = sorted(port_attributes_dict.keys())
    assert ports, "port_attributes_dict is empty - nothing to validate"
    shared_state = {}
    failures = []  # collected across every (port, step) tuple

    logger.info("Recording link states and uptime for %d port(s)", len(ports))
    logger.info("Recording initial uptime: %s", pr_helpers.get_pmon_uptime(duthost))
    for port in ports:
        if not check_links_up(duthost, port,):
            logger.warning("Validation on Start FAILED: %s is down", port)
    
    logger.info("Restarting pmon...")
    pr_helpers.restart_pmon(duthost)
    
    # Wait for settle time and verify
    for port in ports:
        port_attrs = port_attributes_dict[port]
        pmon_wait = pr_helpers.sys_attr(port_attrs, "pmon_restart_settle_sec", 120)
        result = standard_port_recovery_and_verification(
            duthost, port, port_attrs,
            link_up_timeout_sec=pmon_wait,
            shared_state=shared_state,
        )
        if not result["passed"]:
            failures.append(f"[startup] {result['details']}")
            logger.warning("Startup validation FAILED: %s", result["details"])
        else:
            logger.info("Startup validation PASSED: %s", result["details"])
    
    if failures:
        pytest.fail(
            f"pmon restart recovery FAILED on {len(failures)} port(s):\n  - "
            + "\n  - ".join(failures)
        )
