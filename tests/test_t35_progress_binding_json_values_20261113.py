"""Pure synthetic structural controls for progress binding equality.

Only fresh Python values are used. The cycle check is an in-process robustness
fault model: JSON persisted through OperationStore cannot encode that cycle.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any

from comsol_mcp._g3_solver_ops import _PROGRESS_BINDING_FIELDS, _same_progress_binding


def _binding(ref):
    value: dict[str, Any] = {name: f'synthetic-{name}' for name in _PROGRESS_BINDING_FIELDS}
    value['model_ref'] = ref
    value['model_revision'] = 1
    return value


def test_nested_json_identity_rejects_boolean_integer_alias():
    expected = _binding({'nested': [{'value': 1}]})
    assert _same_progress_binding(_binding({'nested': [{'value': 1}]}), expected)
    assert not _same_progress_binding(_binding({'nested': [{'value': True}]}), expected)


def test_nested_json_identity_accepts_matching_values_and_mapping_interface():
    expected = _binding({'nested': [1, {'x': [False, None, 's', 2.5]}]})
    actual = _binding(MappingProxyType({'nested': [1, {'x': [False, None, 's', 2.5]}]}))
    assert _same_progress_binding(actual, expected)


def test_nested_json_identity_rejects_list_tuple_alias():
    expected = _binding({'nested': [1, 2]})
    assert not _same_progress_binding(_binding({'nested': (1, 2)}), expected)


def test_repeated_acyclic_subvalue_is_not_treated_as_cycle():
    shared = [1, {'x': 'y'}]
    expected = _binding({'a': shared, 'b': shared})
    actual = _binding({'a': shared, 'b': shared})
    assert _same_progress_binding(actual, expected)


def test_identical_cyclic_python_values_do_not_certify():
    shared = []
    shared.append(shared)
    expected = _binding({'cycle': shared})
    actual = _binding({'cycle': shared})
    assert not _same_progress_binding(actual, expected)
