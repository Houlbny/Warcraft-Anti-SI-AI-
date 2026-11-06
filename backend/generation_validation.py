"""Model-content validation with metadata-only, serializable diagnostics.

Paths must be fixed schema paths supplied by application code, never response
values. Diagnostics intentionally retain neither the response nor its text.
"""
import re


_REASONS = {
    'missing': '缺少必填字段',
    'type': '字段类型不正确',
    'empty': '文本去除首尾空白后为空',
    'too_long': '文本超过长度上限',
    'invalid_json': '返回内容不是有效的 JSON',
    'truncated': '模型输出被截断，未收到完整内容',
    'invalid_content': '内容不符合生成要求',
}
_TYPES = frozenset({'NoneType', 'str', 'dict', 'list', 'bool', 'int', 'float', 'tuple', 'set', 'bytes', 'other'})
_PATH = re.compile(r'[A-Za-z_][A-Za-z_0-9]*(?:\[[0-9]+\]|\.[A-Za-z_][A-Za-z_0-9]*)*\Z')


class ContentValidationError(ValueError):
    """A safe validation failure; optional details contain no returned values."""

    def __init__(self, field, reason, *, actual_type=None, expected_type=None, actual_length=None, maximum=None,
                 json_line=None, json_column=None, json_offset=None, json_error=None):
        field = field if isinstance(field, str) and len(field) <= 200 and _PATH.fullmatch(field) else 'content'
        reason = reason if isinstance(reason, str) and reason in _REASONS else 'invalid_content'
        details = {'field': field, 'reason': reason}
        if actual_type is not None:
            details['actual_type'] = actual_type if isinstance(actual_type, str) and actual_type in _TYPES else 'other'
        if expected_type is not None:
            details['expected_type'] = expected_type if isinstance(expected_type, str) and expected_type in _TYPES else 'other'
        if type(actual_length) is int and actual_length >= 0:
            details['actual_length'] = actual_length
        if type(maximum) is int and maximum >= 0:
            details['maximum'] = maximum
        for name, value in (('json_line', json_line), ('json_column', json_column), ('json_offset', json_offset)):
            if type(value) is int and value >= 0:
                details[name] = value
        if json_error in {'property_name', 'delimiter', 'unterminated_string', 'escape',
                          'control_character', 'extra_data', 'invalid_value', 'unknown'}:
            details['json_error'] = json_error
        self._details = details
        message = f'章节字段 {field}：{_REASONS[reason]}'
        if reason == 'type' and 'expected_type' in details:
            message += f'（应为 {details["expected_type"]}，实际为 {details.get("actual_type", "unknown")}）'
        if reason == 'too_long' and 'actual_length' in details and 'maximum' in details:
            message += f'（去除首尾空白后 {details["actual_length"]} 字符，上限 {details["maximum"]} 字符）'
        super().__init__(message + '，未发布关卡。')

    @property
    def details(self):
        return self._details.copy()

    def to_dict(self):
        return self.details


def text_field(item, key, maximum=3000, path=None, *, allow_empty=False):
    """Return stripped text, measuring the same text used by downstream checks."""
    field = path if path is not None else key
    if not isinstance(item, dict):
        raise ContentValidationError(field, 'type', actual_type=type(item).__name__, expected_type='dict')
    if key not in item:
        raise ContentValidationError(field, 'missing')
    value = item[key]
    if not isinstance(value, str):
        raise ContentValidationError(field, 'type', actual_type=type(value).__name__, expected_type='str')
    value = value.strip()
    length = len(value)
    if not length and not allow_empty:
        raise ContentValidationError(field, 'empty', actual_type='str', actual_length=0, maximum=maximum)
    if length > maximum:
        raise ContentValidationError(field, 'too_long', actual_type='str', actual_length=length, maximum=maximum)
    return value
