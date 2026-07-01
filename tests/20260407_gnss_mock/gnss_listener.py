"""
GNSS UDP 数据监听器
监听本机指定端口，打印收到的 NMEA 数据
"""

import socket
import argparse

DEFAULT_PORT = 9001


def get_local_ip() -> str:
    """获取本机局域网 IP"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description="GNSS UDP 数据监听器")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"监听端口 (默认: {DEFAULT_PORT})")
    parser.add_argument("--host", default="",
                        help="监听地址 (默认: 自动获取本机 IP)")
    args = parser.parse_args()

    host = args.host or get_local_ip()
    port = args.port

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((host, port))

    print(f"监听 {host}:{port} ...")
    print("按 Ctrl+C 停止")
    print("-" * 60)

    try:
        while True:
            data, addr = sock.recvfrom(65535)
            text = data.decode('utf-8', errors='replace').rstrip('\n\r')
            print(text)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
