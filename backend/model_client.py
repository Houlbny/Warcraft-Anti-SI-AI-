"""OpenAI-compatible Chat Completions. User config only; never reads env keys."""
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from .credentials import CredentialError, CredentialStore
from .generation_validation import ContentValidationError

MAX_CHAPTER_REQUEST_BYTES = 32000

# Exact sensitive field names (with common service prefixes), not substrings
# such as password_length or token_count. Preserve quotes and JSON punctuation.
_SECRET_FIELD = (
    r'(?:[a-z][a-z0-9]*[_-])*'
    r'(?:api[_-]?key|password|passwd|secret(?:[_-]?key)?|'
    r'client[_-]?secret|(?:access|refresh|auth|session)[_-]?token|token|authorization)'
)
_ASSIGNMENT = re.compile(
    r'(?P<prefix>(?<![\w-])(?P<keyquote>["\']?)' + _SECRET_FIELD +
    r'(?P=keyquote)(?![\w-])\s*(?:(?::\s*(?:string|str)\s*)?=|:)\s*)'
    r'(?:(?P<quote>["\'])(?P<quoted>(?:\\.|(?!(?P=quote))[^\\\r\n])*)(?P=quote)'
    r'|(?P<bare>[^\s,;#"\'{}\[\]]+))', re.IGNORECASE,
)


def redact(text: str, secrets=()):
    """Best-effort deterministic masking of common credential literals.

    This is not a parser or a guarantee that arbitrary source contains no secret.
    Sensitive quoted/config assignments are masked; ordinary variable names,
    function calls and simple arithmetic expressions keep their learning context.
    Explicit caller-provided secret values are always removed wherever they occur.
    """
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')

    def assignment(match):
        quote = match.group('quote')
        if quote:
            if not match.group('quoted'):
                return match.group(0)
            return match.group('prefix') + quote + '[REDACTED]' + quote
        value = match.group('bare')
        if value.casefold() == 'bearer' and text[match.end():].lstrip().startswith('[REDACTED]'):
            return match.group(0)
        # Do not turn executable expressions into malformed source. Bare values
        # in .env/YAML/logs are credentials; calls and variable expansion are not
        # themselves literal credentials.
        tail = text[match.end():]
        if (any(char in value for char in '()[]') or value.startswith(('$', '<'))
                or value.casefold() in {'none', 'null', 'true', 'false', 'undefined'}
                or re.match(r'\s*(?:[+*/%?]|-(?!-)|\|\||&&)', tail)):
            return match.group(0)
        return match.group('prefix') + '[REDACTED]'

    # Authorization headers must be masked before their bare assignment is
    # processed, otherwise only the word Bearer would be replaced.
    text = re.sub(r'(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]+=*', r'\1[REDACTED]', text)
    text = _ASSIGNMENT.sub(assignment, text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}'
                  r'|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}'
                  r'|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[A-Za-z0-9_-]{30,})\b', '[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b', '[REDACTED]', text)
    text = re.sub(r'-----BEGIN (?P<kind>(?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY)-----'
                  r'.*?(?:-----END (?P=kind)-----|\Z)', '[REDACTED PRIVATE KEY]', text, flags=re.DOTALL)
    return text


def parse_json(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    try:
        result = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ContentValidationError('response', 'invalid_json', actual_type='str', actual_length=len(text)) from exc
    if not isinstance(result, (dict, list)):
        raise ContentValidationError('response', 'invalid_content', actual_type=type(result).__name__)
    return result


class ModelClient:
    def __init__(self, store, *, use_keyring=True, transport=None):
        self.store = store
        self.use_keyring = use_keyring
        self.transport = transport
        self.credentials = CredentialStore(store.root, enabled=use_keyring)
        self.storage = 'session'

    def _account(self, url):
        return hashlib.sha256((str(self.store.root) + url).encode()).hexdigest()

    def _key(self, url):
        value, self.storage = self.credentials.read(self._account(url))
        return value or ''

    def public(self):
        settings = self.store.get('settings', 'model', {'base_url': 'https://api.openai.com/v1', 'model': ''})
        settings.setdefault('generation_timeout', 300)
        try:
            has_key = bool(self._key(settings['base_url']))
        except CredentialError as exc:
            self.storage = 'unavailable'
            return {**settings, 'key_set': False, 'storage': self.storage, 'storage_error': str(exc)}
        return {**settings, 'key_set': has_key, 'storage': self.storage}

    def configure(self, base_url, model, api_key=None, *, generation_timeout=None):
        if generation_timeout is not None and (type(generation_timeout) is not int or not 30 <= generation_timeout <= 900):
            raise ValueError('章节响应等待上限必须是 30–900 秒的整数。')
        base_url = base_url.strip().rstrip('/')
        parsed = urlparse(base_url)
        local = parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or (parsed.scheme == 'http' and not local) or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('接口地址必须为 HTTPS，或本机 HTTP 地址；地址中不能包含凭据或查询参数。')
        if not model.strip():
            raise ValueError('请输入模型名称。')
        previous_settings = self.store.get('settings', 'model', {'base_url': 'https://api.openai.com/v1', 'model': '', 'generation_timeout': 300})
        previous_settings.setdefault('generation_timeout', 300)
        previous = previous_settings['base_url']
        if previous != base_url and api_key is None:
            # A change of destination must not implicitly send an existing key elsewhere.
            api_key = ''
        if api_key is not None:
            try:
                if api_key.strip():
                    self.storage = self.credentials.write(self._account(base_url), api_key.strip())
                else:
                    self.credentials.clear(self._account(base_url))
            except CredentialError as exc:
                raise ValueError(str(exc)) from exc
        self.store.put('settings', 'model', {'base_url': base_url, 'model': model.strip(), 'generation_timeout': generation_timeout if generation_timeout is not None else previous_settings['generation_timeout']})
        return self.public()

    def chat(self, system, user, *, purpose='default'):
        if purpose not in {'default', 'chapter', 'connectivity'}:
            raise ValueError('未知的模型请求用途。')
        settings = self.public()
        if settings.get('storage_error'):
            raise ValueError(settings['storage_error'])
        if not settings['model']:
            raise ValueError('请先在模型设置中配置接口地址和模型名称。')
        try:
            key = self._key(settings['base_url'])
        except CredentialError as exc:
            raise ValueError(str(exc)) from exc
        if not key and urlparse(settings['base_url']).hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('请先在模型设置中配置 API Key。')
        endpoint = settings['base_url']
        if not endpoint.endswith('/chat/completions'):
            endpoint += '/chat/completions'
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = f'Bearer {key}'
        payload = {'model': settings['model'], 'messages': [{'role': 'system', 'content': redact(system, [key])}, {'role': 'user', 'content': redact(user, [key])}], 'stream': False}
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if purpose == 'chapter' and len(body) > MAX_CHAPTER_REQUEST_BYTES:
            raise ValueError('本段模型请求超过 32,000 字节上限，未发送。已保存分段保留，请检查异常过长内容。')
        read_timeout = settings['generation_timeout'] if purpose == 'chapter' else (30 if purpose == 'connectivity' else 90)
        started = time.monotonic()
        # Bounded operational metadata only: no prompts, source, headers, URL or key.
        diagnostic = {'id': uuid.uuid4().hex, 'purpose': purpose, 'started_at': datetime.now(timezone.utc).isoformat(), 'request_bytes': len(body), 'read_timeout_seconds': read_timeout, 'status': 'failed'}
        try:
            with httpx.Client(timeout=httpx.Timeout(read_timeout, connect=15, write=30, pool=15), follow_redirects=False, transport=self.transport, trust_env=False) as client:
                response = client.post(endpoint, headers=headers, content=body)
            diagnostic.update({'http_status': response.status_code, 'response_bytes': len(response.content)})
            if response.status_code != 200:
                detail = redact(response.text[:1200], [key])
                raise ValueError(f'模型接口返回 HTTP {response.status_code}：{detail}')
            content = response.json()['choices'][0]['message']['content']
            if isinstance(content, list):
                content = '\n'.join(part.get('text', '') for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                raise ValueError('模型没有返回文本内容。')
            diagnostic['status'] = 'succeeded'
            return redact(content, [key])
        except httpx.TimeoutException as exc:
            diagnostic['exception_type'] = type(exc).__name__
            elapsed = round(time.monotonic() - started, 1)
            if isinstance(exc, httpx.ConnectTimeout):
                message = '连接模型接口超时（连接等待上限 15 秒），请检查接口地址和网络后重试。'
            elif isinstance(exc, httpx.ReadTimeout):
                message = f'等待模型响应超时（已耗时 {elapsed} 秒，连续响应等待上限 {read_timeout} 秒）。'
                message += '可在模型设置中增加章节响应等待上限，或稍后重试。' if purpose == 'chapter' else '请稍后重试或检查模型服务状态。'
            elif isinstance(exc, httpx.WriteTimeout):
                message = '向模型发送请求超时（发送等待上限 30 秒），请检查网络后重试。'
            elif isinstance(exc, httpx.PoolTimeout):
                message = '等待模型连接池超时，请稍后重试。'
            else:
                message = f'模型请求超时（已耗时 {elapsed} 秒），请稍后重试。'
            raise ValueError(message + '已有进度和文件未改变，未自动重试。') from exc
        except httpx.HTTPError as exc:
            diagnostic['exception_type'] = type(exc).__name__
            raise ValueError('无法连接模型接口，请检查地址与网络。') from exc
        except (KeyError, IndexError, TypeError) as exc:
            diagnostic['exception_type'] = type(exc).__name__
            raise ValueError('接口响应不符合 Chat Completions 格式。') from exc
        finally:
            diagnostic['duration_seconds'] = round(time.monotonic() - started, 3)
            with self.store.lock:
                recent = self.store.get('model_requests', 'recent', [])
                self.store.put('model_requests', 'recent', [diagnostic] + recent[:19])

    def ask_json(self, system, user, *, purpose='default'):
        return parse_json(self.chat(system + '\n只返回 JSON，不要 Markdown。', user, purpose=purpose))
