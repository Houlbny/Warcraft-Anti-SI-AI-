"""Start one foreground, project-managed local service; no global installs."""
import argparse
import importlib
from pathlib import Path

from backend.runtime import RuntimeError, data_directory, serve_managed


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description='启动修复者营地本地服务')
    parser.add_argument('--port', type=int, help='指定本机端口；默认沿用管理记录，首次从8765查找')
    args = parser.parse_args()
    if not (root / 'frontend/dist/index.html').is_file():
        parser.exit(1, '请先运行安装脚本，或在 frontend 执行 npm install 和 npm run build。\n')
    try:
        serve_managed(app_factory=lambda: importlib.import_module('backend.api').app,
            project_root=root, data_root=data_directory(root), port=args.port)
    except (RuntimeError, ImportError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
