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


def test_primary_gga_still_parsed():
    gga = (
        "$GPGGA,073111.00,3032.29402340,N,11420.96840904,E,"
        "1,30,0.6,71.988,M,-14.176,M,,*7B"
    )
    r = parse_sentence(gga)
    assert r is not None
    assert r["type"] == "GGA"
