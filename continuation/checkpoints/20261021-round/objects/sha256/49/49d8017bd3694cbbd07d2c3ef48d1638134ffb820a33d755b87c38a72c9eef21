"""Private observed-copy readback. This never proves whole-copy equality."""
from __future__ import annotations

from copy import deepcopy
from typing import Mapping

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._g2_checkpoint_diff import Budget, Reader, canonical, compare, normalize, path_key

_AUXILIARY = {
    'property': 'no verified generic numeric PropFeature unit getter',
    'expression': 'ExpressionBase has no verified local unit getter',
    'metadata:build_status': 'existing inspectors have no verified transported complete feature build-status/problem adapter',
    'selection:geometry_revision': 'no verified geometry revision getter; model revision is not geometry revision',
}


def new_budget(path):
    normalized = normalize({'left': 'current', 'right': 'current', 'scope': {'mode': 'paths', 'paths': [path], 'properties': []}})
    return Budget(normalized['budget'])


def _auxiliary(row, interfaces):
    field, reason = row.get('field'), row.get('reason')
    if row.get('status') != 'UNSUPPORTED':
        return False
    if isinstance(field, str) and field.endswith('.unit'):
        if field.startswith('property:'):
            return reason == _AUXILIARY['property'] and 'com.comsol.model.PropFeature' in interfaces
        if field.startswith('expression:'):
            return (reason == _AUXILIARY['expression'] and 'com.comsol.model.ExpressionBase' in interfaces
                    and 'com.comsol.model.ParamBase' not in interfaces)
    if field == 'metadata:build_status':
        return reason == _AUXILIARY[field] and bool({'com.comsol.model.GeomFeature', 'com.comsol.model.MeshFeature'} & set(interfaces))
    if field == 'selection:geometry_revision':
        return reason == _AUXILIARY[field] and bool({'com.comsol.model.Selection', 'com.comsol.model.LocalSelection', 'com.comsol.model.GeomObjectSelection'} & set(interfaces))
    return False


def require_structure(projection, root):
    """Keep auxiliary gaps; refuse every other missing required observation."""
    faults = []
    descriptors = {path_key(r['path']): r['descriptor']['interfaces'] for r in projection['receiver_receipts']}
    if projection.get('unknown') or not projection['nodes']:
        faults.append({'field': 'scope', 'reason': 'unknown or empty observed source'})
    if projection['runtime_version'] != '6.4.0.293' or projection['normalized_scope'] != {'mode': 'paths', 'paths': [root], 'properties': []}:
        faults.append({'field': 'scope', 'reason': 'unexpected runtime or exact scope'})
    root_node = next((n for n in projection['nodes'] if n['path'] == root), None)
    if root_node is None or root_node['fields'].get('tag', {}).get('value', {}).get('data') != root['segments'][-1]['tag']:
        faults.append({'field': 'tag', 'reason': 'exact selected root tag missing'})
    accepted = []
    for row in projection['coverage']:
        interfaces = descriptors.get(path_key(row['path']), [])
        if row['status'] in {'VERIFIED', 'NOT_APPLICABLE'}:
            continue
        if _auxiliary(row, interfaces):
            matching = [e for e in projection['errors'] if e['path'] == row['path'] and e['field'] == row['field']
                        and e['code'] == 'API_UNSUPPORTED' and e['message'] == row['reason'] and e['cause_type'] == 'ExecutionContractError'
                        and not e['dispatched'] and not e['mutation_possible']]
            if len(matching) == 1:
                accepted.append(row)
                continue
        faults.append(row)
    for error in projection['errors']:
        if not any(row['path'] == error['path'] and row['field'] == error['field'] and row['reason'] == error['message'] for row in accepted):
            faults.append(error)
    # The primary API declares this typed child, but current Worker does not
    # transport paramCase. Empty expressions do not establish zero cases.
    for node in projection['nodes']:
        if 'com.comsol.model.StudyFeature' in node['fields'].get('public_interfaces', []):
            receipt = next((r['descriptor'] for r in projection['receiver_receipts'] if r['path'] == node['path']), {})
            if not any(m['method'] == 'feature' and m['parameters'] == [] and m['returns'] == 'com.comsol.model.StudyFeatureList'
                       for m in receipt.get('methods', [])):
                faults.append({'path': node['path'], 'field': 'collection:feature', 'reason': 'known StudyFeature typed child collection is not exposed'})
        if 'com.comsol.model.ModelParamGroup' in node['fields'].get('public_interfaces', []):
            faults.append({'path': node['path'], 'field': 'collection:paramCase', 'reason': 'known typed parameter-case child domain is not transported'})
    if faults:
        raise PreWriteRefusal('API_UNSUPPORTED', 'required observed copy structure is unavailable',
                              details={'projection': projection, 'required_failures': faults, 'auxiliary_gaps': accepted})
    return accepted


def observe(worker, model_tag, path, budget, role):
    scope = {'mode': 'paths', 'paths': [path], 'properties': []}
    projection = Reader(worker, model_tag, scope, budget, 100, role).project()
    require_structure(projection, path)
    return projection


def _mapped(projection, source_root, target_root):
    result = deepcopy(projection)
    before, after = source_root['segments'], target_root['segments']
    def mapped(path):
        segments = path['segments']
        return {'segments': deepcopy(after + segments[len(before):])} if segments[:len(before)] == before else deepcopy(path)
    for node in result['nodes']:
        is_root = node['path'] == source_root
        node['path'] = mapped(node['path'])
        if is_root:
            node['fields']['tag']['value']['data'] = after[-1]['tag']
    for key in ('coverage', 'coverage_contract', 'errors', 'receiver_receipts'):
        for row in result[key]:
            row['path'] = mapped(row['path'])
    result['normalized_scope']['paths'] = [mapped(p) for p in result['normalized_scope']['paths']]
    return result


def _obligations(projection):
    return sorted([{'path': row['path'], 'field': row['field'], 'status': row['status'], 'getter': row['getter'], 'reason': row['reason']}
                   for row in projection['coverage']], key=canonical)


def match_observed(source, target, source_root, target_root):
    left = _mapped(source, source_root, target_root)
    changes = compare(left, target)
    # compare intentionally withholds added/removed fields on incomplete views.
    # Required observed node/field sets are checked independently here.
    field_sets = lambda p: sorted((path_key(n['path']), sorted(n['fields'])) for n in p['nodes'])
    failures = []
    if changes:
        failures.append({'field': 'observed_values', 'changes': changes})
    if field_sets(left) != field_sets(target):
        failures.append({'field': 'observed_node_field_sets', 'before': field_sets(left), 'after': field_sets(target)})
    if _obligations(left) != _obligations(target):
        failures.append({'field': 'coverage_obligations', 'before': _obligations(left), 'after': _obligations(target)})
    if failures:
        raise ExecutionContractError('EXECUTION_STATE_UNKNOWN', 'observed copy subtree readback differs', details={'differences': failures})
    return {'observed_structural_status': 'OBSERVED_MATCH', 'whole_equality': 'UNVERIFIED', 'complete': False,
            'normalization': 'requested root tag and exact root path prefix only', 'matched_observed_nodes': len(source['nodes']),
            'auxiliary_gaps': deepcopy([r for r in source['coverage'] if r['status'] == 'UNSUPPORTED'])}


def require_copy_order(before, after, source_tag, target_tag):
    if source_tag not in before or target_tag in before or after.count(target_tag) != 1 or [t for t in after if t != target_tag] != before:
        raise ExecutionContractError('EXECUTION_STATE_UNKNOWN', 'copy changed original native list identity or order', details={'before': before, 'after': after})


def legacy_summary(projection, root):
    fields = next(n['fields'] for n in projection['nodes'] if n['path'] == root)
    return {'path': deepcopy(root), 'tag': fields['tag']['value']['data'], 'type_id': fields.get('type', {}).get('value', {}).get('data'),
            'values': {name[len('property:'):]: deepcopy(value) for name, value in fields.items() if name.startswith('property:')},
            'projection': projection}
