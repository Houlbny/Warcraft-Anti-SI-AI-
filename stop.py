"""Stop this project's authenticated local instance without starting another."""
import argparse
from pathlib import Path

from backend.runtime import RuntimeError, data_directory, stop_managed


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='停止本项目受管理的本地服务')
    parser.add_argument('--timeout', type=float, default=30, help='等待关闭确认的秒数，默认30')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 120:
        parser.exit(1, '等待时间必须为1–120秒。\n')
    try:
        stopped = stop_managed(root, data_directory(root), timeout=args.timeout)
    except RuntimeError as exc:
        parser.exit(1, f'{exc}\n')
    print('服务已停止，存档和已保存的密钥保留。' if stopped else '未发现运行中的受管理实例，无需停止。')


if __name__ == '__main__':
    main()
