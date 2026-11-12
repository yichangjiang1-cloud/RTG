"""
单元测试：PLC 通信协议 V1.0.0（xlsx）
"""

import math
import struct
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.bridge.plc_protocol_v1 import (
    FRAME_MAGIC,
    FRAME_FOOTER,
    GNSS_TO_PLC_SIZE,
    PLC_TO_GNSS_SIZE,
    PlcProtocolContext,
    encode_fix_status_pair,
    encode_gnss_to_plc,
    decode_plc_to_gnss,
    try_extract_plc_frame,
)
from src.bridge.protocol import encode_frame, decode_frame, FRAME_SIZE
from src.models.positioning import PositionResult, GantryResult, TrolleyResult


def _build_plc_downlink(
    heartbeat: int = 7,
    peer_hb: int = 3,
    seq: int = 99,
    yard: str = "YARD01",
    rtg: str = "RTG001",
) -> bytes:
    body = struct.pack(
        "<8s10sIIhhhhhH10HH",
        yard.encode("ascii")[:8].ljust(8, b"\x00"),
        rtg.encode("ascii")[:10].ljust(10, b"\x00"),
        1000,
        2000,
        100,
        200,
        150,
        80,
        50,
        1,
        *([0] * 10),
        FRAME_FOOTER,
    )
    header = struct.pack("<HHHH", FRAME_MAGIC, heartbeat, peer_hb, seq)
    return header + body


class TestPlcProtocolV1(unittest.TestCase):
    def test_frame_sizes(self):
        self.assertEqual(GNSS_TO_PLC_SIZE, 158)
        self.assertEqual(PLC_TO_GNSS_SIZE, 68)
        self.assertEqual(FRAME_SIZE, GNSS_TO_PLC_SIZE)

    def test_fix_status_pair(self):
        self.assertEqual(encode_fix_status_pair(4, 4), 44)
        self.assertEqual(encode_fix_status_pair(1, 0), 10)

    def test_encode_gnss_frame_length(self):
        ctx = PlcProtocolContext(vehicle_id="RTG-001")
        result = PositionResult(
            yard_id="25A",
            in_yard=True,
            current_bay_no=9,
            yard_origin_lat=30.0,
            yard_origin_lon=120.0,
            yard_origin_alt=10.0,
            bay_spacing_m=6.0,
            gnss_fix_quality=4,
            fix_quality_left=4,
            fix_quality_left_ant2=4,
            fix_quality_right=4,
            fix_quality_right_ant2=5,
            fix_quality_trolley=1,
            fix_quality_trolley_ant2=1,
        )
        result.gantry = GantryResult(
            center_elec_side=120.5,
            center_engine_side=121.0,
            leg_offsets=[0.01, 0.02, 0.03, 0.04],
            speed=1.5,
            lpoint_x=120.75,
        )
        result.trolley = TrolleyResult(
            travel_distance=12.5,
            center_y=12.5,
            speed=0.8,
            is_valid=True,
        )

        result.gantry.heading = 90.0
        result.gantry.tire_elec1 = (1.25, 2.0, -1.5)
        result.gantry.tire_elec1_valid = True
        result.gantry.lane_mm = [-120, 80, -30, 40]
        result.gantry.a1_valid = True
        frame = encode_gnss_to_plc(result, ctx)
        self.assertEqual(len(frame), GNSS_TO_PLC_SIZE)
        lane = struct.unpack_from("<hhhh", frame, 8 + struct.calcsize("<HHHH8sHH"))
        self.assertEqual(lane, (-120, 80, -30, 40))
        tire_off = 8 + struct.calcsize("<HHHH8sHHhhhhIIHhhhHH")
        x_mm, y_mm, z_mm = struct.unpack_from("<IIH", frame, tire_off)
        self.assertEqual(struct.unpack("<i", struct.pack("<I", x_mm))[0], 1250)
        self.assertEqual(struct.unpack("<i", struct.pack("<I", y_mm))[0], 2000)
        self.assertEqual(z_mm, 1500)
        reserved0 = struct.unpack_from("<H", frame, GNSS_TO_PLC_SIZE - 2 - 40)[0]
        self.assertEqual(reserved0, 9000)
        magic = struct.unpack_from("<H", frame, 0)[0]
        self.assertEqual(magic, FRAME_MAGIC)
        footer = struct.unpack_from("<H", frame, GNSS_TO_PLC_SIZE - 2)[0]
        self.assertEqual(footer, FRAME_FOOTER)

    def test_encode_frame_compat(self):
        result = PositionResult(gnss_fix_quality=1)
        ctx = PlcProtocolContext()
        frame = encode_frame(result, ctx)
        self.assertEqual(len(frame), GNSS_TO_PLC_SIZE)

    def test_decode_plc_downlink(self):
        raw = _build_plc_downlink()
        self.assertEqual(len(raw), PLC_TO_GNSS_SIZE)
        parsed = decode_plc_to_gnss(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.heartbeat, 7)
        self.assertEqual(parsed.peer_heartbeat, 3)
        self.assertEqual(parsed.message_seq, 99)
        self.assertEqual(parsed.yard_code, "YARD01")
        self.assertEqual(parsed.rtg_id, "RTG001")
        self.assertEqual(parsed.gantry_pos_mm, 1000)
        self.assertEqual(parsed.trolley_pos_mm, 2000)

    def test_decode_frame_wrapper(self):
        raw = _build_plc_downlink()
        parsed = decode_frame(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.gantry_run_status, 1)

    def test_antenna_xy_uses_yard_axes(self):
        from src.solver.coordinate_transform import YardTransform, wgs84_to_ecef

        origin = (32.200977111, 119.054199750, 9.655)
        heading = 228.335
        y_east = math.sin(math.radians(heading))
        y_north = math.cos(math.radians(heading))
        tf = YardTransform(*origin)
        tf.set_y_axis(y_east, y_north)
        lat, lon, alt = tf.lycs_to_wgs84(0.0, -2.5, 0.0)
        ex, ey, ez = wgs84_to_ecef(lat, lon, alt)
        result = PositionResult(
            yard_origin_lat=origin[0],
            yard_origin_lon=origin[1],
            yard_origin_alt=origin[2],
            yard_axis_east=y_east,
            yard_axis_north=y_north,
            gantry_antennas={
                "a1": {"ecef_x": ex, "ecef_y": ey, "ecef_z": ez, "valid": True},
            },
        )
        frame = encode_gnss_to_plc(result, PlcProtocolContext())
        off = 8 + struct.calcsize("<HHHH8sHHhhhhIIHhhhHH")
        x_u, y_u, z_mm = struct.unpack_from("<IIH", frame, off)
        x_mm = struct.unpack("<i", struct.pack("<I", x_u))[0]
        y_mm = struct.unpack("<i", struct.pack("<I", y_u))[0]
        self.assertAlmostEqual(x_mm, 0, delta=30)
        self.assertAlmostEqual(y_mm, -2500, delta=30)
        self.assertEqual(z_mm, 0)

    def test_lane_deviation_sign_is_sent(self):
        result = PositionResult()
        result.gantry.lane_mm = [-120, 80, -30, 40]
        frame = encode_gnss_to_plc(result, PlcProtocolContext())
        lane = struct.unpack_from("<hhhh", frame, 8 + struct.calcsize("<HHHH8sHH"))
        self.assertEqual(lane, (-120, 80, -30, 40))

    def test_base_station_uses_yard_origin(self):
        ctx = PlcProtocolContext(base_station_mode="yard_origin")
        ctx2 = PlcProtocolContext(
            base_station_mode="fixed",
            base_lat=31.0,
            base_lon=121.0,
            base_alt=5.0,
        )
        result = PositionResult(
            yard_origin_lat=30.123456,
            yard_origin_lon=120.654321,
            yard_origin_alt=10.0,
        )
        from src.bridge.plc_protocol_v1 import _resolve_base_station

        lat, lon, alt = _resolve_base_station(result, ctx)
        self.assertAlmostEqual(lat, 30.123456)
        self.assertAlmostEqual(lon, 120.654321)
        self.assertAlmostEqual(alt, 10.0)

        lat2, lon2, alt2 = _resolve_base_station(result, ctx2)
        self.assertAlmostEqual(lat2, 31.0)
        self.assertAlmostEqual(lon2, 121.0)
        self.assertAlmostEqual(alt2, 5.0)

    def test_stream_reassembly(self):
        buf = bytearray(b"\x00\x01\x02" + _build_plc_downlink() + _build_plc_downlink(seq=100))
        f1, buf = try_extract_plc_frame(buf)
        f2, buf = try_extract_plc_frame(buf)
        self.assertIsNotNone(f1)
        self.assertIsNotNone(f2)
        self.assertEqual(f2.message_seq, 100)


if __name__ == "__main__":
    unittest.main()
