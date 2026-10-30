"""Project-owned local service lifecycle; never kills a PID or unknown listener."""
import builtins
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class RuntimeError(builtins.RuntimeError):
    pass


def data_directory(project_root):
    return Path(os.environ.get('CODEWARDEN_DATA', Path(project_root) / '.warcraft')).resolve()


def _regular(path):
    if path.is_symlink() or (path.exists() and (not path.is_file() or getattr(path.stat(), 'st_file_attributes', 0) & 0x400)):
        raise RuntimeError(f'管理文件不可为链接或特殊文件：{path.name}')


class RuntimeLease:
    """One managed server per project data directory, held for its lifetime."""
    def __init__(self, data_root, *, lock_name='runtime.lock'):
        self.data_root = Path(data_root).resolve()
        if lock_name not in {'runtime.lock', 'restart.lock'}:
            raise RuntimeError('未知的服务管理锁。')
        self.lock_name = lock_name
        self.file = None

    def __enter__(self):
        self.data_root.mkdir(parents=True, exist_ok=True)
        path = self.data_root / self.lock_name
        _regular(path)
        self.file = path.open('a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'\0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close(); self.file = None
            message = '另一项停止或重启正在执行，请等待当前操作结束。' if self.lock_name == 'restart.lock' else '已有本项目受管理服务正在运行或启动，请勿重复启动。'
            raise RuntimeError(message) from exc
        return self

    def __exit__(self, *_):
        if self.file:
            try:
                self.file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close(); self.file = None


def _lease_free(data_root):
    try:
        with RuntimeLease(data_root):
            return True
    except RuntimeError:
        return False


def load_runtime(project_root, data_root):
    project_root, data_root = Path(project_root).resolve(), Path(data_root).resolve()
    path = data_root / 'runtime.json'
    _regular(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 16384:
            raise ValueError('size')
        record = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('schema') != 1:
            raise ValueError('schema')
        if record.get('project_root') != str(project_root) or record.get('data_root') != str(data_root):
            raise ValueError('ownership')
        if type(record.get('pid')) is not int or record['pid'] <= 0 or type(record.get('port')) is not int or not 1 <= record['port'] <= 65535:
            raise ValueError('identity')
        if not isinstance(record.get('instance_id'), str) or len(record['instance_id']) < 24 or not isinstance(record.get('token'), str) or len(record['token']) < 32:
            raise ValueError('nonce')
        return record
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError('服务管理记录无效或不属于本项目，未停止任何程序。请先人工核对 runtime.json。') from exc


def _last_port(project_root, data_root):
    path = Path(data_root) / 'runtime.last.json'; _regular(path)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 16384:
            raise ValueError('size')
        record = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(record, dict) or record.get('schema') != 1
                or record.get('project_root') != str(Path(project_root).resolve())
                or record.get('data_root') != str(Path(data_root).resolve())
                or type(record.get('port')) is not int or not 1 <= record['port'] <= 65535):
            raise ValueError('ownership')
        return record['port']
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError('上次端口记录无效，未停止任何程序。请核对 runtime.last.json。') from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(port, path, *, token='', method='GET', timeout=1):
    # Neither environment proxy settings nor redirects may move the management
    # token away from the one literal loopback endpoint.
    headers = {'X-Codewarden-Runtime-Token': token, 'Content-Type': 'application/json'}
    request = Request(f'http://127.0.0.1:{port}{path}', data=b'{}' if method == 'POST' else None, headers=headers, method=method)
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
        content = response.read(65537)
        if len(content) > 65536:
            raise ValueError('response too large')
        return response.status, json.loads(content)


def probe_runtime(record):
    try:
        _, info = _request(record['port'], '/api/runtime', token=record['token'])
        return isinstance(info, dict) and all(info.get(key) == record[key] for key in ('project_root', 'data_root', 'pid', 'port', 'instance_id'))
    except (OSError, URLError, ValueError, KeyError):
        return False


def _port_open(port):
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def _write_record(data_root, record):
    history = {key: record[key] for key in ('schema', 'project_root', 'data_root', 'port')}
    for name, value in [('runtime.last.json', history), ('runtime.json', record)]:
        path = data_root / name; _regular(path)
        temporary = data_root / (name + '.' + record['instance_id'] + '.tmp')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                json.dump(value, file, ensure_ascii=False); file.flush(); os.fsync(file.fileno())
            os.replace(temporary, path)
            try: path.chmod(0o600)
            except OSError: pass
        finally:
            temporary.unlink(missing_ok=True)


def _listener(port):
    candidates = [port] if port is not None else range(8765, 8786)
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise RuntimeError('端口必须为1–65535的整数。')
    for number in candidates:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', number)); listener.listen(128)
            return listener
        except OSError:
            listener.close()
    raise RuntimeError('目标端口被占用，未停止占用它的程序。请使用 --port 指定其他空闲本机端口。')


def serve_managed(app=None, *, app_factory=None, project_root, data_root, port=None):
    import uvicorn
    project_root, data_root = Path(project_root).resolve(), Path(data_root).resolve()
    with RuntimeLease(data_root):
        existing = load_runtime(project_root, data_root)
        if existing and _port_open(existing['port']):
            raise RuntimeError('旧管理记录对应端口仍有程序响应，未覆盖记录。请先核对服务状态。')
        if port is None:
            port = existing['port'] if existing else _last_port(project_root, data_root)
        listener = _listener(port)
        record = None
        try:
            # The main application factory runs only AFTER acquiring ownership,
            # so a duplicate start cannot normalize another live server's drafts.
            application = app_factory() if app_factory else app
            if application is None:
                raise RuntimeError('未提供本地服务应用。')
            actual_port = listener.getsockname()[1]
            record = {'schema': 1, 'project_root': str(project_root), 'data_root': str(data_root),
                'pid': os.getpid(), 'port': actual_port, 'instance_id': secrets.token_hex(24),
                'token': secrets.token_urlsafe(36), 'started_at': datetime.now(timezone.utc).isoformat()}
            server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=actual_port,
                log_level='info', access_log=False))
            application.state.runtime_info = {key: value for key, value in record.items() if key != 'token'}
            application.state.runtime_token = record['token']
            application.state.shutdown_callback = lambda: setattr(server, 'should_exit', True)
            _write_record(data_root, record)
            print(f'本地服务初始化后访问：http://127.0.0.1:{actual_port}\n按 Ctrl+C 停止。', flush=True)
            server.run(sockets=[listener])
            if not server.started:
                raise RuntimeError('本地服务初始化失败，请检查 server.log。')
        finally:
            listener.close()
            if record:
                try:
                    current = load_runtime(project_root, data_root)
                    if current and current['instance_id'] == record['instance_id']:
                        (data_root / 'runtime.json').unlink(missing_ok=True)
                except RuntimeError:
                    pass


def launch_background(command, project_root, data_root):
    data_root = Path(data_root).resolve(); data_root.mkdir(parents=True, exist_ok=True)
    log = data_root / 'server.log'; _regular(log)
    environment = os.environ.copy()
    environment.update(CODEWARDEN_DATA=str(data_root), PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    with log.open('ab', buffering=0) as output:
        return subprocess.Popen(command, cwd=project_root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            shell=False, close_fds=True, env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            start_new_session=os.name != 'nt')


def wait_ready(project_root, data_root, port=None, *, timeout=20, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = load_runtime(project_root, data_root)
        if record and (port is None or record['port'] == port) and probe_runtime(record):
            try:
                status, health = _request(record['port'], '/api/health')
                if status == 200 and isinstance(health, dict) and health.get('status') == 'ok':
                    return record
            except (OSError, URLError, ValueError):
                pass
        if process is not None and process.poll() is not None:
            raise RuntimeError('服务进程已退出，未确认启动成功。请查看数据目录的 server.log。')
        time.sleep(.1)
    raise RuntimeError('尚未确认服务启动成功，请查看数据目录的 server.log 后再检查。未停止其他程序。')


def restart_managed(project_root, data_root, *, port=None, timeout=20):
    # A separate short-lived lock serializes the whole shutdown/spawn/health
    # sequence without conflicting with the child's lifetime runtime lease.
    with RuntimeLease(data_root, lock_name='restart.lock'):
        return _restart_managed(project_root, data_root, port=port, timeout=timeout)


def stop_managed(project_root, data_root, *, timeout=20):
    """Return True after verified shutdown, False if no managed instance is live."""
    project_root, data_root = Path(project_root).resolve(), Path(data_root).resolve()
    with RuntimeLease(data_root, lock_name='restart.lock'):
        return _stop_instance(data_root, load_runtime(project_root, data_root), timeout=timeout)


def _stop_instance(data_root, record, *, timeout):
    if record and probe_runtime(record):
        try:
            status, response = _request(record['port'], '/api/runtime/shutdown', token=record['token'], method='POST', timeout=2)
        except HTTPError as exc:
            if exc.code == 409:
                raise RuntimeError('当前有运行、生成或其他任务，请先等待任务结束再停止或重启。') from exc
            raise RuntimeError('受管理服务拒绝关闭，未强制停止任何进程。') from exc
        except (OSError, URLError, ValueError) as exc:
            raise RuntimeError('关闭请求未获确认，请稍后核对状态；未强制停止任何进程。') from exc
        if status != 202 or not isinstance(response, dict) or response.get('stopping') is not True or response.get('instance_id') != record['instance_id']:
            raise RuntimeError('服务未返回有效关闭确认，未强制停止任何进程。')
        deadline = time.monotonic() + timeout
        while not _lease_free(data_root) or _port_open(record['port']):
            if time.monotonic() >= deadline:
                raise RuntimeError('服务尚未完成关闭，请稍后重试。未强制结束进程。')
            time.sleep(.1)
        return True
    elif not _lease_free(data_root):
        raise RuntimeError('本项目服务正在启动或管理接口不可用，未停止任何程序。请稍后重试。')
    elif record and _port_open(record['port']):
        raise RuntimeError('记录中的端口有未知程序或无法认证的服务，未停止任何程序。请先人工核对。')
    return False


def _restart_managed(project_root, data_root, *, port=None, timeout=20):
    project_root, data_root = Path(project_root).resolve(), Path(data_root).resolve()
    record = load_runtime(project_root, data_root)
    target = port if port is not None else (record['port'] if record else _last_port(project_root, data_root))
    if target is not None and (type(target) is not int or not 1 <= target <= 65535):
        raise RuntimeError('端口必须为1–65535的整数。')
    _stop_instance(data_root, record, timeout=timeout)
    if target is not None and _port_open(target):
        raise RuntimeError('目标端口被未知程序占用，未停止它；请指定其他空闲端口。')
    command = [sys.executable, str(project_root / 'start.py')]
    if target is not None:
        command.extend(['--port', str(target)])
    process = launch_background(command, project_root, data_root)
    return wait_ready(project_root, data_root, target, timeout=timeout, process=process)
