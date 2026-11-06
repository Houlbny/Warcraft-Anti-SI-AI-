"""OpenAI-compatible Chat Completions. User config only; never reads env keys."""
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from .credentials import CredentialError, CredentialStore
from .generation_validation import ContentValidationError

MAX_CHAPTER_REQUEST_BYTES = 32000


class RetryableModelError(ValueError):
    """A chapter transport failure; the caller owns the single retry budget."""

    def __init__(self, message, *, exception_type=None, http_status=None):
        super().__init__(message)
        self.details = {'kind': 'transport', 'retryable': True}
        if exception_type in {'ConnectError', 'ReadError', 'RemoteProtocolError', 'ConnectTimeout',
                              'ReadTimeout', 'WriteTimeout', 'PoolTimeout', 'TimeoutException'}:
            self.details['exception_type'] = exception_type
        if type(http_status) is int and (http_status == 429 or 500 <= http_status <= 599):
            self.details['http_status'] = http_status


class ModelFormatCompatibilityError(ValueError):
    """An explicit format rejection; the chapter caller owns any second call."""

    def __init__(self, *, requested_format, fallback_format):
        super().__init__('模型接口明确不支持本次输出格式，将使用已知兼容模式；已保存进度保留。')
        self.details = {'kind': 'format_compatibility', 'retryable': True,
                        'requested_format': requested_format, 'fallback_format': fallback_format}

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


def redact_json(value, secrets=()):
    """Mask data values, never JSON quote/escape syntax.

    Source snippets are ordinary string leaves here, so assignment redaction
    sees the actual source rather than a second JSON-escaped representation.
    """
    if isinstance(value, dict):
        return {redact(key, secrets): ('[REDACTED]' if isinstance(item, str) and item
                                      and re.fullmatch(_SECRET_FIELD, key, re.IGNORECASE)
                                      else redact_json(item, secrets)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_json(item, secrets) for item in value]
    return redact(value, secrets) if isinstance(value, str) else value


def redact_message(text, secrets=()):
    """Preserve structured request messages; plain prompts remain plain text."""
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return redact(text, secrets)
    if not isinstance(parsed, (dict, list)):
        return redact(text, secrets)
    return json.dumps(redact_json(parsed, secrets), ensure_ascii=False, separators=(',', ':'))


def encode_chat_request(model, system, user, *, response_format=None, secrets=()):
    """The exact UTF-8 request representation shared by sender and budget checks."""
    payload = {'model': model, 'messages': [
        {'role': 'system', 'content': redact_message(system, secrets)},
        {'role': 'user', 'content': redact_message(user, secrets)}], 'stream': False}
    if response_format is not None:
        payload['response_format'] = redact_json(response_format, secrets)
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _explicit_format_unsupported(response, response_format):
    """Only machine-readable parameter rejection permits a format fallback.

    In particular, malformed schemas and generic invalid-request responses must
    remain errors. Provider prose is neither logged nor used to infer support.
    """
    if not isinstance(response_format, dict) or response_format.get('type') not in {'json_schema', 'json_object'}:
        return False
    if not 400 <= response.status_code < 500 or response.status_code in {401, 403, 429}:
        return False
    try:
        envelope = response.json()
    except ValueError:
        return False
    error = envelope.get('error') if isinstance(envelope, dict) else None
    if not isinstance(error, dict):
        return False
    parameters = {'response_format', 'response_format.type'}
    if response_format['type'] == 'json_schema':
        parameters.update({'response_format.json_schema', 'response_format.json_schema.strict'})
    return (error.get('param') in parameters
            and error.get('code') in {'unsupported_parameter', 'unsupported_value', 'unsupported_format'})


def parse_json(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        categories = {'Expecting property name enclosed in double quotes': 'property_name',
                      "Expecting ',' delimiter": 'delimiter', "Expecting ':' delimiter": 'delimiter',
                      'Extra data': 'extra_data', 'Expecting value': 'invalid_value'}
        category = categories.get(exc.msg, 'unknown')
        for prefix, name in (('Unterminated string', 'unterminated_string'), ('Invalid \\escape', 'escape'),
                             ('Invalid control character', 'control_character')):
            if exc.msg.startswith(prefix):
                category = name
        raise ContentValidationError('response', 'invalid_json', actual_type='str', actual_length=len(text),
                                     json_line=exc.lineno, json_column=exc.colno, json_offset=exc.pos,
                                     json_error=category) from None
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
        self._format_lock = threading.RLock()
        self._format_identity = None
        self._format_observations = {'unsupported': set(), 'json_object': False}

    def _selected_format(self, identity, requested):
        with self._format_lock:
            if self._format_identity != identity:
                self._format_identity = identity
                self._format_observations = {'unsupported': set(), 'json_object': False}
            state = self._format_observations
            mode = requested.get('type') if isinstance(requested, dict) else None
            if mode in state['unsupported']:
                if mode == 'json_schema' and state['json_object'] and 'json_object' not in state['unsupported']:
                    return {'type': 'json_object'}, state
                return None, state
            return requested, state

    def _reject_format(self, state, response_format):
        with self._format_lock:
            mode = response_format['type']
            state['unsupported'].add(mode)
            if mode == 'json_schema' and state['json_object'] and 'json_object' not in state['unsupported']:
                return 'json_object'
            return 'text'

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
        with self._format_lock:
            self._format_identity = None
            self._format_observations = {'unsupported': set(), 'json_object': False}
        return self.public()

    def chat(self, system, user, *, purpose='default'):
        return self._request(system, user, purpose=purpose, expect_json=False)

    def _request(self, system, user, *, purpose, expect_json, response_format=None):
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
        requested_body = encode_chat_request(settings['model'], system, user,
                                             response_format=response_format, secrets=[key])
        if purpose == 'chapter' and len(requested_body) > MAX_CHAPTER_REQUEST_BYTES:
            raise ValueError('本段模型请求超过 32,000 字节上限，未发送。已保存分段保留，请检查异常过长内容。')
        effective_format, format_state = response_format, None
        if purpose == 'chapter' and expect_json:
            effective_format, format_state = self._selected_format((endpoint, settings['model']), response_format)
        body = (requested_body if effective_format == response_format else
                encode_chat_request(settings['model'], system, user, response_format=effective_format, secrets=[key]))
        read_timeout = settings['generation_timeout'] if purpose == 'chapter' else (30 if purpose == 'connectivity' else 90)
        started = time.monotonic()
        # Bounded operational metadata only: no prompts, source, headers, URL or key.
        diagnostic = {'id': uuid.uuid4().hex, 'purpose': purpose, 'started_at': datetime.now(timezone.utc).isoformat(),
                      'request_bytes': len(body), 'read_timeout_seconds': read_timeout, 'status': 'failed',
                      'transport_status': 'failed', 'content_status': 'not_received'}
        if isinstance(response_format, dict) and response_format.get('type') in {'json_schema', 'json_object'}:
            diagnostic['requested_format'] = response_format['type']
            diagnostic['effective_format'] = effective_format['type'] if effective_format else 'text'
            diagnostic['requested_request_bytes'] = len(requested_body)
        try:
            metadata = json.loads(user)
        except (ValueError, TypeError):
            metadata = None
        if (isinstance(metadata, dict) and isinstance(metadata.get('stage'), str)
                and metadata['stage'] in {'analyzing', 'planning', 'materials', 'lesson_plan', 'lesson_check', 'quests', 'checking'}):
            diagnostic['stage'] = metadata['stage']
            for field, maximum in (('quest_number', 10), ('batch', 100)):
                if type(metadata.get(field)) is int and 1 <= metadata[field] <= maximum:
                    diagnostic[field] = metadata[field]
        try:
            with httpx.Client(timeout=httpx.Timeout(read_timeout, connect=15, write=30, pool=15), follow_redirects=False, transport=self.transport, trust_env=False) as client:
                response = client.post(endpoint, headers=headers, content=body)
            diagnostic.update({'http_status': response.status_code, 'response_bytes': len(response.content),
                               'transport_status': 'succeeded'})
            if response.status_code != 200:
                if format_state is not None and _explicit_format_unsupported(response, effective_format):
                    fallback = self._reject_format(format_state, effective_format)
                    diagnostic.update(content_status='format_unsupported', retryable=True, fallback_format=fallback)
                    raise ModelFormatCompatibilityError(requested_format=effective_format['type'], fallback_format=fallback)
                if purpose == 'chapter' and expect_json and (response.status_code == 429 or 500 <= response.status_code <= 599):
                    diagnostic['retryable'] = True
                    raise RetryableModelError(f'模型接口暂时不可用（HTTP {response.status_code}）。',
                                              http_status=response.status_code)
                detail = redact(response.text[:1200], [key])
                raise ValueError(f'模型接口返回 HTTP {response.status_code}：{detail}')
            try:
                envelope = response.json()
            except ValueError:
                diagnostic['content_status'] = 'invalid_envelope'
                raise ValueError('接口响应不是有效的 Chat Completions JSON，未读取模型内容。') from None
            choice = envelope['choices'][0]
            finish = choice.get('finish_reason')
            diagnostic['finish_reason'] = finish if finish in {'stop', 'length', 'content_filter', 'tool_calls', 'function_call'} else 'unknown'
            usage = envelope.get('usage')
            if isinstance(usage, dict):
                diagnostic['usage'] = {name: usage[name] for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                                       if type(usage.get(name)) is int and usage[name] >= 0}
            if choice['message'].get('refusal'):
                diagnostic['content_status'] = 'refused'
                raise ValueError('模型拒绝了本段生成；已保存进度保留，未自动重试该拒绝。')
            if finish == 'content_filter':
                diagnostic['content_status'] = 'filtered'
                raise ValueError('模型服务中止了本段内容生成；已保存进度保留。')
            if finish == 'length':
                diagnostic['content_status'] = 'truncated'
                raise ContentValidationError('response', 'truncated')
            content = choice['message']['content']
            if isinstance(content, list):
                content = '\n'.join(part.get('text', '') for part in content if isinstance(part, dict))
            if not isinstance(content, str) or not content.strip():
                diagnostic['content_status'] = 'empty'
                if expect_json:
                    error = ContentValidationError('response', 'empty' if isinstance(content, str) else 'type',
                                                   actual_type=type(content).__name__, expected_type='str')
                    diagnostic['validation_error'] = error.details
                    raise error
                raise ValueError('模型没有返回文本内容。')
            if expect_json:
                try:
                    value = parse_json(content)
                except ContentValidationError as error:
                    diagnostic['content_status'] = error.details['reason']
                    diagnostic['validation_error'] = error.details
                    raise
                diagnostic.update(status='succeeded', content_status='valid_json')
                if (format_state is not None and isinstance(value, dict)
                        and effective_format == {'type': 'json_object'}):
                    # Retain the captured state object: a concurrent configure()
                    # replaces it, so an old response cannot teach the new config.
                    with self._format_lock:
                        format_state['json_object'] = True
                return redact_json(value, [key])
            diagnostic['status'] = 'succeeded'
            diagnostic['content_status'] = 'text_received'
            return redact_message(content, [key])
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
            if purpose == 'chapter' and expect_json:
                diagnostic['retryable'] = True
                raise RetryableModelError(message, exception_type=type(exc).__name__) from exc
            raise ValueError(message + '已有进度和文件未改变，未自动重试。') from exc
        except httpx.HTTPError as exc:
            diagnostic['exception_type'] = type(exc).__name__
            if (purpose == 'chapter' and expect_json
                    and isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError))):
                diagnostic['retryable'] = True
                raise RetryableModelError('模型接口暂时连接失败或连接中断。', exception_type=type(exc).__name__) from exc
            raise ValueError('无法连接模型接口，请检查地址与网络。') from exc
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            diagnostic['exception_type'] = type(exc).__name__
            diagnostic['content_status'] = 'invalid_envelope'
            raise ValueError('接口响应不符合 Chat Completions 格式。') from exc
        finally:
            diagnostic['duration_seconds'] = round(time.monotonic() - started, 3)
            with self.store.lock:
                recent = self.store.get('model_requests', 'recent', [])
                self.store.put('model_requests', 'recent', [diagnostic] + recent[:19])

    def ask_json(self, system, user, *, purpose='default', response_format=None):
        # The caller supplies a complete prompt, including its frozen JSON contract.
        return self._request(system, user, purpose=purpose, expect_json=True, response_format=response_format)
