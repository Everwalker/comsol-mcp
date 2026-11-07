"""Node type is a content dimension, never a product alias."""
import pytest
from comsol_mcp._g2_docs import OfflineDocsIndex, infer_version
from comsol_mcp._managed_backend import ManagedBackend


def test_install_dir_markers_select_the_release():
    """COMSOL63/COMSOL64 install names resolve to 6.3/6.4 releases."""
    assert infer_version('COMSOL64') == '6.4'
    assert infer_version('/Applications/COMSOL63/Multiphysics/doc/help') == '6.3'
    assert infer_version('C:\\Program Files\\COMSOL\\COMSOL64\\Multiphysics\\doc') == '6.4'
    # A dotted release keeps priority over the fallback alias.
    assert infer_version('COMSOL 6.4', '6.3') == '6.4'
    # The marker needs the COMSOL prefix and an exact two-digit release.
    assert infer_version('COMSOL640') == 'unknown'
    assert infer_version('COMSOL640', '6.3') == '6.3'
    assert infer_version('reconstruct64', '6.4') == '6.4'


def test_install_dir_layout_keeps_63_and_64_rows_apart(tmp_path):
    """Indexing both install trees under one alias must not merge versions."""
    for marker, body in (('COMSOL64', 'ModelEntity current'),
                         ('COMSOL63', 'ModelEntity legacy')):
        tree = tmp_path / 'Applications' / marker / 'Multiphysics' / 'doc'
        tree.mkdir(parents=True)
        (tree / 'model.txt').write_text(body)
    index = OfflineDocsIndex(tmp_path / 'docs.sqlite', allowed_roots=[tmp_path])
    try:
        out = index.index(runtime_id='COMSOL64', sources=[str(tmp_path / 'Applications')])
        assert out['version'] == '6.4'
        current = index.search(query='current', version='6.4')
        legacy = index.search(query='legacy', version='6.3')
        assert current['status'] == 'SUCCEEDED' and current['results'][0]['snippet'] == 'ModelEntity current'
        assert legacy['status'] == 'SUCCEEDED' and legacy['results'][0]['snippet'] == 'ModelEntity legacy'
        # Each release slice is available but holds no document for the other
        # tree's text, so cross-release queries are NOT_FOUND, never a merge.
        assert index.search(query='current', version='6.3')['status'] == 'NOT_FOUND'
        assert index.search(query='legacy', version='6.4')['status'] == 'NOT_FOUND'
    finally:
        index.close()


@pytest.fixture
def corpus(tmp_path):
    files = []
    for version, text in [('6.4', 'PropFeature\nIllegal value example tutorial'), ('6.4', 'MeshFeature\nmesh failed'), ('6.3', 'PropFeature\nlegacy error')]:
        path = tmp_path / f'{version}-{len(files)}.txt'
        path.write_text(text)
        files.append(str(path))
    return files


def test_node_type_filter_and_product_version_isolation(tmp_path, corpus):
    index = OfflineDocsIndex(tmp_path / 'docs.sqlite')
    try:
        index.index(runtime_id='6.4', sources=corpus)
        assert index.search(query='Illegal', version='6.4', product='PropFeature')['status'] == 'UNAVAILABLE'
        found = index.search(query='Illegal', version='6.4', node_type='propfeature')
        assert found['status'] == 'SUCCEEDED'
        assert len(found['results']) == 1
        assert found['results'][0]['product'] == 'COMSOL'
        # The node_type filter counts toward availability: 6.4 has no document
        # for that node type, so the filter slice is UNAVAILABLE.
        assert index.search(query='Illegal', version='6.4', node_type='absent')['status'] == 'UNAVAILABLE'
        # Version isolation: 6.3 has a PropFeature document but not the query
        # term, so its corpus is available and the query matches nothing.
        assert index.search(query='Illegal', version='6.3', node_type='PropFeature')['status'] == 'NOT_FOUND'
        assert index.search(query='Illegal', version='6.4', product='COMSOL')['status'] == 'SUCCEEDED'
        assert index.search(query='Illegal', version='6.2', node_type='PropFeature')['status'] == 'UNAVAILABLE'
    finally:
        index.close()


def test_node_type_is_matched_against_title_and_content_not_the_product(tmp_path):
    """The node type is a content dimension; it is never aliased onto product."""
    body = tmp_path / '6.4'
    body.mkdir()
    (body / 'a.txt').write_text('DomainFeature\nPropFeature material selection helpers')
    (body / 'b.txt').write_text('Ordinary title\nmentions PropFeature inside the body text')
    (body / 'c.txt').write_text('NoMatchFeature\nPropFeature unrelated content')
    index = OfflineDocsIndex(tmp_path / 'docs.sqlite')
    try:
        index.index(runtime_id='6.4', sources=[str(body)])
        # A title match and a body-only match both count, case-insensitively.
        titled = index.search(query='PropFeature', version='6.4', node_type='domaiNfEature')
        assert titled['status'] == 'SUCCEEDED'
        assert [row['title'] for row in titled['results']] == ['DomainFeature']
        in_body = index.search(query='PropFeature', version='6.4', node_type='ordinary TITLE')
        assert in_body['status'] == 'SUCCEEDED'
        assert [row['title'] for row in in_body['results']] == ['Ordinary title']
        # Availability counts the node_type filter: a node type this version has
        # no document for is UNAVAILABLE, while an unindexed version is too.
        assert index.search(query='PropFeature', version='6.4', node_type='absentfeature')['status'] == 'UNAVAILABLE'
        assert index.search(query='PropFeature', version='6.2', node_type='DomainFeature')['status'] == 'UNAVAILABLE'
        # A query term that matches nothing is NOT_FOUND, not a silent success.
        assert index.search(query='zzzz-nothing', version='6.4', node_type='DomainFeature')['status'] == 'NOT_FOUND'
        # product and node_type are independent filters and both must hold.
        both = index.search(query='PropFeature', version='6.4', product='COMSOL', node_type='NoMatchFeature')
        assert both['status'] == 'SUCCEEDED' and [row['title'] for row in both['results']] == ['NoMatchFeature']
        assert index.search(query='PropFeature', version='6.4', product='Heat Transfer Module',
                            node_type='DomainFeature')['status'] == 'UNAVAILABLE'
        # A node type name is never a product name.
        assert index.search(query='PropFeature', version='6.4', product='DomainFeature')['status'] == 'UNAVAILABLE'
    finally:
        index.close()


def test_multi_token_query_publishes_only_documents_holding_every_term(tmp_path):
    """One common word must not turn an unrelated page into an error hit."""
    body = tmp_path / 'docs'
    body.mkdir()
    (body / 'err.txt').write_text(
        'ModelUtil\nThrows: java.io.IOException - if the file cannot be read.')
    (body / 'intro.txt').write_text(
        'Introduction\nThis file describes how to get started; read it first.')
    index = OfflineDocsIndex(tmp_path / 'docs.sqlite', allowed_roots=[tmp_path])
    try:
        index.index(runtime_id='COMSOL64', sources=[str(body)])
        # The intro page shares 'file', 'be' and 'read' but not 'cannot', so
        # the full-phrase query excludes it under AND semantics.
        hit = index.search(query='file cannot be read', version='6.4')
        assert hit['status'] == 'SUCCEEDED'
        assert [row['title'] for row in hit['results']] == ['ModelUtil']
        assert 'file cannot be read' in hit['results'][0]['snippet'].lower()
        # A shorter shared query legitimately returns both pages.
        partial = index.search(query='read file', version='6.4')
        assert partial['status'] == 'SUCCEEDED'
        assert {row['title'] for row in partial['results']} == {'ModelUtil', 'Introduction'}
        # A term absent from the whole slice is NOT_FOUND, never a
        # partial-score success.
        assert index.search(query='file cannot be parsed', version='6.4')['status'] == 'NOT_FOUND'
    finally:
        index.close()


def test_backend_error_search_uses_node_type(tmp_path, corpus, monkeypatch):
    roots = lambda *_args: [tmp_path]
    monkeypatch.setattr('comsol_mcp._platform_paths.default_comsol_help_roots', roots)
    monkeypatch.setattr('comsol_mcp._managed_backend.default_comsol_help_roots', roots)
    backend = ManagedBackend(tmp_path, object(), registry={})
    try:
        backend.docs_index.index(runtime_id='6.4', sources=corpus)
        result = backend._invoke_g2_control('docs.error_search', {'error': 'Illegal', 'version': '6.4', 'node_type': 'PropFeature'}, {}, 'op-1')
        assert result['data']['status'] == 'SUCCEEDED'
        result = backend._invoke_g2_control('docs.examples', {'query': 'Illegal', 'version': '6.4'}, {}, 'op-2')
        assert result['data']['status'] == 'SUCCEEDED'
    finally:
        backend.docs_index.close()


def test_backend_docs_operations_forward_the_documented_dimensions(tmp_path, monkeypatch):
    """search->product, error_search->node_type, examples->neither."""
    monkeypatch.setattr('comsol_mcp._managed_backend.default_comsol_help_roots', lambda *_args: [tmp_path])
    backend = ManagedBackend(tmp_path, object(), registry={})
    captured = {}

    def search(**kwargs):
        captured.update(kwargs)
        return {'status': 'SUCCEEDED', 'results': []}

    monkeypatch.setattr(backend.docs_index, 'search', search)
    backend._invoke_g2_control('docs.search', {'query': 'Illegal', 'version': '6.4', 'product': 'COMSOL'}, {}, 'op-1')
    assert captured['product'] == 'COMSOL' and captured.get('node_type') is None
    backend._invoke_g2_control('docs.error_search', {'error': 'Illegal', 'version': '6.4', 'node_type': 'PropFeature'}, {}, 'op-2')
    assert captured['node_type'] == 'PropFeature' and captured.get('product') is None
    backend._invoke_g2_control('docs.examples', {'query': 'Illegal', 'version': '6.4'}, {}, 'op-3')
    assert captured.get('node_type') is None and captured.get('product') is None
    assert captured['query'] == 'Illegal example tutorial'


def test_declared_docs_schemas_keep_product_and_node_type_apart():
    """The catalog is the contract: search->product, error_search->node_type, examples->neither."""
    from comsol_mcp._g2_registry import validate_call

    identity = {'project_id': 'proj'}
    validate_call('docs.search', {**identity, 'query': 'x', 'version': '6.4', 'product': 'COMSOL'})
    with pytest.raises(Exception) as exc:
        validate_call('docs.search', {**identity, 'query': 'x', 'version': '6.4', 'node_type': 'PropFeature'})
    assert exc.value.code == 'INVALID_REQUEST'
    validate_call('docs.error_search', {**identity, 'error': 'x', 'version': '6.4', 'node_type': 'PropFeature'})
    with pytest.raises(Exception) as exc:
        validate_call('docs.error_search', {**identity, 'error': 'x', 'version': '6.4', 'product': 'COMSOL'})
    assert exc.value.code == 'INVALID_REQUEST'
    validate_call('docs.examples', {**identity, 'query': 'x', 'version': '6.4'})
    for extra in ('node_type', 'product'):
        with pytest.raises(Exception) as exc:
            validate_call('docs.examples', {**identity, 'query': 'x', 'version': '6.4', extra: 'PropFeature'})
        assert exc.value.code == 'INVALID_REQUEST'
