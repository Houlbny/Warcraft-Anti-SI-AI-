"""Keep backend.api's module-level app away from the user's active save files."""
import os
import tempfile

_previous_data = os.environ.get('CODEWARDEN_DATA')
_import_state = tempfile.TemporaryDirectory(prefix='codewarden-test-import-', ignore_cleanup_errors=True)
os.environ['CODEWARDEN_DATA'] = _import_state.name


def pytest_unconfigure(config):
    if _previous_data is None:
        os.environ.pop('CODEWARDEN_DATA', None)
    else:
        os.environ['CODEWARDEN_DATA'] = _previous_data
    _import_state.cleanup()
