"""Restart only an authenticated managed instance; never kills by port/PID."""
import argparse
from pathlib import Path

from backend.runtime import RuntimeError, data_directory, restart_managed


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='重启本项目受管理的本地服务')
    parser.add_argument('--port', type=int, help='显式选择新端口；默认沿用上次端口')
    parser.add_argument('--timeout', type=float, default=30, help='等待关闭/启动确认的秒数，默认30')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.exit(1, '等待时间必须为1–120秒。\n')
    if not (root / 'frontend/dist/index.html').is_file():
        parser.exit(1, '请先运行安装脚本并构建前端。\n')
    try:
        record = restart_managed(root, data_directory(root), port=args.port, timeout=args.timeout)
    except RuntimeError as exc:
        parser.exit(1, f'{exc}\n')
    print(f"服务已就绪：http://127.0.0.1:{record['port']}\n后台日志：{data_directory(root) / 'server.log'}")


if __name__ == '__main__':
    main()
