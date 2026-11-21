"""T35 historical 128k threshold: drain all pages via actual daemon routing.

Only synthetic in-memory jobs, fake authority, and an inert ControlDaemon
receiver are used. Events are bulk-seeded with SQL, not through a real
producer. This is not real principal/MCP wire/Worker/COMSOL/WAL/disk or RSS
proof. The page byte budget is proposed engineering admission, not a
published contract. No protected author tests or old resources are accessed.
"""
from __future__ import annotations

import json
import tracemalloc

from comsol_mcp._operation_store import OperationStore
from tests import test_t35_actual_128k_store_boundary_20261113 as store_fixture
from tests import test_t35_joblog_canonical_routes_20261113 as route_fixture

PAGE_LIMIT = 500  # Maximum in the actual canonical job.log input schema.


def test_128001_events_then_terminal_all_pages_via_canonical_joblog():
    store = OperationStore(':memory:')
    try:
        row, reused = store.begin(
            request_id='t35-fullpage-request', idempotency_key='t35-fullpage-key',
            request_hash='t35-fullpage-hash', operation='synthetic.threshold',
            metadata={'execution': {'project_id': route_fixture.PROJECT_A}},
        )
        assert not reused
        job_id = row['job_id']
        count = store_fixture.OLD_EVENT_LIMIT + 1
        store_fixture._seed_events(store, job_id, count)
        assert store.finish(row['operation_id'], status='SUCCEEDED',
                            result={'success': True, 'marker': 'synthetic-fullpage'}) == (True, 'SUCCEEDED')
        store.add_event(job_id, 'SyntheticTerminal', {'status': 'SUCCEEDED'})
        assert store.db.execute('SELECT count(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0] == count + 1
        timeline: list[tuple] = []
        route_fixture._spy(store, timeline)
        receiver = route_fixture._Receiver(store, timeline)
        next_offset, previous_id, full_pages, requests = 0, 0, 0, 0
        expected_nonempty_pages = (count + 1 + PAGE_LIMIT - 1) // PAGE_LIMIT
        peak_wire_bytes = 0
        saw_terminal = False
        tracemalloc.start()
        try:
            while True:
                response = route_fixture._route(receiver, 'job.log',
                    {'job_id': job_id, 'limit': PAGE_LIMIT, 'cursor': str(next_offset)},
                    route_fixture.PROJECT_A)
                requests += 1
                assert response['success'] is True, response
                data = response['data']
                page = data['events']
                assert len(page) <= PAGE_LIMIT
                peak_wire_bytes = max(peak_wire_bytes, len(json.dumps(response, allow_nan=False).encode('utf-8')))
                assert peak_wire_bytes <= 4 * 1024 * 1024
                assert data['next_offset'] == next_offset + len(page)
                assert data['next_cursor'] == str(data['next_offset'])
                for event in page:
                    assert event['id'] > previous_id and event['job_id'] == job_id
                    previous_id = event['id']
                    if next_offset < count:
                        assert event['event'] == 'SyntheticLine'
                        assert event['metadata'] == {'index': next_offset}
                    else:
                        assert next_offset == count
                        assert event['event'] == 'SyntheticTerminal'
                        assert event['metadata'] == {'status': 'SUCCEEDED'}
                        saw_terminal = True
                    next_offset += 1
                if not page:
                    break
                full_pages += 1
                assert requests <= expected_nonempty_pages + 1
            _, peak_python = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert next_offset == count + 1 and saw_terminal
        assert full_pages == expected_nonempty_pages and requests == expected_nonempty_pages + 1
        assert len(timeline) == requests * 3
        assert all(timeline[index:index + 2] == [('authority', route_fixture.PROJECT_A, 'inspect'),
                                                ('snapshot', route_fixture.PROJECT_A, True)]
                   for index in range(0, len(timeline), 3))
        print(f'T35_FULLPAGE_128K events={next_offset} nonempty_pages={full_pages} requests={requests} '
              f'peak_json_bytes={peak_wire_bytes} traced_python_peak={peak_python}')
    finally:
        store.close()
