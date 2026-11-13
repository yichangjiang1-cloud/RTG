"""$PSAT,GGA2 副天线 NMEA 解析测试。"""

from src.driver.nmea_parser import parse_psat_gga2, parse_sentence


SAMPLE = (
    "$PSAT,GGA2,073111.00,3032.29402344,N,11420.96840891,E,"
    "1,30,0.6,71.989,M,-14.176,M,,*6C"
)


def test_parse_psat_gga2_direct():
    r = parse_psat_gga2(SAMPLE)
    assert r is not None
    assert r["type"] == "GGA2"
    assert abs(r["lat"] - 30.538233724) < 1e-6
    assert abs(r["lon"] - 114.349473482) < 1e-6
    assert abs(r["alt"] - 71.989) < 1e-3
    assert r["fix_quality"] == 1
    assert r["num_sats"] == 30


def test_parse_sentence_routes_psat_gga2():
    r = parse_sentence(SAMPLE)
    assert r is not None
    assert r["type"] == "GGA2"


def test_empty_gga2_is_not_a_position():
    """无解的空 GGA2 不能解析成纬度 0、经度 0。"""
    blank = "$PSAT,GGA2,014934.100,,,,,0,,,,M,,M,,*6D"
    assert parse_psat_gga2(blank) is None
    assert parse_sentence(blank) is None


def _nmea(body: str) -> str:
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


def test_second_epoch_gga_does_not_replace_secondary():
    """一个包里叠两个历元时，后一条主天线 GGA 不能把小车2写成小车1。"""
    from src.core.event_bus import EventBus
    from src.driver.gnss_receiver import GnssReceiver

    primary = _nmea(
        "GNGGA,014934.100,3212.10922790,N,11903.30481189,E,4,32,0.5,31.625,M,2.520,M,0.2,0333"
    )
    secondary = _nmea(
        "PSAT,GGA2,014934.100,3212.10802480,N,11903.30604879,E,4,32,0.5,32.265,M,2.520,M,0.7,0333"
    )
    primary_next = _nmea(
        "GNGGA,014934.125,3212.10922790,N,11903.30481189,E,4,32,0.5,31.625,M,2.520,M,0.2,0333"
    )
    empty = _nmea("PSAT,GGA2,014934.125,,,,,0,,,,M,,M,,")
    packet = "\r\n".join((primary, secondary, primary_next, empty)) + "\r\n"

    bus = EventBus()
    published = []
    bus.subscribe(bus.TOPIC_GNSS_RAW, published.append)
    rx = GnssReceiver("trolley", "0.0.0.0", 0, bus=bus)
    rx._process_packet(packet)

    assert len(published) == 1
    frame = published[0]
    assert abs(frame.antenna2_latitude - 32.2018004133) < 1e-6
    assert abs(frame.latitude - frame.antenna2_latitude) > 1e-5


def test_primary_gga_still_parsed():
    gga = (
        "$GPGGA,073111.00,3032.29402340,N,11420.96840904,E,"
        "1,30,0.6,71.988,M,-14.176,M,,*7B"
    )
    r = parse_sentence(gga)
    assert r is not None
    assert r["type"] == "GGA"
