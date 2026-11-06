"""Plain model chat does not acquire the generator's automatic retry policy."""
import httpx
import pytest
from backend import model_client
from backend.storage import Store


@pytest.mark.parametrize('failure', [httpx.ConnectError, httpx.ReadTimeout, 429, 503])
def test_plain_chat_transport_failure_stays_nonretryable(tmp_path, failure):
    calls = []
    def handler(request):
        calls.append(request)
        if isinstance(failure, int):
            return httpx.Response(failure, text='unavailable')
        raise failure('unavailable', request=request)
    model = model_client.ModelClient(Store(tmp_path / 'state'), use_keyring=False, transport=httpx.MockTransport(handler))
    model.configure('http://127.0.0.1:9999/v1', 'synthetic-chat')
    with pytest.raises(ValueError) as caught:
        model.chat('system', 'hello', purpose='chapter')
    assert not isinstance(caught.value, getattr(model_client, 'RetryableModelError', ()))
    assert len(calls) == 1
