import tests.transceiver.attribute_parser.attribute_keys as at_keys

#NOTE TEMP FILE - need to move to established files per file_organization.md

############################# CONSTANTS #############################
# from docs/testplan/transceiver/test_plan.md
XCVRD_RESTART = "docker exec pmon supervisorctl restart xcvrd"
XCVRD_UPTIME = "docker exec pmon supervisorctl status xcvrd | awk '{print $NF}'"
PMON_RESTART = "sudo systemctl restart pmon"
SWSS_RESTART = "sudo systemctl restart swss"
SYNCD_RESTART = "sudo systemctl restart syncd"

# from docs/testplan/transceiver/system_test_plan.md
DEFAULT_XCVRD_SETTLE_SEC = 120
DEFAULT_PMON_SETTLE_SEC = 120
DEFAULT_SWSS_SETTLE_SEC = 180
DEFAULT_SYNCD_SETTLE_SEC = 240

############################# GENERIC HELPERS #############################
def sys_attr(port_attrs, name, default):
    """Extract system attribute from port attributes dict with default fallback."""
    return port_attrs.get(at_keys.SYSTEM_ATTRIBUTES_KEY, {}).get(name, default)

def restart_process(duthost, process):
    if process == "xcvrd": cmd = XCVRD_RESTART
    elif process == "pmon": cmd = PMON_RESTART
    elif process == "swss": cmd = SWSS_RESTART  
    elif process == "syncd": cmd = SYNCD_RESTART
    if process:
        duthost.shell(cmd)

def get_xcvrd_uptime(duthost):
    return duthost.shell(XCVRD_UPTIME)

# def inject_xcvrd_crash(duthost):
    # dude idk
    # it says i need to edit the xcvrd.py file
    # where is it??
    
def get_link_uptime(duthost, port, namespace=None):
    cmd = ''
    if namespace: #MULTI-ASIC
        cmd = f'sonic-db-cli -n "{namespace}" APPL_DB hget "PORT_TABLE:{port}" "last_up_time"'
    else: #SINGLE ASIC
        cmd = f'sonic-db-cli APPL_DB hget "PORT_TABLE:{port}" "last_up_time"' 
    return duthost.shell(cmd)