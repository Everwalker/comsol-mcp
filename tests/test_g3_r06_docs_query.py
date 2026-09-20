"""Node type is a content dimension, never a product alias."""
import pytest
from comsol_mcp._g2_docs import OfflineDocsIndex
from comsol_mcp._managed_backend import ManagedBackend


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
