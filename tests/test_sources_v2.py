import base64
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.storage import Store
from backend.sources import SourceImports


def upload(path, text):
    raw = text.encode() if isinstance(text, str) else text
    return {'path': path, 'data_base64': base64.b64encode(raw).decode()}


@pytest.fixture
def sources(tmp_path):
    return SourceImports(Store(tmp_path / 'state'))


def test_document_directory_and_unknown_language_are_first_class(sources, tmp_path):
    original = tmp_path / 'materials'
    original.mkdir()
    (original / '说明.md').write_text('# 灰烬水门\n开启前先查看水位。', encoding='utf-8')
    (original / 'main.rs').write_text('fn main() { println!("hello"); }', encoding='utf-8')
    (original / '.env').write_text('SECRET=never-import', encoding='utf-8')
    record = sources.create(path=str(original))
    assert {f['path'] for f in record['files']} == {'说明.md', 'main.rs'}
    assert record['status'] == 'imported'
    assert '开启前' in sources.read(record['id'], '说明.md')['content']
    assert 'fragments' not in sources.public(record)
    (original / '说明.md').write_text('CHANGED', encoding='utf-8')
    assert '开启前' in sources.read(record['id'], '说明.md')['content']


@pytest.mark.parametrize('name', ['../escape.md', '/escape.md', 'C:\\escape.md', 'dir/../../escape.md', 'dir/CON.txt', 'x.md:stream', 'dir/evil.'])
def test_upload_rejects_unsafe_names(sources, name):
    with pytest.raises(ValueError):
        sources.create(files=[upload(name, 'content')])


def test_partial_unreadable_files_are_visible(sources):
    record = sources.create(files=[upload('guide.md', '# A\nReadable'), upload('old.txt', b'\xff\x81'), upload('picture.png', b'\x89PNG\0')])
    assert [x['path'] for x in record['files']] == ['guide.md']
    assert any('old.txt' in issue for issue in record['diagnostics'])
    assert any('picture.png' in issue for issue in record['diagnostics'])


def pdf_bytes(tmp_path, text='Hello from the aqueduct', empty_second=True):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 20 200 Td ({text}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    if empty_second:
        writer.add_blank_page(width=300, height=300)
    target = tmp_path / 'book.pdf'
    writer.write(target)
    return target.read_bytes()


def test_pdf_keeps_page_evidence_and_reports_unreadable_pages(sources, tmp_path):
    record = sources.create(files=[upload('book.pdf', pdf_bytes(tmp_path))])
    assert record['files'][0]['pages'] == 2
    fragments = sources.context(record['id'])
    assert fragments[0]['page'] == 1
    assert 'aqueduct' in fragments[0]['content']
    assert any('2' in d and '文字' in d for d in record['diagnostics'])
    assert 'aqueduct' in sources.read(record['id'], 'book.pdf')['content']


def test_scanned_only_pdf_is_not_a_successful_import(sources, tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    file = tmp_path / 'scanned.pdf'
    writer.write(file)
    with pytest.raises(ValueError, match='可读|文字'):
        sources.create(path=str(file))


def test_task_workspaces_are_independent_and_do_not_recopy(sources):
    record = sources.create(files=[upload('main.py', 'print(1)\n')])
    one = sources.workspace(record['id'], 'task_one')
    two = sources.workspace(record['id'], 'task_two')
    (one / 'main.py').write_text('print(2)\n', encoding='utf-8')
    assert (sources.workspace(record['id'], 'task_one') / 'main.py').read_text() == 'print(2)\n'
    assert (two / 'main.py').read_text() == 'print(1)\n'
    assert sources.read(record['id'], 'main.py')['content'] == 'print(1)\n'
    with pytest.raises(ValueError):
        sources.workspace(record['id'], '../escape')


def test_scope_and_context_budget_are_explicit(sources):
    record = sources.create(files=[upload('one.md', '水门说明\n' * 1000), upload('two.md', '炉火说明\n' * 1000)])
    context = sources.context(record['id'], max_chars=500)
    assert 0 < sum(len(x['content']) for x in context) <= 500
    scoped = sources.select_scope(record['id'], ['two.md'])
    assert scoped['status'] == 'imported'
    assert all(x['path'] == 'two.md' for x in sources.context(record['id']))
    assert sources.public(scoped)['coverage']['selected_chars'] < sources.public(scoped)['coverage']['total_chars']
    with pytest.raises(ValueError):
        sources.select_scope(record['id'], ['missing.md'])


def test_chinese_and_escaped_context_fits_utf8_request_budget(sources):
    import json
    record = sources.create(files=[upload('中文.md', ('知识理解与复习\\n"条件"\n' * 1200))])
    context = sources.context(record['id'])
    assert len(json.dumps(context, ensure_ascii=False).encode('utf-8')) <= 16000
    coverage = sources.public(record)['coverage']
    assert coverage['context_chars'] == sum(len(s['content']) for s in context)
    assert coverage['truncated'] is True


def test_case_collisions_and_bad_base64_rejected(sources):
    with pytest.raises(ValueError):
        sources.create(files=[upload('Guide.md', 'a'), upload('guide.md', 'b')])
    with pytest.raises(ValueError):
        sources.create(files=[{'path': 'a.md', 'data_base64': 'not-base64!'}])


def test_delete_only_managed_snapshot(sources, tmp_path):
    original = tmp_path / 'original.md'
    original.write_text('Keep my original', encoding='utf-8')
    record = sources.create(path=str(original))
    sources.delete(record['id'])
    assert original.read_text() == 'Keep my original'
    with pytest.raises(KeyError):
        sources.get(record['id'])


def test_symlink_source_rejected(sources, tmp_path):
    real = tmp_path / 'real.md'
    real.write_text('safe', encoding='utf-8')
    link = tmp_path / 'link.md'
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip('symlink creation requires Windows developer mode or privilege')
    with pytest.raises(ValueError):
        sources.create(path=str(link))
