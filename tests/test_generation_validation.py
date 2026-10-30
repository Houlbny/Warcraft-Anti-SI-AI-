"""Safe model-content diagnostics using synthetic, in-memory responses only."""
import json

import pytest

from backend import campaigns


def validation_module():
    from backend import generation_validation
    return generation_validation


@pytest.mark.parametrize('item,reason,actual_type', [
    ({}, 'missing', None),
    ({'summary': None}, 'type', 'NoneType'),
    ({'summary': {'secret': 'synthetic-secret'}}, 'type', 'dict'),
    ({'summary': ['synthetic-secret']}, 'type', 'list'),
    ({'summary': ' \n\t　'}, 'empty', 'str'),
])
def test_text_diagnostics_distinguish_missing_type_and_empty(item, reason, actual_type):
    module = validation_module()
    with pytest.raises(module.ContentValidationError) as caught:
        module.text_field(item, 'summary', maximum=400, path='analysis.summary')
    details = caught.value.details
    assert details['field'] == 'analysis.summary'
    assert details['reason'] == reason
    assert details.get('actual_type') == actual_type
    if reason == 'type':
        assert details['expected_type'] == 'str'
    assert isinstance(caught.value, ValueError)
    assert 'synthetic-secret' not in str(caught.value)


@pytest.mark.parametrize('maximum', [240, 400, 1200])
def test_length_limit_uses_trimmed_text_and_accepts_exact_boundary(maximum):
    module = validation_module()
    text = '文' * maximum
    assert module.text_field({'summary': ' \n　' + text + '\t '}, 'summary', maximum=maximum) == text
    with pytest.raises(module.ContentValidationError) as caught:
        module.text_field({'summary': ' ' + text + '多\n'}, 'summary', maximum=maximum)
    assert caught.value.to_dict() == {
        'field': 'summary', 'reason': 'too_long', 'actual_type': 'str',
        'actual_length': maximum + 1, 'maximum': maximum,
    }
    assert str(maximum + 1) in str(caught.value)
    assert str(maximum) in str(caught.value)


@pytest.mark.parametrize('reason', ['missing', 'type', 'empty', 'too_long', 'invalid_json', 'invalid_content'])
def test_serialized_error_contains_metadata_only(reason):
    module = validation_module()
    error = module.ContentValidationError('quests[2].steps[1].instruction', reason,
                                          actual_type='dict', actual_length=401, maximum=400)
    serialized = json.dumps(error.to_dict(), ensure_ascii=False)
    assert set(error.to_dict()) == {'field', 'reason', 'actual_type', 'actual_length', 'maximum'}
    assert error.to_dict()['reason'] == reason
    assert 'quests[2].steps[1].instruction' in serialized
    assert error.args == (str(error),)
    external = error.details
    external['reason'] = 'synthetic-secret'
    assert error.details['reason'] == reason


def test_error_never_serializes_response_or_custom_type_representation():
    module = validation_module()
    secret = 'synthetic-token-SHOULD-NEVER-BE-DIAGNOSTIC'
    sensitive_type = type(secret, (), {'__repr__': lambda self: secret})
    with pytest.raises(module.ContentValidationError) as caught:
        module.text_field({'summary': sensitive_type()}, 'summary')
    assert caught.value.details['actual_type'] == 'other'
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in json.dumps(caught.value.to_dict())
    with pytest.raises(module.ContentValidationError) as caught:
        module.text_field({'summary': secret * 100}, 'summary', maximum=400)
    assert secret not in str(caught.value)
    assert secret not in json.dumps(caught.value.to_dict())


def sample_task():
    return {
        'type': 'explore', 'focus': 'language', 'title': '读取函数', 'brief': '阅读源码',
        'objective': '说明返回值', 'source_file': 'main.py', 'line': 1,
        'steps': [{'title': '阅读', 'instruction': '打开源码'}, {'title': '提交', 'instruction': '回答问题'}],
        'hints': ['查看输入', '查看表达式', '查看返回值'], 'concepts': [], 'rubric': [],
        'verification_mode': 'answer', 'verification': '答案匹配', 'answer': '结果',
        'success_message': '已回答',
    }


@pytest.mark.parametrize('where,expected', [
    ('title', 'quests[0].title'),
    ('step', 'quests[0].steps[1].instruction'),
    ('answer', 'quests[0].answer'),
    ('expected_output', 'quests[0].expected_output'),
])
def test_campaign_validation_reports_fixed_task_and_step_paths(where, expected):
    module = validation_module()
    task = sample_task()
    if where == 'step':
        task['steps'][1]['instruction'] = None
    elif where == 'expected_output':
        task.update(type='repair', verification_mode='run', expected_output=None)
    else:
        task[where] = None
    with pytest.raises(module.ContentValidationError) as caught:
        campaigns.Campaigns(None, None, None, None).validate(
            [task], 'mage', [{'path': 'main.py', 'line_count': 3, 'hash': 'synthetic'}], partial=True)
    assert caught.value.details['field'] == expected


def test_campaign_reexports_helper_and_preserves_expected_output_stripping():
    assert campaigns.text_field is validation_module().text_field
    task = sample_task()
    task.update(type='repair', verification_mode='run', expected_output=' \nanswer=2\n ')
    result = campaigns.Campaigns(None, None, None, None).validate(
        [task], 'mage', [{'path': 'main.py', 'line_count': 3, 'hash': 'synthetic'}], partial=True)
    assert result[0]['expected_output'] == 'answer=2'


def test_checker_type_error_does_not_request_text_for_boolean_field():
    from backend.campaign_generation import validate_check
    with pytest.raises(validation_module().ContentValidationError) as caught:
        validate_check({'consistent': 'true', 'issues': []})
    assert caught.value.details['expected_type'] == 'bool'
    assert '需要文本' not in str(caught.value)
