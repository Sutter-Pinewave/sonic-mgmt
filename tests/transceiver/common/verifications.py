# FROM SYSTEM TEST PLAN:
"""
Link Status Verification:
Verify port is operationally up
Wait for configured timeout period before declaring failure

LLDP Verification (if verify_lldp_on_link_up is True):
Verify port appears in LLDP neighbor table
Confirm LLDP neighbor information is correctly populated (remote device ID, port ID, etc. if applicable)

CMIS State Verification (for CMIS active optical transceivers (can be checked via cmis_active_optical attribute)):
Verify DataPathState is DPActivated for operational ports
Verify ConfigState is ConfigSuccess

SI Settings Verification (if applicable):
Optics SI Settings: If optics_si_settings is defined, verify current EEPROM values match configured attributes
Media SI Settings: If media_si_settings is defined, verify PORT_TABLE APPL_DB values match configured attributes. 
Also, ensure NPU_SI_SETTINGS_SYNC_STATUS_KEY is set to NPU_SI_SETTINGS_DONE in PORT_TABLE of APPL_DB
Log any discrepancies for analysis

Application Code Verification (if expected_application_code is defined and not null): 
Read current application code from transceiver EEPROM
Verify the actual application code matches the expected_application_code value
Log any discrepancies for analysis

Docker and Process Health Check:
Verify all critical services (xcvrd, pmon, swss, syncd) are running for at least 3 minutes
Ensure no core files are present in /var/core
Log any service failures for analysis

Standard Port Recovery and Verification Procedure:
Combines all the above checks into a single procedure for each port
"""

import time
import logging

import tests.transceiver.common.process_restart_helpers as prh

from tests.transceiver.attribute_parser.attribute_keys import (
    EEPROM_ATTRIBUTES_KEY, 
    SYSTEM_ATTRIBUTES_KEY
)
from tests.transceiver.common.prerequisites import check_links_up
from tests.common.platform.processes_utils import check_process_up

# from docs/testplan/transceiver/system_test_plan.md
CRIT_PROCESSES_MIN_RUNTIME = 3
POLL_DELAY = 5

logger = logging.getLogger(__name__)


# port_startup_wait_sec
def link_status_verification(duthost, port_attributes_dict, port_timeout=60):
    deadline = time.monotonic() + max(0, int(port_timeout))
    result = check_links_up(duthost, port_attributes_dict)
    while not result['passed'] and time.monotonic() < deadline:
        time.sleep(POLL_DELAY)
        result = check_links_up(duthost, port_attributes_dict)

    if result['passed']:
        details = f"Core processes all OK"
        logger.info("Link Status check PASSED on all ports")
        return {"passed": True, "details": details}
    else:
        details = f"Ports down on {duthost}: " + "; ".join(result['down'])
        logger.warning("Link Status check FAILED to complete before timeout")
        return {"passed": False, "details": details}


def lldp_verification(duthost, port_attributes_dict):
    #NOTE, currently only structured to support loopback connections
        # will NOT verify remote device ID
    #TODO, verify port_attribute_dict contents and formatting
    # 'show lldp neighbors'
    # filter for 'Interface:    {Port}'         # Remote Interface
    # filter for 'PortID:       local etp7a'    # local port
    # filter for 'PortDescr:    Ethernet33'     # local interface

def cmis_state_verification(duthost, port_attributes_dict):


def optics_si_settings_verification(duthost, port_attributes_dict):
    ports = sorted(port_attributes_dict.keys())
    for port in ports:
        


def media_si_settings_verification(duthost, port_attributes_dict):


def application_code_verification(duthost, port_attributes_dict):


def docker_and_process_health_check(duthost):
    failures = []
    for process in ['xcvrd', 'pmon', 'swss', 'syncd']:
        if not check_process_up(duthost, process, minimal_runtime=CRIT_PROCESSES_MIN_RUNTIME):
            failures.append(f"{process} not found or has been running less than {CRIT_PROCESSES_MIN_RUNTIME} minutes")

    filesPresent = False
    #TODO check /var/core, fail if files exist
    if filesPresent:
        failures.append(f"Unexpected core file found on {duthost}")

    if failures:
        details = f"{duthost}: " + "; ".join(failures)
        logger.warning("Docker and Process Health Check FAILED: %s", details)
        return {"passed": False, "details": details}

    details = f"Core processes all OK"
    logger.info("Docker and Process Health Check PASSED: %s", details)
    return {"passed": True, "details": details}



def standard_port_recovery_and_verification(duthost, port_attributes_dict):
    ports = sorted(port_attributes_dict.keys())
    port_attrs = port_attributes_dict[ports[0]]
    sys_attrs = port_attrs.get(SYSTEM_ATTRIBUTES_KEY, {})
    eeprom_attrs = port_attrs.get(EEPROM_ATTRIBUTES_KEY, {})

    """
    Implements the Standard Port Recovery and Verification Procedure from
    docs/testplan/transceiver/system_test_plan.md

    Args:
        duthost: DUT host object
        port_attributes_dict: A dictionary containing key:value pairs of ports and their attributes
        {
            Port name (e.g., "Ethernet0") : Dictionary of attributes
        }
    """
    resultList = []
    resultList.append(link_status_verification(
        duthost, 
        port_attributes_dict,
        sys_attrs.get("port_startup_wait_sec"))
    )
    if sys_attrs.get("verify_lldp_on_link_up", False):
        resultList.append(lldp_verification(
            duthost, 
            port_attributes_dict,
        ))
    if eeprom_attrs.get("cmis_active_optical ", False):
        resultList.append(cmis_state_verification(
            duthost, 
            port_attributes_dict,
        ))
    if sys_attrs.get("optics_si_settings", False):
        resultList.append(optics_si_settings_verification(
            duthost, 
            port_attributes_dict,
        ))
    if sys_attrs.get("media_si_settings", False):
        resultList.append(media_si_settings_verification(
            duthost, 
            port_attributes_dict,
        ))
    if sys_attrs.get("expected_application_code", False):
        resultList.append(application_code_verification(
            duthost, 
            port_attributes_dict,
        ))
    resultList.append(docker_and_process_health_check(duthost))

    passFlag = True
    combinedReport = []
    for result in resultList:
        passFlag == passFlag and result['passed']
        combinedReport.append(result['details'])

    details = f"{duthost}: " + "; ".join(combinedReport)
    if not passFlag:
        logger.info("Standard Port Recovery and Verification Procedures FAILED: %s", details)
    else:
        logger.info("Standard Port Recovery and Verification Procedures PASSED: %s", details)