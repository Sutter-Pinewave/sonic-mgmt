
def restart_xcvrd(duthost):
    duthost.shell("docker exec pmon supervisorctl restart xcvrd")

def get_xcvrd_uptime(duthost):
    return duthost.shell("docker exec pmon supervisorctl status xcvrd | awk '{print $NF}'")

def inject_xcvrd_crash(duthost):
    # dude idk
    # it says i need to edit the xcvrd.py file
    # where is it??