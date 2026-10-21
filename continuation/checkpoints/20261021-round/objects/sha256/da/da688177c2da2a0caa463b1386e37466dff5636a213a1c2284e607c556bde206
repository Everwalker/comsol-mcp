"""Actual public daemon/SQLite/Interp adapter; SDK result tree is software only."""
import time
from tests.test_g3_results import build_tree, FInterp, _real_public_points_daemon
from tools.t044_thermal_benchmark import point_arguments, verify_points
from tools.run_native_w23_saved_mph_diagnostic import science, _response_body
from tools.t044_thermal_benchmark import STUDY


def test_fixed_thermal_points_reach_actual_public_adapter(tmp_path, monkeypatch):
    tree = build_tree(sdim=1, length_unit='m', solver_parameter_units={},
                      solver_size=(100, 1), solver_size_multi=(1, 1),
                      dataset_props={'solution': 'sol1', 'comp': 'comp1', 'geom': 'geom1'})
    tree.solver.solution_info.getSolnum = lambda outer, strict: [1]
    for method in ('getPNames', 'getPvals', 'getUnits'):
        setattr(tree.solver.solution_info, method, lambda pairs: [[] for _ in pairs])
    # Actual production observation registration reads these Study SDK methods.
    # The seam supplies deterministic software facts, not a native witness.
    tree.studies.items['std1'].getLastComputationDate = lambda: 123
    tree.studies.items['std1'].getLastComputationVersion = lambda: '6.4.0.293'
    nodes = []
    def factory(tag, kind):
        node = FInterp(tag, kind, complex_=False, props={'data': None, 'expr': [], 'unit': [], 'coord': None})
        nodes.append(node)
        return node
    tree.numerical.factory = factory
    def coordinates(self, matrix):
        self._guard('setInterpolationCoordinates', (matrix,))
        self.coords = matrix
        self.values = [[[300 + 100*x/.01 for x in matrix[0]]], [[-100000.0 for _ in matrix[0]]]]
    monkeypatch.setattr(FInterp, 'setInterpolationCoordinates', coordinates, raising=False)
    monkeypatch.setattr(FInterp, 'getCoordinates', lambda self: self.coords, raising=False)
    daemon, ref, project = _real_public_points_daemon(tmp_path, monkeypatch, tree)
    daemon.backend.worker.project_root = tmp_path/'public-project-root'/'points'
    request = {'operation': 'operation_call', 'arguments': {'operation_id': 'result.at_points',
               'arguments': point_arguments('dset1')}, 'execution': {'project_id': project,
               'session_id': ref['session_id'], 'model_ref': ref, 'expected_revision': 0,
               'request_id': 'thermal-points', 'idempotency_key': 'thermal-points',
               'rpc_timeout_s': 30, 'queue_timeout_s': 30, 'execution_timeout_s': None}}
    response = daemon.dispatch(request)
    if response.get('execution', {}).get('job_id'):
        response = science._wait_job(daemon, response, deadline_epoch=time.time()+20,
                                    cleanup_reserve_s=1, label='thermal-points', evidence=tmp_path)
    assert response['success'] is True, response
    data = _response_body(response)
    verified = verify_points(data, dataset='dset1', solution='sol1')
    assert verified['flux_relative_max_error'] == 0
    assert nodes and 'getData' in nodes[0].methods()
    assert not tree.numerical.items
    stored = daemon.store.operation_job(response['execution']['operation_id'])
    assert stored['status'] == 'SUCCEEDED'


def test_fixed_stationary_study_runs_through_public_execution_service(tmp_path, monkeypatch):
    tree = build_tree(sdim=1, length_unit='m')
    daemon, ref, project = _real_public_points_daemon(tmp_path, monkeypatch, tree)
    study = tree.studies.items['std1']; calls = []
    client = daemon.backend.worker.client()
    client.set_progress_log = lambda path: {'active': path is not None, 'disabled': path is None}
    state = {'date': 0}
    def run():
        calls.append('run'); state['date'] = 123
    study.run = run
    study.getLastComputationDate = lambda: state['date']
    study.getLastComputationTime = lambda: 1
    study.getLastComputationVersion = lambda: '6.4.0.293'
    request = {'operation': 'operation_call', 'arguments': {'operation_id': 'study.run',
               'arguments': {'study': STUDY, 'timeout_s': 180}}, 'execution': {'project_id': project,
               'session_id': ref['session_id'], 'model_ref': ref, 'expected_revision': 0,
               'request_id': 'thermal-study', 'idempotency_key': 'thermal-study',
               'rpc_timeout_s': 30, 'queue_timeout_s': 30, 'execution_timeout_s': None}}
    response = daemon.dispatch(request)
    if response.get('execution', {}).get('job_id'):
        response = science._wait_job(daemon, response, deadline_epoch=time.time()+20,
                                    cleanup_reserve_s=1, label='thermal-study', evidence=tmp_path)
    assert response['success'] is True, response
    assert calls == ['run']
    assert _response_body(response)['computation_timestamp_changed'] is True
    assert daemon.store.operation_job(response['execution']['operation_id'])['status'] == 'SUCCEEDED'
