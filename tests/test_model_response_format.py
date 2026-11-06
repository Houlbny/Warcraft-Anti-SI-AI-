"""Native format requests share the real wire and the caller's retry budget."""
import copy
import json

import httpx
import pytest

from backend import model_client
from backend.generation_validation import ContentValidationError
from backend.storage import Store


STRICT = {'type': 'json_schema', 'json_schema': {'name': 'lesson', 'strict': True,
          'schema': {'type': 'object', 'properties': {'steps': {'type': 'array', 'items': {'type': 'string'}}},
                     'required': ['steps'], 'additionalProperties': False}}}
OBJECT = {'type': 'json_object'}


def reply(content='{}', *, finish='stop', refusal=None):
    return httpx.Response(200, json={'choices': [{'finish_reason': finish,
                           'message': {'content': content, 'refusal': refusal}}]})


def unsupported(*, status=400, code='unsupported_parameter', param='response_format'):
    return httpx.Response(status, json={'error': {'code': code, 'param': param,
                           'message': 'PRIVATE_PROVIDER_DIAGNOSTIC'}})


def client(tmp_path, responses):
    seen = []
    def handler(request):
        seen.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    store = Store(tmp_path / 'state')
    model = model_client.ModelClient(store, use_keyring=False, transport=httpx.MockTransport(handler))
    model.configure('http://127.0.0.1:9999/v1', 'test', 'SYNTHETIC_CONFIGURED_KEY')
    return model, store, seen


def ask(model, response_format=STRICT, **kwargs):
    return model.ask_json('Return JSON.', '{"stage":"quests","quest_number":1}',
                          purpose='chapter', response_format=response_format, **kwargs)


def test_native_schema_wire_is_shared_redacted_and_input_unchanged(tmp_path):
    model, _, seen = client(tmp_path, [reply('{"steps":["read"]}')])
    format_copy = copy.deepcopy(STRICT)
    user = json.dumps({'source': 'token = "private_source_value"', 'key': 'SYNTHETIC_CONFIGURED_KEY'})
    assert model.ask_json('Return JSON.', user, purpose='chapter', response_format=STRICT) == {'steps': ['read']}
    expected = model_client.encode_chat_request('test', 'Return JSON.', user,
                        response_format=STRICT, secrets=['SYNTHETIC_CONFIGURED_KEY'])
    assert seen[0].content == expected
    body = json.loads(expected)
    assert body['response_format'] == STRICT == format_copy
    assert 'private_source_value' not in expected.decode()
    assert 'SYNTHETIC_CONFIGURED_KEY' not in expected.decode()
    assert json.loads(body['messages'][1]['content'])['source'] == 'token = "[REDACTED]"'


def test_default_chat_and_json_wire_do_not_add_format(tmp_path):
    model, _, seen = client(tmp_path, [reply('text'), reply()])
    assert model.chat('s', 'u') == 'text'
    assert model.ask_json('s', 'u') == {}
    expected = model_client.encode_chat_request('test', 's', 'u')
    assert all(request.content == expected for request in seen)
    assert 'response_format' not in json.loads(expected)


def test_explicit_format_rejection_uses_confirmed_object_on_next_call_only(tmp_path):
    model, store, seen = client(tmp_path, [reply(), unsupported(), reply('{"steps":[]}')])
    assert ask(model, OBJECT) == {}
    with pytest.raises(model_client.ModelFormatCompatibilityError) as error:
        ask(model)
    assert len(seen) == 2  # No hidden HTTP retry.
    assert error.value.details == {'kind': 'format_compatibility', 'retryable': True,
                                  'requested_format': 'json_schema', 'fallback_format': 'json_object'}
    diagnostic = store.get('model_requests', 'recent')[0]
    assert diagnostic['content_status'] == 'format_unsupported'
    assert diagnostic['transport_status'] == 'succeeded'
    assert 'PRIVATE_PROVIDER_DIAGNOSTIC' not in json.dumps(diagnostic)
    assert 'PRIVATE_PROVIDER_DIAGNOSTIC' not in str(error.value)
    assert ask(model) == {'steps': []}
    assert [json.loads(r.content)['response_format']['type'] for r in seen] == ['json_object', 'json_schema', 'json_object']


@pytest.mark.parametrize('first_format', [None, STRICT])
def test_plain_or_strict_success_does_not_claim_json_object_support(tmp_path, first_format):
    model, _, seen = client(tmp_path, [reply(), unsupported(), reply()])
    ask(model, first_format)
    with pytest.raises(model_client.ModelFormatCompatibilityError) as error:
        ask(model)
    assert error.value.details['fallback_format'] == 'text'
    ask(model)
    assert 'response_format' not in json.loads(seen[-1].content)


@pytest.mark.parametrize('bad_response', [reply('[]'), reply('{'), reply('{}', finish='length'),
                                         reply(None, refusal='no'), reply('{}', finish='content_filter')])
def test_unaccepted_object_response_does_not_confirm_mode(tmp_path, bad_response):
    model, _, seen = client(tmp_path, [bad_response, unsupported(), reply()])
    try:
        ask(model, OBJECT)
    except ValueError:
        pass
    with pytest.raises(model_client.ModelFormatCompatibilityError) as error:
        ask(model)
    assert error.value.details['fallback_format'] == 'text'
    ask(model)
    assert 'response_format' not in json.loads(seen[-1].content)


def test_object_unsupported_is_cached_but_plain_request_is_not_upgraded(tmp_path):
    model, _, seen = client(tmp_path, [unsupported(), reply(), reply()])
    with pytest.raises(model_client.ModelFormatCompatibilityError) as error:
        ask(model, OBJECT)
    assert error.value.details['fallback_format'] == 'text'
    ask(model, OBJECT)
    ask(model, None)
    assert all('response_format' not in json.loads(r.content) for r in seen[1:])


@pytest.mark.parametrize('status,code,param', [
    (400, 'invalid_json_schema', 'response_format'),
    (400, 'invalid_request_error', 'response_format'),
    (400, 'unsupported_parameter', 'temperature'),
    (400, 'unsupported_parameter', 'response_format.json_schema.schema.properties'),
    (400, None, 'response_format'),
    (401, 'unsupported_parameter', 'response_format'),
    (403, 'unsupported_parameter', 'response_format'),
    (429, 'unsupported_parameter', 'response_format'),
    (500, 'unsupported_parameter', 'response_format'),
])
def test_unrelated_or_nonexplicit_errors_never_downgrade(tmp_path, status, code, param):
    model, _, seen = client(tmp_path, [unsupported(status=status, code=code, param=param), reply()])
    with pytest.raises(ValueError) as error:
        ask(model)
    assert not isinstance(error.value, model_client.ModelFormatCompatibilityError)
    ask(model)
    assert json.loads(seen[-1].content)['response_format'] == STRICT


@pytest.mark.parametrize('param', ['response_format', 'response_format.type', 'response_format.json_schema',
                                  'response_format.json_schema.strict'])
@pytest.mark.parametrize('code', ['unsupported_parameter', 'unsupported_value', 'unsupported_format'])
def test_exact_unsupported_parameters_are_compatibility_failures(tmp_path, param, code):
    model, _, seen = client(tmp_path, [unsupported(param=param, code=code)])
    with pytest.raises(model_client.ModelFormatCompatibilityError):
        ask(model)
    assert len(seen) == 1


@pytest.mark.parametrize('changed', ['model', 'base_url', 'configure'])
def test_mode_observations_reset_with_configuration(tmp_path, changed):
    model, store, seen = client(tmp_path, [reply(), unsupported(), reply()])
    ask(model, OBJECT)
    with pytest.raises(model_client.ModelFormatCompatibilityError):
        ask(model)
    if changed == 'configure':
        model.configure('http://127.0.0.1:9999/v1', 'test', None)
    else:
        settings = store.get('settings', 'model')
        settings[changed] = 'other-model' if changed == 'model' else 'http://127.0.0.1:9998/v1'
        store.put('settings', 'model', settings)
    ask(model)
    assert json.loads(seen[-1].content)['response_format'] == STRICT


def test_full_requested_schema_counts_against_limit_even_when_cached_fallback_fits(tmp_path):
    model, _, seen = client(tmp_path, [unsupported()])
    with pytest.raises(model_client.ModelFormatCompatibilityError):
        ask(model)
    large = copy.deepcopy(STRICT)
    large['json_schema']['schema']['description'] = 'x' * model_client.MAX_CHAPTER_REQUEST_BYTES
    with pytest.raises(ValueError, match='32,000'):
        ask(model, large)
    assert len(seen) == 1


def test_nonchapter_explicit_format_error_does_not_enable_automatic_compatibility(tmp_path):
    model, _, seen = client(tmp_path, [unsupported(), reply()])
    with pytest.raises(ValueError) as error:
        model.ask_json('JSON.', '{}', response_format=STRICT)
    assert not isinstance(error.value, model_client.ModelFormatCompatibilityError)
    ask(model)
    assert json.loads(seen[-1].content)['response_format'] == STRICT
