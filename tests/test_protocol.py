"""
单元测试：PLC 通信协议
测试帧编码/解码的正确性、CRC 校验等。
"""

import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.bridge.protocol import encode_frame, decode_frame, FRAME_MAGIC, FRAME_SIZE
from src.models.positioning import PositionResult, GantryResult, TrolleyResult, DataSource


class TestProtocol(unittest.TestCase):
    """PLC 协��编解码测试。"""

    def test_frame_size(self):
        """测试：帧大小是否符合预期（76 字节）。"""
        self.assertEqual(FRAME_SIZE, 76)

    def test_encode_decode_roundtrip(self):
        """测试：编码 → 解码往返。"""
        # 构造测试数据
        result = PositionResult(
            timestamp=1234567890.123,
            yard_id="yard_01",
            gnss_fix_quality=4,
            imu_coasting_active=False,
            imu_coasting_duration=0.0,
        )
        result.gantry = GantryResult(
            timestamp=result.timestamp,
            center_elec_side=120.5,
            center_engine_side=121.0,
            leg_offsets=[0.01, 0.02, 20.01, 20.02],
            speed=1.5,
            heading=87.5,
            pitch=0.1,
            roll=-0.2,
            lpoint_x=120.75,
            lpoint_y=10.0,
            lpoint_z=5.0,
            data_source=DataSource.GNSS_RTK_FIXED,
        )
        result.trolley = TrolleyResult(
            timestamp=result.timestamp,
            rel_x=1.2,
            rel_y=12.5,
            rel_z=0.0,
            center_z=3.0,
            travel_distance=12.5,
            speed=0.8,
            is_valid=True,
            data_source=DataSource.GNSS_RTK_FIXED,
        )

        seq = 42

        # 编码
        frame = encode_frame(result, seq)
        self.assertEqual(len(frame), FRAME_SIZE)

        # 解码
        decoded = decode_frame(frame)
        self.assertIsNotNone(decoded)

        # 验证关键字段
        self.assertEqual(decoded["magic"], FRAME_MAGIC)
        self.assertEqual(decoded["seq"], seq)
        self.assertAlmostEqual(decoded["timestamp"], result.timestamp, places=6)
        self.assertAlmostEqual(decoded["center_elec"], 120.5, places=3)
        self.assertAlmostEqual(decoded["trolley_x"], 1.2, places=3)
        self.assertAlmostEqual(decoded["travel_dist"], 12.5, places=3)
        self.assertEqual(decoded["fix_quality"], 4)

    def test_decode_corrupted_crc(self):
        """测试：损坏的 CRC 应导致解码失败。"""
        result = PositionResult(timestamp=0.0)
        frame = encode_frame(result, 0)

        # 故意修改一个字节（破坏 CRC）
        corrupted = bytearray(frame)
        corrupted[10] ^= 0xFF
        corrupted = bytes(corrupted)

        decoded = decode_frame(corrupted)
        self.assertIsNone(decoded)

    def test_status_bits(self):
        """测试：状态字 bit 位正确设置。"""
        result = PositionResult(
            timestamp=0.0,
            gnss_fix_quality=4,         # RTK 固���解
            imu_coasting_active=True,   # 惯导补位激活
        )
        frame = encode_frame(result, 0)
        decoded = decode_frame(frame)

        status = decoded["status"]
        # bit0=GNSS有效, bit1=RTK固定, bit2=惯导补位
        self.assertTrue(status & 0x01)   # GNSS 有效
        self.assertTrue(status & 0x02)   # RTK 固定
        self.assertTrue(status & 0x04)   # 惯导补位


if __name__ == "__main__":
    unittest.main()

