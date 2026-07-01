"""
GNSS 接收机模拟器
从 NMEA 日志文件读取数据，通过 UDP 端口推送到中控机。
模拟双天线接收机：日志内 GGA（定位）+ PASHR/GPTRA 等（航向）合并推送。
默认端口应对齐 config/system.yaml：9001 gantry_a1 / 9002 gantry_a2 / 9003 trolley。
"""

import logging
import socket
import time
import sys
import signal
from pathlib import Path
from typing import List

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.enhanced_logger import EnhancedLogger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = EnhancedLogger.get_logger("gnss_simulator")


def get_local_ip() -> str:
    """获取本机局域网 IP 地址"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 不实际发送数据，仅通过路由表确定本地接口 IP
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    finally:
        s.close()


class GnssSimulator:
    """
    GNSS 接收机模拟器
    从本地 NMEA 日志文件读取，通过 UDP 推送到指定地址和端口
    """
    
    def __init__(
        self,
        nmea_file: str,
        target_host: str = "",
        target_port: int = 9001,
        receiver_id: str = "sim_receiver_1",
        rate: int = 10,  # 每秒行数
    ):
        """
        初始化模拟器
        
        Args:
            nmea_file: NMEA 日志文件路径
            target_host: 目标中控机地址
            target_port: 目标 UDP 端口
            receiver_id: 接收机标识符
            rate: 每秒发送行数（默认 10 行/秒 = 100ms/行）
        """
        self.nmea_file = nmea_file
        self.target_host = target_host or get_local_ip()
        self.target_port = target_port
        self.receiver_id = receiver_id
        self.rate = rate
        self.interval = 1.0 / rate  # 每行的间隔时间（秒）
        
        # 创建 UDP socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # 加载 NMEA 数据
        self.nmea_lines: List[str] = []
        self.load_nmea_file()
        
        self._running = False
        self._paused = False
        
        # 统计信息
        self.total_sent = 0
        self.errors = 0
    
    def load_nmea_file(self) -> None:
        """从文件加载 NMEA 语句"""
        if not Path(self.nmea_file).exists():
            raise FileNotFoundError(f"NMEA 文件不存在: {self.nmea_file}")
        
        with open(self.nmea_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 去除空行和注释
        self.nmea_lines = [
            line.strip() 
            for line in lines 
            if line.strip() and not line.startswith('#')
        ]
        
        logger.info(f"✓ 加载 NMEA 文件: {self.nmea_file}")
        logger.info(f"  总行数: {len(self.nmea_lines)}")
    
    def send_nmea(self, nmea_sentence: str) -> bool:
        """
        发送单条 NMEA 语句
        
        Args:
            nmea_sentence: NMEA 语句
        
        Returns:
            True 如果发送成功，否则 False
        """
        try:
            # NMEA 语句必须以换行符结尾
            if not nmea_sentence.endswith('\n'):
                nmea_sentence += '\n'
            
            # 发送到目标
            data = nmea_sentence.encode('utf-8')
            self.socket.sendto(data, (self.target_host, self.target_port))
            
            self.total_sent += 1
            return True
        except Exception as e:
            logger.error(f"✗ 发送失败: {e}")
            self.errors += 1
            return False
    
    def start(self) -> None:
        """启动模拟器，开始推送 NMEA 数据"""
        self._running = True
        logger.info("=" * 60)
        logger.info(f"GNSS 模拟器启动")
        logger.info(f"  接收机 ID: {self.receiver_id}")
        logger.info(f"  目标地址: {self.target_host}:{self.target_port}")
        logger.info(f"  发送速率: {self.rate} 行/秒 (间隔 {self.interval*1000:.0f}ms)")
        logger.info(f"  NMEA 语句数: {len(self.nmea_lines)}")
        logger.info(f"  预计循环时间: {len(self.nmea_lines) * self.interval / 60:.1f} 分钟")
        logger.info("  按 Ctrl+C 停止模拟器")
        logger.info("=" * 60)
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        try:
            self._run_loop()
        finally:
            self.stop()
    
    def _run_loop(self) -> None:
        """主循环，循环发送 NMEA 数据"""
        line_index = 0
        last_stats_time = time.time()
        
        while self._running:
            # 获取当前行
            nmea_line = self.nmea_lines[line_index]
            
            # 发送数据
            if self.send_nmea(nmea_line):
                # 从日志输出频率降低（每 100 行输出一次）
                if self.total_sent % 100 == 0:
                    logger.debug(f"已发送 {self.total_sent} 行数据")
            
            # 移动到下一行
            line_index += 1
            if line_index >= len(self.nmea_lines):
                line_index = 0
                logger.info(f"↻ NMEA 数据循环播放（已发送 {self.total_sent} 行）")
            
            # 定期输出统计信息
            now = time.time()
            if now - last_stats_time >= 10:  # 每 10 秒统计一次
                elapsed = now - last_stats_time
                rate = self.total_sent / elapsed if elapsed > 0 else 0
                logger.info(f"实时速率: {rate:.1f} 行/秒 | 总已发送: {self.total_sent} | 错误: {self.errors}")
                last_stats_time = now
            
            # 延迟
            time.sleep(self.interval)
    
    def stop(self) -> None:
        """停止模拟器"""
        self._running = False
        try:
            self.socket.close()
        except:
            pass
        
        logger.info("=" * 60)
        logger.info("GNSS 模拟器已停止")
        logger.info(f"  总发送行数: {self.total_sent}")
        logger.info(f"  错误次数: {self.errors}")
        logger.info(f"  成功率: {(1 - self.errors/max(self.total_sent, 1))*100:.2f}%")
        logger.info("=" * 60)
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"收到系统信号 {signum}，准备退出...")
        self._running = False


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="GNSS 接收机模拟器 - 从 NMEA 日志文件读取并通过 UDP 发送"
    )
    parser.add_argument(
        "nmea_file",
        help="NMEA 日志文件路径"
    )
    parser.add_argument(
        "--host",
        default="",
        help="目标中控机地址 (默认: 自动获取本机局域网 IP)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9001,
        help="目标 UDP 端口 (默认: 9001)"
    )
    parser.add_argument(
        "--rate",
        type=int,
        default=10,
        help="发送速率，行/秒 (默认: 10)"
    )
    parser.add_argument(
        "--receiver-id",
        default="sim_receiver",
        help="接收机标识符 (默认: sim_receiver)"
    )
    
    args = parser.parse_args()
    
    # 创建模拟器
    simulator = GnssSimulator(
        nmea_file=args.nmea_file,
        target_host=args.host,
        target_port=args.port,
        receiver_id=args.receiver_id,
        rate=args.rate,
    )
    
    # 启动
    simulator.start()


if __name__ == "__main__":
    main()

