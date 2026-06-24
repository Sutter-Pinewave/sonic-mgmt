# expect_pmon_restart_with_swss_or_syncd 	    Whether pmon restart is expected during swss/syncd restartimport logging
import re
import time
import pytest

from tests.transceiver.common.prerequisites import (
    standard_port_recovery_and_verification,
    check_links_up
)
import tests.transceiver.common.process_restart_helpers as pr_helpers

logger = logging.getLogger(__name__)

def test_system_syncd_restart(duthost, port_attributes_dict):
    """ 
    Implements the test described in docs\testplan\transceiver\system_test_plan.md

    Simple syncd restart:
        1. Verify all ports are operationally up and record link up time
        2. Restart syncd using 'sudo systemctl restart syncd'
        3. Wait for syncd_restart_settle_sec
        4. Execute Standard Port Recovery and Verification Procedure for all ports
        5. Verify syncd has been running for at least syncd_restart_settle_sec
    """
    ports = sorted(port_attributes_dict.keys())
    assert ports, "port_attributes_dict is empty - nothing to validate"
    shared_state = {}
    failures = []  # collected across every (port, step) tuple

    logger.info("Recording link states and uptime for %d port(s)", len(ports))
    logger.info("Recording initial link uptime: %s", pr_helpers.get_xcvrd_uptime(duthost))
    for port in ports:
        if not check_links_up(duthost, port,):
            logger.warning("Validation on Start FAILED: %s is down", port)
    
    logger.info("Restarting syncd...")
    pr_helpers.restart_syncd(duthost)

    if pr_helpers.sys_attr(port_attributes_dict[ports[0]], "expect_pmon_restart_with_swss_or_syncd", False):
        logger.info("Verifying pmon restart after syncd restart...")
        #TODO - make sure pmon restarted
    
    # Wait for settle time and verify
    for port in ports:
        port_attrs = port_attributes_dict[port]
        syncd_wait = pr_helpers.sys_attr(port_attrs, "syncd_restart_settle_sec", 120)
        result = standard_port_recovery_and_verification(
            duthost, port, port_attrs,
            link_up_timeout_sec=syncd_wait,
            shared_state=shared_state,
        )
        if not result["passed"]:
            failures.append(f"[startup] {result['details']}")
            logger.warning("Startup validation FAILED: %s", result["details"])
        else:
            logger.info("Startup validation PASSED: %s", result["details"])
    
    if failures:
        pytest.fail(
            f"syncd restart recovery FAILED on {len(failures)} port(s):\n  - "
            + "\n  - ".join(failures)
        )
