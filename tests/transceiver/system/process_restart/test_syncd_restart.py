import logging
import time
import pytest

from tests.transceiver.common.prerequisites import (
    check_links_up
)
from tests.transceiver.common.verification import (
    standard_port_recovery_and_verification,
    list_core_files
)
import tests.transceiver.common.process_restart_helpers as prh
from tests.common.platform.processes_utils import check_process_up

logger = logging.getLogger(__name__)

@pytest.mark.disable_loganalyzer
def test_system_syncd_restart(duthost, port_attributes_dict, expected_pid_changes):
    """ 
    Implements the syncd restarttest described in docs\testplan\transceiver\system_test_plan.md

    syncd restart:
        1. Verify link states are up for all transceivers and record link up time
        2. Restart syncd using 'sudo systemctl restart syncd'
        3. Wait for syncd_restart_settle_sec
        4. Check if expect_pmon_restart_with_swss_or_syncd is True and verify pmon restart accordingly.
        5. Execute Standard Port Recovery and Verification Procedure for all ports
    """
    expected_pid_changes.add("xcvrd")
    ports = sorted(port_attributes_dict.keys())
    assert ports, "port_attributes_dict is empty - nothing to validate"
    failures = [] 
    shared_state = {"core_baseline": list_core_files(duthost)}

    logger.info("Recording link states and uptime for %d port(s)", len(ports))
    if not check_links_up(duthost, port_attributes_dict):
        logger.warning("Validation on Start FAILED: %s is down", port)
    else:
        for port in ports:
            logger.info("Recording initial link uptime: %s", 
                        prh.get_db_port_table(duthost,port,attr_filter='last_up_time'))
    
    logger.info("Restarting syncd...")
    prh.restart_process(duthost, "syncd")
    syncd_wait = prh.sys_attr(port_attributes_dict[ports[0]], "syncd_restart_settle_sec", prh.DEFAULT_SYNCD_SETTLE_SEC)
    time.sleep(syncd_wait)

    #Check if pmon is expected to restart with syncd restart, and verify if it did
    if prh.sys_attr(port_attributes_dict[ports[0]], "expect_pmon_restart_with_swss_or_syncd", False):
        time.sleep(15)
        logger.info("Verifying pmon restart after syncd restart...")
        if check_process_up(duthost, 'pmon'): # False means pmon restarted, so fail on TRUE
            failures.append(f"[startup] {result['details']}")
            logger.warning("pmon FAILED to restart when expect_pmon_restart_with_swss_or_syncd is True")
    
    # Wait for settle time and verify
    for port in ports:
        port_attrs = port_attributes_dict[port]
        result = standard_port_recovery_and_verification(
            duthost, port, port_attrs,
            link_up_timeout_sec=syncd_wait,
            shared_state=shared_state,
        )
        if not result["passed"]:
            failures.append(f"[startup] {result['details']}")
            logger.warning("Post-restart validation FAILED: %s", result["details"])
        else:
            logger.info("Post-restart validation PASSED: %s", result["details"])
    
    if failures:
        pytest.fail(
            f"syncd restart recovery FAILED on {len(failures)} port(s):\n  - "
            + "\n  - ".join(failures)
        )
