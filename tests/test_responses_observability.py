import json

import pytest

from turn_preparation import prepare_turn
from tests.test_image_turns import account, attachment
import server


def test_phase_preserves_business_failure_and_ignores_observer_failure():
    from execution_observer import phase
    def broken(_): raise RuntimeError('observer broke')
    with phase(broken, 'generation'): pass
    with pytest.raises(ValueError, match='business'):
        with phase(broken, 'generation'): raise ValueError('business')


def test_trace_timings_are_once_only_and_whitelisted():
    from execution_observer import Observation
    from responses_observability import RequestTrace
    ticks, rows = [1.0], []
    trace = RequestTrace('req_test',clock=lambda:ticks[0],emit=rows.append)
    ticks[0] = 1.1; trace.first('first_upstream_event'); trace.first('first_upstream_event')
    trace.observe(Observation('image_upload',20,'completed',{'image_count':1,'url':'SECRET','image_bytes':100}))
    trace.bind('resp_test'); trace.finish('completed'); trace.finish('failed','SECRET')
    assert len(rows) == 3
    assert rows[0]['elapsed_ms'] == pytest.approx(100)
    assert rows[1]['image_count'] == 1 and rows[-1]['response_id'] == 'resp_test'
    assert 'SECRET' not in json.dumps(rows)
    assert not any(row['stage'] == 'first_output_text' for row in rows)


def test_image_preparation_observes_actual_upload_and_processing():
    from execution_observer import Observation
    rows = []
    acct, _ = account(); image = attachment()
    prepared = prepare_turn(server.AcquiredAccount(acct),image.marker,(image,),observer=rows.append)
    assert prepared.ref_file_ids == ['a-red']
    assert [row.stage for row in rows] == ['session_create','image_upload','image_processing']
    assert all(isinstance(row,Observation) and row.status == 'completed' for row in rows)


def test_phase_reports_cancellation_without_swallowing():
    import asyncio
    from execution_observer import phase
    rows = []
    with pytest.raises(asyncio.CancelledError):
        with phase(rows.append, 'generation'): raise asyncio.CancelledError()
    assert rows[0].status == 'cancelled'


def test_actual_route_emits_safe_stages_and_survives_logging_failure(backend, monkeypatch):
    import responses_routes
    from fastapi.testclient import TestClient
    from responses_observability import RequestTrace
    rows = []
    monkeypatch.setattr(responses_routes, 'RequestTrace', lambda request_id: RequestTrace(request_id,emit=rows.append))
    backend.state.text = 'SECRET-ANSWER'; backend.state.reasoning = 'SECRET-THINK'
    with TestClient(server.app) as http:
        response = http.post('/v1/responses',json={'input':'SECRET-PROMPT','stream':True})
        assert response.status_code == 200 and 'response.completed' in response.text
    assert {'history_replay','account_wait','session_create','generation','storage',
            'first_upstream_event','first_output_text','request'} <= {row['stage'] for row in rows}
    assert 'SECRET' not in json.dumps(rows)
    assert sum(row['stage'] == 'request' for row in rows) == 1
    with TestClient(server.app) as http:
        assert http.post('/v1/responses',json={'input':'x','unknown':'SECRET'}).status_code == 400
    assert rows[-1]['status'] == 'failed' and rows[-1]['error_code'] == 'invalid_request_error'
    def broken(_): raise RuntimeError('SECRET')
    monkeypatch.setattr(responses_routes, 'RequestTrace', lambda request_id: RequestTrace(request_id,emit=broken))
    with TestClient(server.app) as http:
        assert http.post('/v1/responses',json={'input':'x'}).status_code == 200


from tests.test_responses_api import backend
