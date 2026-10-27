from copy import deepcopy
import json

import pytest

from responses_errors import ResponseError
from responses_format import ResponsesRequest, normalize_input


def tool(schema=None, **kwargs):
    return {'type':'function','name':'f','parameters':schema if schema is not None else {
        'type':'object','properties':{'city':{'type':'string'}}}, **kwargs}


def call(index=0, **kwargs):
    return {'id':f'call-{index}', 'function':{'name':'f','arguments':json.dumps(kwargs)}}


def test_normalization_is_effective_and_does_not_mutate_input():
    source = [tool({'type':'object','properties':{'value':{'anyOf':[
        {'type':'object','properties':{'x':{'type':'integer'}}}, {'type':'null'}]}}})]
    original = deepcopy(source)
    req = ResponsesRequest(input='x', tools=source)
    assert source == original
    schema = req.tools[0]['parameters']
    assert req.tools[0]['strict'] is True
    assert schema['required'] == ['value'] and schema['additionalProperties'] is False
    nested = schema['properties']['value']['anyOf'][0]
    assert nested['required'] == ['x'] and nested['additionalProperties'] is False


def test_explicit_strict_rejects_non_closed_schema_and_null_default_is_normalized():
    with pytest.raises(ResponseError) as caught: ResponsesRequest(input='x', tools=[tool(strict=True)])
    assert caught.value.param == 'tools[0].parameters'
    req = ResponsesRequest(input='x', tools=[{'type':'function','name':'f','parameters':None,'strict':None}])
    assert req.tools[0]['strict'] is True
    assert req.tools[0]['parameters'] == {'type':'object','properties':{},'required':[],'additionalProperties':False}


def test_valid_unsupported_strict_construct_falls_back_and_empty_schema_survives():
    schema = {'type':'object','patternProperties':{'^x':{'type':'string'}}}
    req = ResponsesRequest(input='x', tools=[tool(schema)])
    assert req.tools[0]['strict'] is False and req.tools[0]['parameters'] == schema
    with pytest.raises(ResponseError): ResponsesRequest(input='x', tools=[tool(schema, strict=True)])
    assert ResponsesRequest(input='x', tools=[tool({})]).tools[0]['parameters'] == {}


def test_local_refs_and_annotation_values_are_not_confused_with_schema():
    from responses_tools import validate_call_batch
    schema = {'type':'object','properties':{'city':{'$ref':'#/$defs/a~1b'}},
        '$defs':{'a/b':{'type':'string'}}, 'default':{'$ref':'https://example.invalid/annotation'}}
    req = ResponsesRequest(input='x', tools=[tool(schema)])
    validate_call_batch(req.tools, 'auto', [call(city='Paris')], parallel=False)
    with pytest.raises(ResponseError): validate_call_batch(req.tools, 'auto', [call(city=5)], parallel=False)


@pytest.mark.parametrize('arguments', ['{"city":NaN}', '{"city":Infinity}', 'not JSON'])
def test_non_strict_still_rejects_invalid_json(arguments):
    from responses_tools import validate_call_batch
    with pytest.raises(ResponseError):
        validate_call_batch([tool(strict=False)], 'auto', [{'id':'x','function':{'name':'f','arguments':arguments}}], parallel=False)


def test_call_batch_limits_choice_and_unique_ids():
    from responses_tools import validate_call_batch
    tools = ResponsesRequest(input='x', tools=[tool()]).tools
    validate_call_batch(tools, 'required', [call(i, city='Paris') for i in range(64)], parallel=True)
    for calls, parallel, choice in [([call(), call(1)],False,'auto'),
        ([call(i) for i in range(65)],True,'auto'), ([call(),call()],True,'auto'),
        ([],True,'required'), ([call()],True,'none'),
        ([{'id':[], 'function':{'name':'f','arguments':'{}'}}],True,'auto')]:
        with pytest.raises(ResponseError): validate_call_batch(tools, choice, calls, parallel=parallel)


def test_pending_result_batch_requires_all_outputs_but_accepts_reverse_order():
    history = [{'role':'assistant','content':'','tool_calls':[call(0),call(1)]}]
    with pytest.raises(ResponseError): normalize_input(ResponsesRequest(input='continue'), history)
    for items in [[{'type':'function_call_output','call_id':'call-0','output':'x'}],
        [{'type':'function_call_output','call_id':'call-0','output':'x'}]*2]:
        with pytest.raises(ResponseError) as caught: normalize_input(ResponsesRequest(input=items), history)
        assert caught.value.param.startswith('input')
    messages, _ = normalize_input(ResponsesRequest(input=[
        {'type':'function_call_output','call_id':'call-1','output':'second'},
        {'type':'function_call_output','call_id':'call-0','output':'first'}]), history)
    assert [m['tool_call_id'] for m in messages] == ['call-1','call-0']
