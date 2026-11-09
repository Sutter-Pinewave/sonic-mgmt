"""State Preservation and Restoration helpers for transceiver System tests.

Lives at the location reserved by
``docs/testplan/transceiver/diagrams/file_organization.md`` for the
"State Preservation and Restoration helpers".

Implements the post-session / post-test restoration described in
``docs/testplan/transceiver/system_test_plan.md`` (§ State Preservation and
Restoration): bring every port back to admin-up and high power mode (low-power
off), then bounce any remaining admin-up/oper-down links once. Operational state
is checked after restoration by the caller.

This is the restorative counterpart to
:mod:`tests.transceiver.common.verification` (which is diagnostic): it issues
port startup through :mod:`tests.transceiver.common.cli_helpers`. The caller
performs final link verification after all post-session checks.

Per-test State Preservation and Restoration is built from *facets*: one
independently captured and restored piece of port state (admin/oper, LPMode,
loopback, C-CMIS tuning, or any caller-defined state). Three standalone
building blocks operate on any facet set:

* :func:`capture_port_state` - State Capture, one batched read per facet.
* :func:`verify_port_state`  - compare live state with a snapshot.
* :func:`restore_port_state` - put drifted ports back to the snapshot.

:func:`preserved_port_state` composes them with the Standard Port Recovery
and Verification Procedure so restoration runs regardless of pass/fail.

Adding state to preserve needs no change to the engine. Build a facet from
the declarative helpers and register it so tests can select it by name::

    FEC_FACET = register_facet(db_field_facet(
        "fec", "CONFIG_DB", "PORT", ["fec"],
        lambda port, key, target, ns: f"sudo config interface{ns_flag(ns)} fec {port} {target}",
    ))

* :func:`db_field_facet` - DB fields restored by one CLI command per port.
* :func:`db_reader` / :func:`table_reader` - generic ``read`` builders.
* :func:`command_apply` - generic batched ``apply`` builder.
* :class:`StateFacet` - fully custom ``read`` / ``apply`` when neither fits.
"""
import contextlib
import logging
from collections import namedtuple

from natsort import natsorted

from tests.common.platform.interface_utils import get_dut_interfaces_status
from tests.transceiver.attribute_parser.attribute_keys import BASE_ATTRIBUTES_KEY, SYSTEM_ATTRIBUTES_KEY
from tests.transceiver.common import cli_helpers, db_helpers, scenario_ops
from tests.transceiver.common.attribute_helpers import MEDIA_LANE_MASK_KEY
from tests.transceiver.common.cmis_helper import (
    CMIS_HOST_SIDE_LOOPBACK_MODES,
    CMIS_LOOPBACK_MODE_OFFSET,
    CMIS_PAGE_13_LOOPBACK_PAGE,
    CMIS_PAGE_13_LOOPBACK_SIZE,
    CMIS_PAGE_13_LOOPBACK_START,
    lanes_from_mask,
    loopback_lane_state,
)
from tests.transceiver.common.verification import standard_port_recovery_and_verification

logger = logging.getLogger(__name__)


def post_state_restoration(duthost, port_attributes_dict):
    """Restore every port in ``port_attributes_dict`` to known-good state.

    Per ``system_test_plan.md`` post-session State Restoration:
            * high power mode (low-power off),
            * admin-up,
            * one shut/startup recovery attempt for admin-up/oper-down links.

    The function is restorative, not diagnostic - it does the minimum
    needed to bring each port back, then reports which ports it touched
    and which ones still failed to recover.

    Returns:
        dict: ``{
            'admin_up_restored': [str],     # ports we issued 'startup' on
            'lpmode_high_restored': [str],  # ports we toggled out of LPMode
            'link_bounced': [str],          # oper-down ports we shut/start
            'still_failing': [str],         # toggle/wait failure details
        }``
    """
    summary = {
        "admin_up_restored": [],
        "lpmode_high_restored": [],
        "link_bounced": [],
        "still_failing": [],
    }
    if not port_attributes_dict:
        return summary

    # Pass 1: turn off low-power mode on anything still in LPMode.
    lpmode_by_port, err = cli_helpers.sfputil_show_lpmode(duthost)
    if err:
        logger.warning("Restoration: failed to read LPMode state: %s", err)
    else:
        low_power_ports = [
            port for port in sorted(port_attributes_dict)
            if lpmode_by_port.get(port, "").lower() == "on"
        ]
        for port in low_power_ports:
            logger.info("Restoration: turning off LPMode on %s", port)
            _, err = cli_helpers.sfputil_set_lpmode(duthost, port, low_power=False)
            if err:
                logger.warning("Restoration: failed to turn off LPMode on %s: %s", port, err)
                summary["still_failing"].append(err)
            else:
                summary["lpmode_high_restored"].append(port)

    # Pass 2: admin-up everything that's down after power-state restoration.
    intf_status = get_dut_interfaces_status(duthost)
    admin_down_ports = [
        port for port in sorted(port_attributes_dict)
        if (intf_status.get(port) or {}).get("admin") == "down"
    ]
    if admin_down_ports:
        startup_wait = scenario_ops.scale_bulk_wait(
            max(
                port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY]["port_startup_wait_sec"]
                for port in admin_down_ports
            ),
            len(admin_down_ports),
        )
        scenario_ops.perform_ports_startup(duthost, admin_down_ports, startup_wait)
        summary["admin_up_restored"].extend(admin_down_ports)

    # Pass 3: bounce links that remain admin-up but operationally down.
    intf_status = get_dut_interfaces_status(duthost)
    oper_down_ports = [
        port for port in sorted(port_attributes_dict)
        if (intf_status.get(port) or {}).get("admin") == "up"
        and (intf_status.get(port) or {}).get("oper") == "down"
    ]
    if oper_down_ports:
        shutdown_wait = scenario_ops.scale_bulk_wait(
            max(
                port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY]["port_shutdown_wait_sec"]
                for port in oper_down_ports
            ),
            len(oper_down_ports),
        )
        startup_wait = scenario_ops.scale_bulk_wait(
            max(
                port_attributes_dict[port][SYSTEM_ATTRIBUTES_KEY]["port_startup_wait_sec"]
                for port in oper_down_ports
            ),
            len(oper_down_ports),
        )
        summary["link_bounced"].extend(oper_down_ports)
        scenario_ops.perform_ports_shutdown(duthost, oper_down_ports, shutdown_wait)
        scenario_ops.perform_ports_startup(duthost, oper_down_ports, startup_wait)

    final_status = get_dut_interfaces_status(duthost)
    summary["still_failing"].extend(
        "{}: admin={}, oper={}".format(
            port,
            (final_status.get(port) or {}).get("admin", "missing"),
            (final_status.get(port) or {}).get("oper", "missing"),
        )
        for port in sorted(port_attributes_dict)
        if (final_status.get(port) or {}).get("admin") != "up"
        or (final_status.get(port) or {}).get("oper") != "up"
    )

    return summary


# ──────────────────────────────────────────────────────────────────────
# Per-test State Preservation and Restoration
#
# Every facet value is a ``{key: value}`` dict per port (e.g. port status
# ``{"admin": "up", "oper": "up"}``, loopback ``{"host-side-input": "off", ...}``).
# The generic engine below diffs per key, groups drifted ports by
# ``(key, target)``, and calls the facet's ``apply`` once per group, so a facet
# only defines how to read its state and how to apply one target value to a
# batch of ports.
# ──────────────────────────────────────────────────────────────────────

# Fallback per-port waits for ports without SYSTEM_ATTRIBUTES, so the facets
# are usable from any test category; these match the system.json defaults.
_DEFAULT_PORT_SHUTDOWN_WAIT_SEC = 5
_DEFAULT_PORT_STARTUP_WAIT_SEC = 60

StateFacet = namedtuple("StateFacet", ("name", "read", "apply", "key_order"), defaults=(None,))
StateFacet.__doc__ = """One independently captured and restored piece of port state.

Fields:
    name: unique facet name; keys the snapshot's ``values`` / ``errors``.
    read: ``read(duthost, ports, port_attributes_dict)`` ->
        ``({port: {key: value}}, {port: error_str})``. Read every port in as
        few DUT calls as possible; :func:`table_reader` builds one from a
        single whole-DUT query.
    apply: ``apply(duthost, ports, key, target, port_attributes_dict)`` ->
        ``[failure_str]``. Drive ``key`` to ``target`` on every port in
        ``ports`` (batched where the CLI allows).
    key_order: optional sequence of keys restored one at a time, in order,
        re-reading the facet before each so later keys see the effect of
        earlier ones (e.g. ``("admin", "oper")``). ``None`` treats keys as
        independent: one read, every drifted key applied.

Build common shapes with :func:`db_field_facet`, :func:`db_reader`,
:func:`table_reader`, and :func:`command_apply`; write ``read`` / ``apply``
by hand only for state those cannot express (e.g. decoded EEPROM bits).
"""

PortStateSnapshot = namedtuple("PortStateSnapshot", ("ports", "facets", "values", "errors"))
PortStateSnapshot.__doc__ = """Result of :func:`capture_port_state`.

Fields:
    ports: tuple of captured logical ports.
    facets: tuple of :class:`StateFacet` captured, in restore order.
    values: ``{facet_name: {port: {key: value}}}`` for captured ports.
    errors: ``{facet_name: {port: error_str}}`` for ports that could not be
        captured; those ports are skipped by verify and restore for that facet.
"""


def table_reader(fetch, source):
    """Build a :class:`StateFacet` ``read`` from one whole-DUT table query.

    Args:
        fetch: ``fetch(duthost)`` -> ``({port: {key: value}}, err)``; one DUT call
            covering every port.
        source: human-readable source name used in "not reported" errors.

    Returns:
        callable: ``read(duthost, ports, port_attributes_dict)`` returning
        ``({port: {key: value}}, {port: error_str})`` for the requested ports.
    """
    def read(duthost, ports, port_attributes_dict):
        table, err = fetch(duthost)
        if err:
            return {}, {port: err for port in ports}
        values = {port: table[port] for port in ports if port in table}
        errors = {port: f"not reported by {source}" for port in ports if port not in table}
        return values, errors
    return read


def db_reader(db, table, fields, sep="|"):
    """Build a :class:`StateFacet` ``read`` over DB hash fields, one dump per ASIC namespace.

    Args:
        db: database name, e.g. ``"CONFIG_DB"``, ``"STATE_DB"``, ``"APPL_DB"``.
        table: table name; rows are keyed by logical port.
        fields: hash fields to capture; each becomes one facet key.
        sep: table/key separator (``"|"`` for CONFIG/STATE_DB, ``":"`` for APPL_DB).

    Returns:
        callable: ``read(duthost, ports, port_attributes_dict)`` returning
        ``({port: {field: value}}, {port: error_str})``. An absent row or field
        is captured as ``None`` (a valid "unset" state); only a failed dump is
        an error.
    """
    fields = tuple(fields)

    def read(duthost, ports, port_attributes_dict):
        values, errors = {}, {}
        for namespace, ns_ports in db_helpers.group_ports_by_namespace(duthost, ports).items():
            rows, err = db_helpers.get_db_table(duthost, db, table, namespace=namespace, sep=sep)
            for port in ns_ports:
                if err:
                    errors[port] = err
                else:
                    row = rows.get(port, {})
                    values[port] = {field: row.get(field) for field in fields}
        return values, errors
    return read


def ns_flag(namespace):
    """Return ``" -n <namespace>"`` for a truthy namespace, else ``""`` (for ``config`` commands)."""
    return f" -n {namespace}" if namespace else ""


def command_apply(build_cmd):
    """Build a :class:`StateFacet` ``apply`` that runs one command per port in a single DUT call.

    Args:
        build_cmd: ``build_cmd(port, key, target, namespace)`` -> command string,
            or ``None`` when ``target`` cannot be applied (e.g. unsetting a
            field the CLI cannot clear). ``namespace`` is ``None`` on
            single-ASIC DUTs; see :func:`ns_flag`.

    Returns:
        callable: ``apply(duthost, ports, key, target, port_attributes_dict)``
        returning one failure string per port that could not be applied.
    """
    def apply(duthost, ports, key, target, port_attributes_dict):
        cmds, failures = {}, []
        for port in ports:
            cmd = build_cmd(port, key, target, db_helpers.resolve_port_namespace(duthost, port))
            if cmd is None:
                failures.append(f"{port}: cannot apply {key}={target!r}")
            else:
                cmds[port] = cmd
        errors = cli_helpers.run_per_port_commands(duthost, cmds) if cmds else {}
        return failures + [err for err in errors.values() if err]
    return apply


def db_field_facet(name, db, table, fields, build_cmd, sep="|", key_order=None):
    """Build a facet for DB fields that a per-port CLI command restores.

    Args:
        name: unique facet name.
        db, table, fields, sep: see :func:`db_reader`.
        build_cmd: see :func:`command_apply`.
        key_order: see :class:`StateFacet`.

    Returns:
        StateFacet: ready to pass to :func:`capture_port_state` or
        :func:`register_facet`.
    """
    return StateFacet(name, db_reader(db, table, fields, sep), command_apply(build_cmd), key_order)


def _fetch_port_status(duthost):
    """Return ``({port: {"admin", "oper"}}, None)`` from ``show interface description``."""
    return {
        port: {"admin": status.get("admin"), "oper": status.get("oper")}
        for port, status in get_dut_interfaces_status(duthost).items()
    }, None


def _fetch_lpmode(duthost):
    """Return ``({port: {"lpmode": "On"|"Off"|...}}, err)`` from ``sfputil show lpmode``."""
    lpmode_by_port, err = cli_helpers.sfputil_show_lpmode(duthost)
    if err:
        return None, err
    return {port: {"lpmode": value} for port, value in lpmode_by_port.items()}, None


def loopback_lanes_by_mode(port_attributes_dict, port):
    """Resolve the lanes each CMIS loopback mode acts on for ``port``.

    Args:
        port_attributes_dict: resolved per-port attributes.
        port: logical port name.

    Returns:
        dict: ``{mode: lanes}`` - host lanes (``host_lane_mask``) for host-side
        modes, media lanes (``media_lane_mask``) for media-side modes. Modes
        whose lane mask is absent or invalid are omitted, so a port with only a
        ``host_lane_mask`` still resolves its host-side modes.

    Raises:
        ValueError: when neither lane mask resolves.
    """
    base_attrs = port_attributes_dict.get(port, {}).get(BASE_ATTRIBUTES_KEY, {})
    lanes_by_side = {}
    mask_errors = []
    for side, mask_key in (("host", "host_lane_mask"), ("media", MEDIA_LANE_MASK_KEY)):
        try:
            lanes_by_side[side] = lanes_from_mask(base_attrs[mask_key])
        except (KeyError, ValueError) as error:
            mask_errors.append(f"{mask_key}: {error!r}")
    if not lanes_by_side:
        raise ValueError("; ".join(mask_errors))
    return {
        mode: lanes_by_side[side]
        for mode in CMIS_LOOPBACK_MODE_OFFSET
        for side in ["host" if mode in CMIS_HOST_SIDE_LOOPBACK_MODES else "media"]
        if side in lanes_by_side
    }


def _read_loopback(duthost, ports, port_attributes_dict):
    """Read every resolvable CMIS loopback mode's on/off/partial state from one batched page 13h read."""
    values, errors, lanes_by_port = {}, {}, {}
    for port in ports:
        try:
            lanes_by_port[port] = loopback_lanes_by_mode(port_attributes_dict, port)
        except ValueError as error:
            errors[port] = f"cannot resolve loopback lanes: {error}"
    reads = cli_helpers.sfputil_read_eeprom_batch(
        duthost, list(lanes_by_port),
        page=f"0x{CMIS_PAGE_13_LOOPBACK_PAGE:02X}",
        offset=CMIS_PAGE_13_LOOPBACK_START, size=CMIS_PAGE_13_LOOPBACK_SIZE,
    )
    for port, lanes_by_mode in lanes_by_port.items():
        byte_map, err = reads[port]
        states = {} if err else {
            mode: loopback_lane_state(byte_map, mode, lanes) for mode, lanes in lanes_by_mode.items()
        }
        if err or None in states.values():
            errors[port] = err or "loopback control bytes missing from page 13h read"
        else:
            values[port] = states
    return values, errors


def _apply_port_status(duthost, ports, key, target, port_attributes_dict):
    """Drive admin state with a bulk shutdown/startup, or recover oper-up with one bulk bounce."""
    def _wait(attr, default):
        return scenario_ops.scale_bulk_wait_for_ports(port_attributes_dict, ports, attr, default)

    shutdown_wait = _wait("port_shutdown_wait_sec", _DEFAULT_PORT_SHUTDOWN_WAIT_SEC)
    startup_wait = _wait("port_startup_wait_sec", _DEFAULT_PORT_STARTUP_WAIT_SEC)
    if key == "admin" and target == "down":
        return scenario_ops.perform_ports_shutdown(duthost, ports, shutdown_wait)
    if key == "admin" and target == "up":
        # Oper-up wait failures are re-evaluated by the following "oper" pass.
        scenario_ops.perform_ports_startup(duthost, ports, startup_wait)
        return []
    if key == "oper" and target == "up":
        logger.info("Restoration: bouncing %d oper-down port(s): %s", len(ports), ports)
        return (scenario_ops.perform_ports_shutdown(duthost, ports, shutdown_wait)
                + scenario_ops.perform_ports_startup(duthost, ports, startup_wait))
    if key == "oper":
        # A link cannot be forced oper-down while admin-up; verify reports the drift.
        logger.info("Restoration: not forcing oper=%r on %s", target, ports)
        return []
    return [f"{port}: cannot drive {key} to {target!r}" for port in ports]


def _apply_lpmode(duthost, ports, key, target, port_attributes_dict):
    """Set each port's module to the target On/Off low-power mode (sfputil is per port)."""
    if target not in ("On", "Off"):
        return [f"{port}: cannot restore lpmode {target!r}" for port in ports]
    failures = []
    for port in ports:
        failures += scenario_ops.perform_lpmode_set(duthost, port, low_power=(target == "On"))
    return failures


def _loopback_cmd(port, key, target, namespace):
    """Build ``sfputil debug loopback`` for one port; ``partial`` cannot be restored."""
    if target not in ("on", "off"):
        return None
    return cli_helpers.sfputil_debug_loopback_cmd(port, key, target == "on")


# CONFIG_DB PORT field -> ``config interface transceiver`` subcommand.
_CCMIS_TUNING_SUBCOMMANDS = {"laser_freq": "frequency", "tx_power": "tx_power"}


def _ccmis_tuning_cmd(port, key, target, namespace):
    """Build the C-CMIS tuning command; an unset (``None``) value cannot be re-applied."""
    if target is None:
        return None
    return f"sudo config interface{ns_flag(namespace)} transceiver {_CCMIS_TUNING_SUBCOMMANDS[key]} {port} {target}"


PORT_STATUS_FACET = StateFacet(
    "port_status", table_reader(_fetch_port_status, "show interface description"), _apply_port_status,
    key_order=("admin", "oper"),
)
LPMODE_FACET = StateFacet("lpmode", table_reader(_fetch_lpmode, "sfputil show lpmode"), _apply_lpmode)
LOOPBACK_FACET = StateFacet("loopback", _read_loopback, command_apply(_loopback_cmd))
CCMIS_TUNING_FACET = db_field_facet(
    "ccmis_tuning", "CONFIG_DB", "PORT", list(_CCMIS_TUNING_SUBCOMMANDS), _ccmis_tuning_cmd,
)
DEFAULT_STATE_FACETS = (LPMODE_FACET, PORT_STATUS_FACET)

_FACET_REGISTRY = {}


def register_facet(facet, replace=False):
    """Make ``facet`` selectable by name in capture / verify / restore calls.

    Args:
        facet: :class:`StateFacet` to register.
        replace: allow overriding an existing registration with the same name.

    Returns:
        StateFacet: ``facet``, so a module can register at definition time.

    Raises:
        ValueError: when a different facet already uses the name and
            ``replace`` is False.
    """
    existing = _FACET_REGISTRY.get(facet.name)
    if existing is not None and existing is not facet and not replace:
        raise ValueError(f"facet {facet.name!r} is already registered")
    _FACET_REGISTRY[facet.name] = facet
    return facet


def get_facet(name):
    """Return the registered :class:`StateFacet` called ``name``.

    Raises:
        KeyError: listing the registered names when ``name`` is unknown.
    """
    try:
        return _FACET_REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown state facet {name!r}; registered: {sorted(_FACET_REGISTRY)}")


for _facet in (PORT_STATUS_FACET, LPMODE_FACET, LOOPBACK_FACET, CCMIS_TUNING_FACET):
    register_facet(_facet)


def _select_facets(snapshot, facets):
    """Return the snapshot's facets filtered by ``facets`` (names or StateFacet), in snapshot order."""
    if facets is None:
        return list(snapshot.facets)
    wanted = {facet if isinstance(facet, str) else facet.name for facet in facets}
    unknown = wanted - {facet.name for facet in snapshot.facets}
    if unknown:
        raise ValueError(f"facets {sorted(unknown)} were not captured in this snapshot")
    return [facet for facet in snapshot.facets if facet.name in wanted]


def capture_errors(snapshot):
    """List every port/facet the snapshot could not capture.

    Args:
        snapshot: :class:`PortStateSnapshot` from :func:`capture_port_state`.

    Returns:
        list[str]: ``"<port>: <facet>: <error>"`` entries; empty when complete.
    """
    return [
        f"{port}: {name}: {error}"
        for name, port_errors in snapshot.errors.items()
        for port, error in natsorted(port_errors.items())
    ]


def capture_port_state(duthost, ports, port_attributes_dict, facets=DEFAULT_STATE_FACETS):
    """State Capture phase: snapshot the selected facets for ``ports``.

    Args:
        duthost: SONiC DUT host fixture.
        ports: logical ports to capture.
        port_attributes_dict: resolved per-port attributes (facets may read
            lane masks and wait attributes from it).
        facets: sequence of :class:`StateFacet` objects and/or registered
            facet names. Restore runs in this order, except
            ``PORT_STATUS_FACET``, which always restores last so link recovery
            follows every module-level change. Defaults to LPMode + port status.

    Returns:
        PortStateSnapshot: captured values and per-port capture errors. Check
        :func:`capture_errors` when the test needs a complete snapshot.
    """
    ports = tuple(natsorted(ports))
    facets = tuple(get_facet(f) if isinstance(f, str) else f for f in facets)
    facets = tuple(f for f in facets if f is not PORT_STATUS_FACET) + tuple(
        f for f in facets if f is PORT_STATUS_FACET
    )
    names = [facet.name for facet in facets]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate facet names: {names}")
    values, errors = {}, {}
    for facet in facets:
        values[facet.name], errors[facet.name] = facet.read(duthost, list(ports), port_attributes_dict)
        logger.info("State Capture %s: %d captured, %d error(s)",
                    facet.name, len(values[facet.name]), len(errors[facet.name]))
    return PortStateSnapshot(ports, facets, values, errors)


def _read_drift(duthost, snapshot, facet, ports, port_attributes_dict, keys=None):
    """Re-read ``facet`` and diff it against the snapshot, per key.

    Args:
        keys: optional subset of keys to compare; ``None`` compares all.

    Returns:
        tuple(list, dict): ``[(port, key, original, current)]`` for drifted
        keys, and ``{port: error}`` for captured ports that could not be re-read.
    """
    originals = snapshot.values[facet.name]
    candidates = [port for port in ports if port in originals]
    if not candidates:
        return [], {}
    current, errors = facet.read(duthost, candidates, port_attributes_dict)
    drift = [
        (port, key, original, current[port].get(key))
        for port in natsorted(current)
        for key, original in sorted(originals[port].items())
        if (keys is None or key in keys) and current[port].get(key) != original
    ]
    return drift, errors


def expect_port_state(snapshot, facet, overrides):
    """Derive an expected-state snapshot by overriding captured values.

    Lets a test verify an intermediate state (e.g. "loopback mode X is on",
    "lpmode is On") with :func:`verify_port_state`, while keys it does not
    override must still match the capture.

    Args:
        snapshot: captured :class:`PortStateSnapshot`; left unmodified.
        facet: facet name or :class:`StateFacet` present in ``snapshot``.
        overrides: ``{port: {key: value}}`` merged over the captured values;
            only ports captured for ``facet`` may be overridden.

    Returns:
        PortStateSnapshot: a copy whose ``facet`` values include ``overrides``.

    Raises:
        ValueError: when ``facet`` or an overridden port was not captured.
    """
    name = facet if isinstance(facet, str) else facet.name
    _select_facets(snapshot, [name])
    captured = snapshot.values[name]
    missing = set(overrides) - set(captured)
    if missing:
        raise ValueError(f"cannot override uncaptured {name} ports {natsorted(missing)}")
    values = dict(snapshot.values)
    values[name] = {
        port: {**port_values, **overrides.get(port, {})} for port, port_values in captured.items()
    }
    return snapshot._replace(values=values)


def verify_port_state(duthost, snapshot, port_attributes_dict, ports=None, facets=None, keys=None):
    """Compare live state with ``snapshot``; read-only.

    Args:
        duthost: SONiC DUT host fixture.
        snapshot: :class:`PortStateSnapshot` holding the expected state.
        port_attributes_dict: resolved per-port attributes.
        ports: optional subset of ``snapshot.ports``; defaults to all.
        facets: optional subset of facet names / StateFacets; defaults to all.
        keys: optional subset of facet keys to compare (e.g. one loopback
            mode); defaults to every captured key.

    Returns:
        list[str]: one failure per drifted (port, facet key) or unreadable
        (port, facet); empty when the live state matches. Ports not captured
        are skipped.
    """
    ports = list(snapshot.ports if ports is None else ports)
    keys = None if keys is None else set(keys)
    failures = []
    for facet in _select_facets(snapshot, facets):
        drift, errors = _read_drift(duthost, snapshot, facet, ports, port_attributes_dict, keys)
        failures += [f"{port}: {facet.name}: re-read failed: {err}" for port, err in natsorted(errors.items())]
        failures += [
            f"{port}: {facet.name}.{key} is {current!r}, expected {original!r}"
            for port, key, original, current in drift
        ]
    return failures


def restore_port_state(duthost, snapshot, port_attributes_dict, ports=None, facets=None):
    """Restore drifted ports to ``snapshot``, facet by facet in snapshot order.

    For each facet, drifted keys are grouped by ``(key, target)`` and the
    facet's ``apply`` runs once per group. Facets with a ``key_order`` restore
    one key per pass, re-reading in between. Only drifted ports are touched,
    so a call with nothing drifted makes no configuration change.

    Args:
        duthost: SONiC DUT host fixture.
        snapshot: :class:`PortStateSnapshot` holding the state to restore.
        port_attributes_dict: resolved per-port attributes.
        ports: optional subset of ``snapshot.ports``; defaults to all.
        facets: optional subset of facet names / StateFacets; defaults to all.

    Returns:
        list[str]: restore operation failures; run :func:`verify_port_state`
        afterward to confirm the final state.
    """
    ports = list(snapshot.ports if ports is None else ports)
    failures = []
    for facet in _select_facets(snapshot, facets):
        key_passes = [{key} for key in facet.key_order] if facet.key_order else [None]
        for pass_index, keys in enumerate(key_passes):
            drift, errors = _read_drift(duthost, snapshot, facet, ports, port_attributes_dict, keys)
            if pass_index == 0:
                failures += [
                    f"{port}: {facet.name}: re-read failed: {err}" for port, err in natsorted(errors.items())
                ]
            groups = {}
            for port, key, original, _current in drift:
                groups.setdefault((key, original), []).append(port)
            for (key, target), group in sorted(groups.items(), key=lambda item: str(item[0])):
                logger.warning("Restoration: %s.%s -> %r on %s", facet.name, key, target, group)
                failures += [
                    f"{facet.name}.{key}: {f}"
                    for f in facet.apply(duthost, group, key, target, port_attributes_dict)
                ]
    return failures


class PreservedPortState(object):
    """Handle yielded by :func:`preserved_port_state`.

    Attributes:
        snapshot: the :class:`PortStateSnapshot` being preserved.
        failures: restoration / recovery / verification failures, filled on exit.
        recovery_result: the Standard Port Recovery result dict, or ``None``.
    """

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.failures = []
        self.recovery_result = None
        self._undo_fns = []

    def register_undo(self, label, undo_fn):
        """Register a test-specific undo step for state no facet covers.

        Args:
            label: short name used in failure messages.
            undo_fn: zero-argument callable returning a list of failure strings
                (or ``None``). Undo steps run in reverse registration order
                before facet restoration.
        """
        self._undo_fns.append((label, undo_fn))


@contextlib.contextmanager
def preserved_port_state(duthost, snapshot, port_attributes_dict, *, recovery_kwargs=None, verify=True):
    """Run the body, then always restore ``snapshot`` and verify recovery.

    Exit sequence, executed on pass or fail: registered undo steps ->
    :func:`restore_port_state` -> Standard Port Recovery and Verification on
    ``snapshot.ports`` (when ``recovery_kwargs`` is given) ->
    :func:`verify_port_state`. Each stage is isolated, so one raising does not
    skip the next; exceptions from the body still propagate.

    Args:
        duthost: SONiC DUT host fixture.
        snapshot: :class:`PortStateSnapshot` captured before the body; capture
            outside the ``with`` so pre-test validation can exit early without
            triggering restoration.
        port_attributes_dict: resolved per-port attributes.
        recovery_kwargs: keyword arguments forwarded to
            :func:`standard_port_recovery_and_verification` (e.g.
            ``link_up_timeout_sec``, ``health_baseline``,
            ``lport_to_first_subport_mapping``, ``port_peers``); ``None`` skips
            the recovery procedure.
        verify: when False, skip the final :func:`verify_port_state`.

    Yields:
        PreservedPortState: read ``.failures`` after the ``with`` block.
    """
    preserved = PreservedPortState(snapshot)

    def _stage(label, fn):
        try:
            return fn() or []
        except Exception as error:
            logger.exception("State Restoration stage %s raised", label)
            return [f"{label} raised {type(error).__name__}: {error}"]

    try:
        yield preserved
    finally:
        for label, undo_fn in reversed(preserved._undo_fns):
            preserved.failures += [f"[undo {label}] {f}" for f in _stage(f"undo {label}", undo_fn)]
        preserved.failures += [
            f"[restoration] {f}"
            for f in _stage("restore", lambda: restore_port_state(duthost, snapshot, port_attributes_dict))
        ]
        if recovery_kwargs is not None:
            def _recover():
                preserved.recovery_result = standard_port_recovery_and_verification(
                    duthost, list(snapshot.ports), port_attributes_dict, **recovery_kwargs
                )
                result = preserved.recovery_result
                return [] if result["passed"] else [result["details"]]
            preserved.failures += [f"[recovery] {f}" for f in _stage("recovery", _recover)]
        if verify:
            preserved.failures += [
                f"[restoration verify] {f}"
                for f in _stage("verify", lambda: verify_port_state(duthost, snapshot, port_attributes_dict))
            ]
        if preserved.failures:
            logger.warning("State Preservation and Restoration reported %d issue(s):\n%s",
                           len(preserved.failures), "\n".join(preserved.failures))
