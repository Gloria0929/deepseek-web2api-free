import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai.types.responses import Response

import server
from adapter import RateLimitError
from image_inputs import ImageError
from responses_errors import ResponseError
from responses_format import ResponsesRequest
from responses_stream import ResponseStream
from tests.test_responses_api import backend


def test_classification_is_safe_and_consistent():
    from responses_errors import classify_error
    for error, status, code in [
        (HTTPException(401, 'SECRET'), 401, 'invalid_prompt'),
        (HTTPException(503, 'SECRET'), 503, 'server_error'),
        (RateLimitError('SECRET'), 429, 'rate_limit_exceeded'),
        (RuntimeError('SECRET'), 502, 'server_error'),
        (ImageError('SECRET', 504), 504, 'image_parse_error'),
        (ResponseError('Context unavailable', 409, 'response_context_unavailable'), 409, 'invalid_prompt'),
    ]:
        classified = classify_error(error)
        assert classified.status == status
        wire = ResponseStream(ResponsesRequest(input='hello'), 'resp_test', 'deepseek-chat', 'p', 1)
        frame = wire.fail(classified)
        event = json.loads(frame.split('data: ')[1])
        response = Response.model_validate(event['response'])
        assert response.error.code == code
        assert 'SECRET' not in json.dumps(classified.payload()) + frame


@pytest.mark.parametrize('field,value', [('input',None), ('stream',None), ('store',None),
    ('stream','false'), ('store',0), ('stream',1)])
def test_invalid_request_fields_report_parameter_without_input(backend, field, value):
    with TestClient(server.app) as http:
        response = http.post('/v1/responses', json={'input':'SECRET', field:value})
    assert response.status_code == 400
    assert response.json()['error']['param'].startswith(field)
    assert 'SECRET' not in response.text and not backend.calls


@pytest.mark.parametrize('field,expected', [('metadata',{}), ('tools',[]), ('include',[]),
    ('tool_choice','auto'), ('parallel_tool_calls',False), ('background',False), ('truncation','disabled')])
def test_nullable_options_use_effective_defaults(field, expected):
    req = ResponsesRequest.model_validate({'input':'x', field:None})
    assert getattr(req, field) == expected


def test_unknown_field_has_safe_parameter(backend):
    with TestClient(server.app) as http:
        response = http.post('/v1/responses', json={'input':'hello','unknown_option':'SECRET'})
    assert response.status_code == 400
    assert response.json()['error']['param'] == 'unknown_option'
    assert 'SECRET' not in response.text
