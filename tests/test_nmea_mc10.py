"""MC10 / 小车相关 NMEA：VTG、GNHPR 解析。"""

from src.driver.nmea_parser import parse_sentence, parse_vtg, parse_attitude


def test_parse_gnvtg():
    s = "$GNVTG,231.65,T,,M,0.06,N,0.11,K,R*35"
    r = parse_vtg(s)
    assert r is not None
    assert r["type"] == "VTG"
    assert abs(r["course_deg"] - 231.65) < 1e-6
    assert abs(r["speed_mps"] - 0.11 / 3.6) < 1e-6


def test_parse_sentence_vtg():
    r = parse_sentence("$GNVTG,231.65,T,,M,0.06,N,0.11,K,R*35")
    assert r is not None and r["type"] == "VTG"


def test_parse_pashr_ashtech():
    """$PASHR,time,heading,T,roll,pitch,heave,... — 横滚是第 5 字段，不是航向。"""
    s = "$PASHR,023242.10,119.33,T,-0.15,0.56,-0.13,0.502,0.502,0.021,1*36"
    r = parse_attitude(s)
    assert r is not None
    assert abs(r["heading_deg"] - 119.33) < 1e-6
    assert abs(r["roll_deg"] - (-0.15)) < 1e-6
    assert abs(r["pitch_deg"] - 0.56) < 1e-6


def test_parse_gnhpr():
    s = "$GNHPR,064913.00,174.0726,16.0075,,4,25,1.00,0999*75"
    r = parse_attitude(s)
    assert r is not None
    assert r["type"] == "ATTITUDE"
    assert abs(r["heading_deg"] - 174.0726) < 1e-4
    assert abs(r["pitch_deg"] - 16.0075) < 1e-4
    assert r["roll_deg"] == 0.0


def test_parse_sentence_gnhpr():
    r = parse_sentence("$GNHPR,064913.00,174.0726,16.0075,,4,25,1.00,0999*75")
    assert r is not None and r["type"] == "ATTITUDE"


def test_ksxt_still_ignored():
    s = (
        "$KSXT,20260904064913.000,114.34945892,30.53823046,58.7218,"
        "174.07,16.01,231.65,0.105,0.00,3,3,25,26,44.099,76.893,16.598,"
        "-0.082,-0.065,-0.014,,,*19"
    )
    assert parse_sentence(s) is None
