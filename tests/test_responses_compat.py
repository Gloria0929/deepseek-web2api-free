import json

import pytest
from fastapi.testclient import TestClient

import server
from responses_errors import ResponseError
from responses_format import ResponsesRequest, normalize_input
from tests.test_responses_api import backend


def codex_options():
    return {'prompt_cache_key':'CACHE-SECRET','client_metadata':{'session_id':'CLIENT-SECRET',
        'x-codex-turn-metadata':'{"turn_id":"SYNTHETIC"}'},
        'include':['reasoning.encrypted_content'],'reasoning':{'summary':'auto'}}


@pytest.mark.parametrize('stream',[False,True])
def test_codex_options_reach_chat_without_entering_prompt_or_snapshot(backend,stream,caplog):
    with TestClient(server.app) as http:
        response=http.post('/v1/responses',json={'input':'hello','stream':stream,**codex_options()})
    assert response.status_code==200
    if stream:
        frames=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert frames[-1]['type']=='response.completed'
        result=frames[-1]['response']
    else: result=response.json()
    assert backend.calls and result['status']=='completed'
    assert 'CACHE-SECRET' not in backend.calls[0][1] and 'CLIENT-SECRET' not in backend.calls[0][1]
    serialized=json.dumps(server.RESPONSE_STORE.get('ip:testclient',result['id']))
    assert 'CACHE-SECRET' not in serialized and 'CLIENT-SECRET' not in serialized
    assert all(not item.get('encrypted_content') for item in result['output'])
    assert result['reasoning'] is None
    assert 'CACHE-SECRET' not in caplog.text and 'CLIENT-SECRET' not in caplog.text


@pytest.mark.parametrize('effort',['none','minimal','low','medium','high','xhigh','max','ultra'])
def test_recognized_reasoning_hints_do_not_claim_native_effort(effort):
    req=ResponsesRequest(input='hello',reasoning={'effort':effort,'summary':'auto'},
        text={'format':{'type':'text'},'verbosity':'low'})
    assert req.reasoning is None and req.text=={'format':{'type':'text'}}


@pytest.mark.parametrize('fields,param',[
    ({'prompt_cache_key':123},'prompt_cache_key'),
    ({'prompt_cache_key':'x'*513},'prompt_cache_key'),
    ({'client_metadata':[]},'client_metadata'),
    ({'client_metadata':{'x':'x'*17000}},'client_metadata'),
    ({'client_metadata':{'x':float('nan')}},'client_metadata'),
    ({'include':['web_search_call.action.sources']},'include'),
    ({'reasoning':{'effort':'unrecognized'}},'reasoning.effort'),
    ({'reasoning':{'summary':'invented'}},'reasoning.summary'),
    ({'reasoning':{'encrypted_content':'opaque'}},'reasoning.encrypted_content'),
    ({'text':{'verbosity':'impossible'}},'text.verbosity'),
])
def test_incompatible_or_malformed_options_have_specific_parameter(fields,param):
    with pytest.raises(ResponseError) as caught: ResponsesRequest(input='hello',**fields)
    assert caught.value.status==400 and caught.value.param==param


def test_options_do_not_change_caller_scope_or_functional_guards(backend):
    with TestClient(server.app) as http:
        first=http.post('/v1/responses',headers={'Authorization':'Bearer first'},json={'input':'hello',**codex_options()}).json()
        foreign=http.post('/v1/responses',headers={'Authorization':'Bearer second'},
            json={'input':'followup','previous_response_id':first['id'],**codex_options()})
        assert foreign.status_code==404
        for fields in ({'max_output_tokens':20},{'background':True},{'unknown_option':True}):
            assert http.post('/v1/responses',json={'input':'hello',**codex_options(),**fields}).status_code==400


def test_empty_encrypted_reasoning_replay_is_accepted_but_opaque_state_is_rejected():
    req=ResponsesRequest(input=[{'type':'reasoning','id':'rs_test','summary':[], 'encrypted_content':None},
        {'role':'user','content':'hello'}],store=False,**codex_options())
    messages,_=normalize_input(req,[])
    assert messages[-1]['content']=='hello'
    req.input[0]['encrypted_content']='foreign-opaque-content'
    with pytest.raises(ResponseError):normalize_input(req,[])


def test_hint_normalization_preserves_original_payload_and_nullable_defaults():
    from copy import deepcopy
    original={'input':'hello',**codex_options(),'text':{'verbosity':'high'}}
    before=deepcopy(original)
    req=ResponsesRequest.model_validate(original)
    assert original==before and req.text=={}
    nullable=ResponsesRequest(input='hello',prompt_cache_key=None,client_metadata=None,
        reasoning=None,include=None,text={'verbosity':None})
    assert nullable.include==[] and nullable.reasoning is None and nullable.text=={}
