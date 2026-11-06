"""Real serialization paths must preserve JSON while masking credential values."""
import json

import httpx
import pytest

from backend import model_client
from backend.generation_validation import ContentValidationError
from backend.storage import Store


def client(tmp_path, handler):
    store = Store(tmp_path / 'state')
    model = model_client.ModelClient(store, use_keyring=False, transport=httpx.MockTransport(handler))
    model.configure('http://127.0.0.1:9999/v1', 'test', 'SYNTHETIC_CONFIGURED_KEY')
    return model, store


@pytest.mark.parametrize('field', ['token', 'password', 'api_key'])
def test_serialized_source_remains_valid_json_after_request_redaction(tmp_path, field):
    original = {'stage': 'quests', 'quest_number': 4, 'sources': [
        {'content': f'{field} = "SYNTHETIC_SOURCE_VALUE"\nprint({field})\n'}]}
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(json.loads(body['messages'][1]['content']))
        assert request.headers['Authorization'] == 'Bearer SYNTHETIC_CONFIGURED_KEY'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    model, _ = client(tmp_path, handler)
    assert model.ask_json('Return JSON.', json.dumps(original), purpose='chapter') == {}
    assert seen[0]['sources'][0]['content'] == f'{field} = "[REDACTED]"\nprint({field})\n'


@pytest.mark.parametrize('field', ['token', 'password', 'api_key'])
def test_valid_model_json_is_parsed_before_source_literal_redaction(tmp_path, field):
    returned = {'quest': {'body': f'{field} = "SYNTHETIC_RESPONSE_VALUE"\nprint({field})',
                          'rubric': ['说明类型', '解释返回值']}}

    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(returned)}}]})

    model, store = client(tmp_path, handler)
    result = model.ask_json('Return JSON.', '{}', purpose='chapter')
    assert result['quest']['body'] == f'{field} = "[REDACTED]"\nprint({field})'
    assert result['quest']['rubric'] == returned['quest']['rubric']
    assert 'SYNTHETIC_RESPONSE_VALUE' not in json.dumps(store.get('model_requests', 'recent'))


def test_structured_redaction_is_idempotent_and_preserves_nested_types():
    original = {'source': 'token = "already masked"', 'api_key': 'secret "with quotes"',
                'nested': [{'numbers': [0, 1.25, True, None], 'body': 'print("normal")'}]}
    once = model_client.redact_message(json.dumps(original))
    twice = model_client.redact_message(once)
    assert json.loads(once) == json.loads(twice)
    result = json.loads(once)
    assert result['api_key'] == '[REDACTED]'
    assert result['nested'] == original['nested']


def test_invalid_json_logs_parse_location_and_distinguishes_transport_success(tmp_path):
    broken = '{"quest": {"body": "PRIVATE_SOURCE_TEXT", }}'

    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': broken}}],
                                       'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30,
                                                 'private': 'PRIVATE_SOURCE_TEXT'}})

    model, store = client(tmp_path, handler)
    with pytest.raises(ContentValidationError) as error:
        model.ask_json('Return JSON.', '{"stage":"quests","quest_number":4}', purpose='chapter')
    assert error.value.details['json_offset'] > 0
    assert error.value.details['json_line'] == 1
    assert error.value.details['json_error'] == 'property_name'
    record = store.get('model_requests', 'recent')[0]
    assert record['status'] == 'failed'
    assert record['transport_status'] == 'succeeded'
    assert record['content_status'] == 'invalid_json'
    assert record['finish_reason'] == 'stop'
    assert record['stage'] == 'quests' and record['quest_number'] == 4
    assert record['usage'] == {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}
    assert 'PRIVATE_SOURCE_TEXT' not in json.dumps(record)


def test_truncation_is_reported_even_if_partial_response_happens_to_parse(tmp_path):
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]})
    model, store = client(tmp_path, handler)
    with pytest.raises(ContentValidationError) as error:
        model.ask_json('Return JSON.', '{}', purpose='chapter')
    assert error.value.details['reason'] == 'truncated'
    assert store.get('model_requests', 'recent')[0]['content_status'] == 'truncated'


@pytest.mark.parametrize('finish,refusal', [('stop', 'PRIVATE_REFUSAL'), ('content_filter', None)])
def test_refusal_or_filter_is_not_accepted_as_generated_content(tmp_path, finish, refusal):
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': {'content': None, 'refusal': refusal}}]})
    model, store = client(tmp_path, handler)
    with pytest.raises(ValueError) as error:
        model.ask_json('Return JSON.', '{}', purpose='chapter')
    assert not isinstance(error.value, ContentValidationError)
    record = store.get('model_requests', 'recent')[0]
    assert record['status'] == 'failed'
    assert record['content_status'] in {'refused', 'filtered'}
    assert 'PRIVATE_REFUSAL' not in json.dumps(record)


def test_malformed_envelope_and_nullable_usage_are_handled_without_false_success(tmp_path):
    replies = [httpx.Response(200, content='PRIVATE_NON_JSON'),
               httpx.Response(200, json={'usage': None, 'choices': [{'message': {'content': '{}'}}]})]
    model, store = client(tmp_path, lambda request: replies.pop(0))
    with pytest.raises(ValueError):
        model.ask_json('Return JSON.', '{}')
    assert store.get('model_requests', 'recent')[0]['content_status'] == 'invalid_envelope'
    assert model.ask_json('Return JSON.', '{}') == {}
    assert store.get('model_requests', 'recent')[0]['content_status'] == 'valid_json'


@pytest.mark.parametrize('content,reason', [('', 'empty'), (' \n\t', 'empty'), (None, 'type'), (17, 'type'), ({}, 'type')])
def test_empty_json_content_is_a_safe_correctable_validation_failure(tmp_path, content, reason):
    def handler(request):
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]})
    model, store = client(tmp_path, handler)
    with pytest.raises(ContentValidationError) as error:
        model.ask_json('Return JSON.', '{}', purpose='chapter')
    assert error.value.details['field'] == 'response'
    assert error.value.details['reason'] == reason
    assert error.value.details['actual_type'] == type(content).__name__
    record = store.get('model_requests', 'recent')[0]
    assert record['transport_status'] == 'succeeded'
    assert record['content_status'] == 'empty'
    assert record['validation_error'] == error.value.details


@pytest.mark.parametrize('content', ['', None])
def test_plain_chat_keeps_existing_empty_content_error(tmp_path, content):
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    model, store = client(tmp_path, handler)
    with pytest.raises(ValueError) as error:
        model.chat('Say hello.', 'hello')
    assert not isinstance(error.value, ContentValidationError)
    assert store.get('model_requests', 'recent')[0]['content_status'] == 'empty'
