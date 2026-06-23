import tests.transceiver.attribute_parser.attribute_keys as at_keys

XCVRD_RESTART = "docker exec pmon supervisorctl restart xcvrd"
XCVRD_UPTIME = "docker exec pmon supervisorctl status xcvrd | awk '{print $NF}'"
PMON_RESTART = "sudo systemctl restart pmon"
SWSS_RESTART = "sudo systemctl restart swss"
SYNCD_RESTART = "sudo systemctl restart syncd"



############################# GENERIC HELPERS #############################
def sys_attr(port_attrs, name, default):
    """Extract system attribute from port attributes dict with default fallback."""
    return port_attrs.get(at_keys.SYSTEM_ATTRIBUTES_KEY, {}).get(name, default)

############################# XCVRD HELPERS #############################
def restart_xcvrd(duthost):
    duthost.shell(XCVRD_RESTART)

def get_xcvrd_uptime(duthost):
    return duthost.shell(XCVRD_UPTIME)

# def inject_xcvrd_crash(duthost):
    # dude idk
    # it says i need to edit the xcvrd.py file
    # where is it??
    
############################# PMON HELPERS #############################
def restart_pmon(duthost):  
    duthost.shell(PMON_RESTART)

############################# SWSS HELPERS #############################
def restart_swss(duthost):  
    duthost.shell(SWSS_RESTART)

############################# SYNCD HELPERS #############################
def restart_syncd(duthost):  
    duthost.shell(SYNCD_RESTART)
