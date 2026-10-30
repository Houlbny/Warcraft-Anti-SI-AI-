"""OS-backed credentials with authoritative, secret-free per-account references.

Windows uses the native system keyring first, then current-user DPAPI if that
keyring cannot persist a value. A committed DPAPI reference pins that account to
DPAPI. References/tombstones prevent old credentials in another backend reviving.
Only enabled=False uses instance-local memory, for explicitly isolated tests.
"""
import ctypes
import hashlib
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path


class CredentialError(RuntimeError):
    """Safe actionable error: never include credential contents or backend errors."""


_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_MAGIC = b'CODEWARDEN-DPAPI\x01'


def _keyring_module():
    from types import SimpleNamespace
    from keyring import errors
    # Choose the native OS backend explicitly: never follow a configured plaintext
    # backend or a chainer that might silently fall through to one.
    if sys.platform == 'win32':
        from keyring.backends.Windows import WinVaultKeyring as NativeKeyring
    elif sys.platform == 'darwin':
        from keyring.backends.macOS import Keyring as NativeKeyring
    else:
        raise CredentialError('未发现受支持的系统凭据管理器，不会使用明文凭据后端。')
    native = NativeKeyring()
    return SimpleNamespace(errors=errors, get_password=native.get_password,
                           set_password=native.set_password, delete_password=native.delete_password)


def _crypt(data, entropy, decrypt=False):
    if sys.platform != 'win32':
        raise CredentialError('DPAPI 凭据只能由保存它的 Windows 用户读取，请重新配置密钥。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
    incoming_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    incoming = Blob(len(data), ctypes.cast(incoming_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    optional_entropy = Blob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        operation = crypt32.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    else:
        operation = crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = 'Codewarden API credential'
    operation.restype = wintypes.BOOL
    try:
        # CRYPTPROTECT_UI_FORBIDDEN=1; no LOCAL_MACHINE flag: current-user scope only.
        if not operation(ctypes.byref(incoming), description, ctypes.byref(optional_entropy), None, None, 1, ctypes.byref(outgoing)):
            raise CredentialError('Windows 无法解密此密钥；请用保存时的 Windows 用户重新输入密钥。' if decrypt else 'Windows 无法加密保存密钥，请检查当前用户会话和目录权限。')
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        ctypes.memset(incoming_buffer, 0, len(incoming_buffer))
        ctypes.memset(entropy_buffer, 0, len(entropy_buffer))
        if outgoing.pbData:
            ctypes.memset(outgoing.pbData, 0, outgoing.cbData)
            kernel32.LocalFree(ctypes.cast(outgoing.pbData, ctypes.c_void_p))


def _protect(data, entropy):
    return _crypt(data, entropy)


def _unprotect(data, entropy):
    return _crypt(data, entropy, decrypt=True)


def _is_link(path):
    return path.is_symlink() or (path.exists() and bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400))


class CredentialStore:
    def __init__(self, root, enabled=True):
        self.root = Path(root).resolve()
        self.directory = self.root / 'credentials'
        self.enabled = bool(enabled)
        self._session = {}
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(str(self.directory), threading.RLock())

    @staticmethod
    def _validate(account):
        if not isinstance(account, str) or not re.fullmatch(r'[a-f0-9]{64}', account):
            raise CredentialError('凭据账户标识无效，未访问任何凭据。')

    def _file(self, name):
        if _is_link(self.directory):
            raise CredentialError('凭据目录不能是链接，请检查本地数据目录后重试。')
        path = self.directory / name
        if _is_link(path):
            raise CredentialError('凭据文件不能是链接，请检查本地数据目录后重试。')
        return path

    def _metadata(self, account):
        path = self._file(account + '.json')
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 16384:
                raise ValueError()
            meta = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(meta, dict) or meta.get('version') != 1 or meta.get('state') not in {'active', 'deleted'} or meta.get('storage') not in {'system-keyring', 'windows-dpapi'}:
                raise ValueError()
            if meta['state'] == 'active' and meta['storage'] == 'windows-dpapi' and not re.fullmatch(re.escape(account) + r'\.[a-f0-9]{32}\.bin', meta.get('blob', '')):
                raise ValueError()
            if meta['storage'] == 'system-keyring':
                service = meta.get('service')
                if (meta['state'] == 'active' or service is not None) and (not isinstance(service, str) or not re.fullmatch(re.escape('codewarden:' + account) + r'(?::[a-f0-9]{32})?', service)):
                    raise ValueError()
            return meta
        except (OSError, ValueError, TypeError, UnicodeError):
            raise CredentialError('凭据记录已损坏或无法读取，请在模型设置中重新输入密钥替换。') from None

    def _atomic(self, name, data):
        target = self._file(name)
        temporary = None
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Recheck after creation, and never follow a caller-controlled leaf path.
            target = self._file(name)
            temporary = self.directory / ('.' + uuid.uuid4().hex + '.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        except OSError:
            raise CredentialError('无法持久保存凭据，请检查本地数据目录权限；未改用临时或明文存储。') from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _commit(self, account, metadata):
        self._atomic(account + '.json', json.dumps({'version': 1, **metadata}, ensure_ascii=True, sort_keys=True).encode('utf-8'))

    @staticmethod
    def _entropy(account):
        return hashlib.sha256(('codewarden-credential-v1:' + account).encode()).digest()

    def read(self, account):
        self._validate(account)
        if not self.enabled:
            return self._session.get(account), 'session'
        with self._lock:
            meta = self._metadata(account)
            if meta:
                storage = meta['storage']
                if meta['state'] == 'deleted':
                    if meta.get('cleanup_pending'):
                        raise CredentialError('密钥已停用，但旧凭据清理尚未完成，请再次移除或重新输入密钥替换。')
                    return None, storage
                if storage == 'windows-dpapi':
                    try:
                        data = self._file(meta['blob']).read_bytes()
                        if len(data) > 65536 or not data.startswith(_MAGIC):
                            raise ValueError()
                        value = _unprotect(data[len(_MAGIC):], self._entropy(account)).decode('utf-8')
                        if not value:
                            raise ValueError()
                        return value, storage
                    except (OSError, ValueError, UnicodeError):
                        raise CredentialError('加密凭据已损坏或无法读取，请在模型设置中重新输入密钥替换。') from None
                try:
                    value = _keyring_module().get_password(meta['service'], account)
                except Exception:
                    raise CredentialError('系统凭据暂时无法读取，请检查当前用户会话，或重新输入密钥保存。') from None
                if not isinstance(value, str) or not value:
                    raise CredentialError('系统凭据记录缺失，请在模型设置中重新输入密钥。')
                return value, storage
            # Migration only probes these exact known accounts, never enumerates a vault.
            try:
                keyring = _keyring_module()
                for service in ('codewarden:' + account, 'codewarden'):
                    value = keyring.get_password(service, account)
                    if value:
                        if not isinstance(value, str):
                            raise ValueError()
                        storage = self.write(account, value)
                        return value, storage
                return None, 'system-keyring'
            except CredentialError:
                raise
            except Exception:
                raise CredentialError('系统凭据暂时无法读取。可重新输入密钥，由当前用户的安全存储持久保存。') from None

    def write(self, account, value):
        self._validate(account)
        if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 16384:
            raise CredentialError('请输入有效密钥；移除密钥请使用删除操作。')
        if not self.enabled:
            self._session[account] = value
            return 'session'
        with self._lock:
            try:
                previous = self._metadata(account)
            except CredentialError:
                previous = None  # An explicit replacement may repair corrupt references.
            dpapi_only = previous and previous['storage'] == 'windows-dpapi'
            if not dpapi_only:
                try:
                    keyring = _keyring_module()
                    service = 'codewarden:' + account + ':' + uuid.uuid4().hex
                    keyring.set_password(service, account, value)
                    if keyring.get_password(service, account) != value:
                        raise ValueError()
                except Exception:
                    if sys.platform != 'win32':
                        raise CredentialError('系统凭据管理器无法持久保存密钥，请检查系统 Keychain 权限；未保存到临时或明文文件。') from None
                else:
                    try:
                        self._commit(account, {'state': 'active', 'storage': 'system-keyring', 'service': service})
                    except CredentialError:
                        try:
                            keyring.delete_password(service, account)
                        except Exception:
                            pass  # Uncommitted versions are never discovered without authority.
                        raise
                    # Old versions are no longer authoritative. Best-effort retirement
                    # cannot turn a committed new credential into a reported save failure.
                    if previous and previous.get('service') and previous['service'] != service:
                        try:
                            keyring.delete_password(previous['service'], account)
                        except Exception:
                            pass
                    return 'system-keyring'
            if sys.platform != 'win32':
                raise CredentialError('此凭据由 Windows DPAPI 保存，请使用原 Windows 用户或重新配置本机系统凭据。')
            protected = _protect(value.encode('utf-8'), self._entropy(account))
            blob = account + '.' + uuid.uuid4().hex + '.bin'
            self._atomic(blob, _MAGIC + protected)
            try:
                self._commit(account, {'state': 'active', 'storage': 'windows-dpapi', 'backend_policy': 'dpapi-only', 'blob': blob})
            except CredentialError:
                try:
                    self._file(blob).unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            return 'windows-dpapi'

    def clear(self, account):
        self._validate(account)
        if not self.enabled:
            self._session.pop(account, None)
            return
        with self._lock:
            try:
                previous = self._metadata(account)
            except CredentialError:
                previous = None
            storage = previous['storage'] if previous else 'system-keyring'
            tombstone = {'state': 'deleted', 'storage': storage, 'cleanup_pending': True}
            if storage == 'system-keyring':
                tombstone['service'] = previous.get('service', 'codewarden:' + account) if previous else 'codewarden:' + account
            # Publish revocation first: neither a crash nor an old backend can revive it.
            self._commit(account, tombstone)
            try:
                if storage == 'system-keyring':
                    known_active = previous and (previous['state'] == 'active' or previous.get('cleanup_pending'))
                    try:
                        keyring = _keyring_module()
                        for service in dict.fromkeys((tombstone['service'], 'codewarden:' + account, 'codewarden')):
                            try:
                                keyring.delete_password(service, account)
                            except keyring.errors.PasswordDeleteError:
                                if keyring.get_password(service, account) is not None:
                                    raise ValueError()
                    except Exception:
                        if known_active:
                            raise
                        # A new destination may be configured without a key even when
                        # this logon has no readable vault. The tombstone is authoritative.
                for path in self.directory.iterdir():
                    if re.fullmatch(re.escape(account) + r'(?:\.[a-f0-9]{32})?\.bin', path.name):
                        self._file(path.name).unlink(missing_ok=True)
            except Exception:
                raise CredentialError('密钥已停用，但旧凭据未能完全清理，请检查权限后再次移除；不会恢复使用旧密钥。') from None
            self._commit(account, {**tombstone, 'cleanup_pending': False})
