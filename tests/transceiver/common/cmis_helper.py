"""CMIS page-level decode helpers and register-map constants.

Holds the CMIS-specific page constants and decoders that are not part of the
SFF-8024 family dispatch. The per-family vendor-field offsets and the family
classifier live in ``tests.transceiver.common.eeprom_decode`` instead.
"""

__all__ = [
    # ── Constants: CMIS upper page 11h (DataPath state) ────────────────────
    "CMIS_DP_STATE_START",
    "CMIS_DP_STATE_ACTIVATED",
    "CMIS_DP_STATE_DEACTIVATED",
    "CMIS_DP_STATE_NIBBLE_MASK",
    "CMIS_DP_STATE_LANES_PER_BYTE",

    # ── Constants: CMIS page 01h (CDB capability) ──────────────────────────
    "CMIS_PAGE_01_CDB_CAP_PAGE",
    "CMIS_PAGE_01_CDB_CAP_OFFSET",
    "CMIS_PAGE_01_CDB_BG_MODE_BIT",

    # ── Constants: CMIS page 11h (Active Control Set SI) ───────────────────
    "CMIS_PAGE_11_ACS_SI_PAGE",
    "CMIS_PAGE_11_ACS_SI_START",
    "CMIS_PAGE_11_ACS_SI_SIZE",
    "CMIS_ACS_SI_REGISTER_START",

    # ── Constants: CMIS page 13h (loopback controls) ───────────────────────
    "CMIS_PAGE_13_LOOPBACK_PAGE",
    "CMIS_PAGE_13_LOOPBACK_START",
    "CMIS_PAGE_13_LOOPBACK_SIZE",
    "CMIS_LOOPBACK_MODE_OFFSET",
    "CMIS_HOST_SIDE_LOOPBACK_MODES",

    # ── Public helpers ──────────────────────────────────────────────────────
    "check_dp_state",
    "check_dp_state_activated",
    "lanes_from_mask",
    "decode_lane_nibble",
    "compare_optics_si_settings",
    "loopback_lane_state",
]

# CMIS upper page 11h: DataPath state registers (2 lanes per byte, nibble-encoded).
# A module that hosts N lanes consumes ceil(N / 2) bytes starting at CMIS_DP_STATE_START;
# the per-test call site passes the actual per-port lane count.
CMIS_DP_STATE_START = 0x80
CMIS_DP_STATE_LANES_PER_BYTE = 2
CMIS_DP_STATE_ACTIVATED = 0x4   # DPActivated nibble value per CMIS spec
CMIS_DP_STATE_DEACTIVATED = 0x1   # DPDeactivated nibble value per CMIS spec
CMIS_DP_STATE_NIBBLE_MASK = 0x0F

# CMIS Page 01h: CDB capability register
# CMIS global byte 163 (decimal) = 0xA3 (hex).
# The CMIS standard numbers bytes 0-255 within a page: 0-127 = lower page,
# 128-255 = upper page. Byte 163 is in the upper page at absolute address 0xA3.
# In sfputil's 256-byte page view the upper page starts at 0x80, so
# sfputil offset = 0xA3 directly (= upper-page local offset 0x23 = 35 from 0x80).
# Bit 5 of this byte advertises CDB background mode support.
CMIS_PAGE_01_CDB_CAP_PAGE = 0x01   # Page 01h (Capabilities Advertising)
CMIS_PAGE_01_CDB_CAP_OFFSET = 0xA3   # sfputil offset = CMIS global byte 163 (decimal)
CMIS_PAGE_01_CDB_BG_MODE_BIT = 5      # bit 5: CDB background mode support (1=yes, 0=no)


def check_dp_state(page_11_data, num_lanes, expected_state, state_label=None):
    """Verify ``num_lanes`` lanes report ``expected_state`` in CMIS page 11h.

    Each byte starting at CMIS_DP_STATE_START encodes two lanes as nibbles
    (bits 3:0 = lower lane, bits 7:4 = upper lane). ``expected_state`` is the
    CMIS DataPath-state nibble value to assert (e.g. CMIS_DP_STATE_ACTIVATED,
    CMIS_DP_STATE_DEACTIVATED). We check exactly the lanes the module hosts
    (``num_lanes``), not a fixed 8-lane window — this avoids spurious failures
    for non-existent lanes on 4-lane 400G CMIS, 2-lane 200G, etc.

    Args:
        page_11_data: ``{address(int): byte_value(int)}`` map for CMIS page 11h.
        num_lanes: number of host lanes the module provisions.
        expected_state: the DataPath-state nibble value every lane must report.
        state_label: optional human-readable name for ``expected_state`` (e.g.
            ``"DPActivated"``) used in failure messages; defaults to the hex value.

    Returns a list of failure description strings (empty if all lanes report
    ``expected_state``).
    """
    expected_hex = format(expected_state, "X")
    expected_desc = (
        f"{state_label} (0x{expected_hex})" if state_label else f"0x{expected_hex}"
    )

    failures = []
    if num_lanes <= 0:
        failures.append(f"invalid lane count {num_lanes} for DP-state check")
        return failures

    num_bytes = (num_lanes + CMIS_DP_STATE_LANES_PER_BYTE - 1) // CMIS_DP_STATE_LANES_PER_BYTE
    for byte_idx in range(num_bytes):
        addr = CMIS_DP_STATE_START + byte_idx
        byte_val = page_11_data.get(addr)
        if byte_val is None:
            failures.append(
                f"DataPath state byte missing at page 11h offset 0x{format(addr, '02X')}"
            )
            continue
        for nibble_idx in range(CMIS_DP_STATE_LANES_PER_BYTE):
            lane = byte_idx * CMIS_DP_STATE_LANES_PER_BYTE + nibble_idx + 1
            if lane > num_lanes:
                break
            state = (byte_val >> (nibble_idx * 4)) & CMIS_DP_STATE_NIBBLE_MASK
            if state != expected_state:
                failures.append(
                    f"Lane {lane}: expected {expected_desc}, "
                    f"got 0x{format(state, 'X')} at page 11h offset 0x{format(addr, '02X')}"
                )
    return failures


def check_dp_state_activated(page_11_data, num_lanes):
    """Verify ``num_lanes`` lanes report DPActivated in CMIS page 11h.

    Thin wrapper over :func:`check_dp_state` pinned to
    CMIS_DP_STATE_ACTIVATED; preserved as the named entry point for the
    DPActivated check used across the EEPROM tests.
    """
    return check_dp_state(
        page_11_data, num_lanes, CMIS_DP_STATE_ACTIVATED, state_label="DPActivated"
    )


# CMIS page 11h: Active Control Set (ACS) signal-integrity registers for DP
# lanes 1-8, as applied by the module after DataPath init. The registers are
# nibble-encoded, two lanes per byte (bits 3:0 = odd lane, bits 7:4 = even
# lane), so each parameter spans 4 bytes. Offsets are sfputil page-view
# offsets (upper page starts at 0x80). One read of CMIS_PAGE_11_ACS_SI_SIZE
# bytes from CMIS_PAGE_11_ACS_SI_START covers every parameter below.
CMIS_PAGE_11_ACS_SI_PAGE = 0x11
CMIS_ACS_SI_REGISTER_START = {
    "FixedInputEqTargetTx": 217,
    "OutputEqPreCursorTargetRx": 223,
    "OutputEqPostCursorTargetRx": 227,
    "OutputAmplitudeTargetRx": 231,
}
CMIS_PAGE_11_ACS_SI_START = min(CMIS_ACS_SI_REGISTER_START.values())
CMIS_PAGE_11_ACS_SI_SIZE = max(CMIS_ACS_SI_REGISTER_START.values()) + 4 - CMIS_PAGE_11_ACS_SI_START

# CMIS page 13h: per-lane loopback enable bitmaps (bit N-1 = lane N). Host-side
# modes are indexed by host lane, media-side modes by media lane.
CMIS_PAGE_13_LOOPBACK_PAGE = 0x13
CMIS_LOOPBACK_MODE_OFFSET = {
    "media-side-output": 180,
    "media-side-input": 181,
    "host-side-output": 182,
    "host-side-input": 183,
}
CMIS_PAGE_13_LOOPBACK_START = min(CMIS_LOOPBACK_MODE_OFFSET.values())
CMIS_PAGE_13_LOOPBACK_SIZE = len(CMIS_LOOPBACK_MODE_OFFSET)
CMIS_HOST_SIDE_LOOPBACK_MODES = frozenset(("host-side-input", "host-side-output"))


def lanes_from_mask(raw_mask):
    """Return the 1-indexed lanes selected by a hex lane mask.

    Args:
        raw_mask: lane mask as an int or hex string (e.g. ``"0x0F"``); bit
            ``N-1`` selects lane ``N``. Must select at least one of lanes 1-8.

    Returns:
        list[int]: selected lanes in ascending order.

    Raises:
        ValueError: when the mask is unparsable or selects no lane in 1-8.
    """
    try:
        mask = raw_mask if isinstance(raw_mask, int) else int(str(raw_mask), 16)
    except (TypeError, ValueError):
        raise ValueError(f"unparsable lane mask {raw_mask!r}")
    if not 0 < mask <= 0xFF:
        raise ValueError(f"lane mask {raw_mask!r} must select lanes 1-8")
    return [lane for lane in range(1, 9) if mask & (1 << (lane - 1))]


def decode_lane_nibble(byte_map, start_offset, lane):
    """Decode one lane's nibble from a CMIS two-lanes-per-byte register block.

    Args:
        byte_map: ``{offset(int): byte_value(int)}`` from ``sfputil read-eeprom``.
        start_offset: offset of the block's first byte (lanes 1-2).
        lane: 1-indexed lane number.

    Returns:
        int | None: the lane's 4-bit value, or ``None`` when the byte is absent.
    """
    byte_val = byte_map.get(start_offset + (lane - 1) // 2)
    if byte_val is None:
        return None
    return (byte_val >> (4 * ((lane - 1) % 2))) & CMIS_DP_STATE_NIBBLE_MASK


def compare_optics_si_settings(expected_settings, acs_byte_map, lanes):
    """Compare configured optics SI intent with page 11h Active Control Set bytes.

    Args:
        expected_settings: ``optics_si_settings`` attribute, shaped like the
            optics_si_settings.json leaf: ``{parameter: {parameter<lane>: value}}``,
            e.g. ``{"OutputAmplitudeTargetRx": {"OutputAmplitudeTargetRx1": 1}}``.
            Lane suffixes are 1-indexed module lanes; values are ints or
            int-parsable strings.
        acs_byte_map: ``{offset: byte}`` read from page 11h starting at
            ``CMIS_PAGE_11_ACS_SI_START``.
        lanes: 1-indexed host lanes owned by the logical port; lane keys outside
            this set are not compared (they belong to a sibling subport).

    Returns:
        tuple(list[str], int): discrepancy strings (empty on full match) and the
        number of lane values compared.
    """
    problems = []
    compared = 0
    lane_set = set(lanes)
    for parameter, lane_values in sorted(expected_settings.items()):
        start = CMIS_ACS_SI_REGISTER_START.get(parameter)
        if start is None:
            problems.append(f"unsupported optics SI parameter {parameter!r}")
            continue
        if not isinstance(lane_values, dict):
            problems.append(f"{parameter}: expected per-lane dict, got {lane_values!r}")
            continue
        for lane_key, raw_expected in sorted(lane_values.items()):
            suffix = lane_key[len(parameter):] if lane_key.startswith(parameter) else ""
            try:
                lane = int(suffix)
                expected = raw_expected if isinstance(raw_expected, int) else int(str(raw_expected), 0)
            except ValueError:
                problems.append(f"{parameter}: unparsable entry {lane_key!r}={raw_expected!r}")
                continue
            if lane not in lane_set:
                continue
            compared += 1
            actual = decode_lane_nibble(acs_byte_map, start, lane)
            if actual is None:
                problems.append(f"{lane_key}: EEPROM byte missing at page 11h offset {start + (lane - 1) // 2}")
            elif actual != expected:
                problems.append(f"{lane_key}: attribute={expected}, EEPROM={actual}")
    return problems, compared


def loopback_lane_state(loopback_byte_map, mode, lanes):
    """Summarize a loopback mode's page 13h enable bits across ``lanes``.

    Args:
        loopback_byte_map: ``{offset: byte}`` read from page 13h starting at
            ``CMIS_PAGE_13_LOOPBACK_START``.
        mode: one of ``CMIS_LOOPBACK_MODE_OFFSET``.
        lanes: 1-indexed lanes owned by the logical port.

    Returns:
        str | None: ``"on"`` when every lane is enabled, ``"off"`` when none
        is, ``"partial"`` otherwise, or ``None`` when the byte is absent.
    """
    byte_val = loopback_byte_map.get(CMIS_LOOPBACK_MODE_OFFSET[mode])
    if byte_val is None:
        return None
    enabled = [bool(byte_val & (1 << (lane - 1))) for lane in lanes]
    if all(enabled):
        return "on"
    return "off" if not any(enabled) else "partial"
