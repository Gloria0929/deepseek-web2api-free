import pytest
from pydantic import ValidationError
from openai.types.responses import Response

from responses_format import ResponsesRequest, normalize_input, validate_calls, response_object, output_items
from responses_errors import ResponseError
from chat_types import ChatResult
from tests.test_image_inputs import data_url


def test_input_items_images_and_function_results():
    req = ResponsesRequest(input=[{'role': 'user', 'content': [
        {'type': 'input_text', 'text': 'look'}, {'type': 'input_image', 'image_url': data_url()}]},
        {'type': 'function_call', 'call_id': 'call-a', 'name': 'weather', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'call-a', 'output': 'sunny'}])
    messages, attachments = normalize_input(req, [])
    assert messages[0]['_image_digests'] == [attachments[0].asset.digest]
    assert messages[-1]['tool_call_id'] == 'call-a'
    assert messages[-1]['content'] == 'sunny'


@pytest.mark.parametrize('kwargs', [dict(background=True), dict(parallel_tool_calls=True),
    dict(max_output_tokens=20), dict(reasoning={'effort':'high'}), dict(text={'format':{'type':'json_schema'}}),
    dict(tools=[{'type':'web_search'}]), dict(tools=[{'type':'function','name':'x','parameters':{'$ref':'https://private/schema'}}])])
def test_unsupported_guarantees_are_explicit(kwargs):
    with pytest.raises((ResponseError, ValidationError)):
        ResponsesRequest(input='hi', **kwargs)


def test_function_call_output_must_match_pending_call():
    req = ResponsesRequest(input=[{'type':'function_call_output','call_id':'missing','output':'done'}])
    with pytest.raises(ResponseError): normalize_input(req, [])


def test_strict_function_result_validation_and_sdk_output_text():
    req = ResponsesRequest(input='hi', tools=[{'type':'function','name':'weather','strict':True,
        'parameters':{'type':'object','properties':{'city':{'type':'string'}},'required':['city'],'additionalProperties':False}}])
    bad = [{'id':'call-a','function':{'name':'weather','arguments':'{"city":1}'}}]
    with pytest.raises(ResponseError): validate_calls(req, bad)
    result = ChatResult('answer', 'thinking')
    obj = response_object(req, 'resp_test', 'deepseek-chat', output_items(result), 'prompt')
    parsed = Response.model_validate(obj)
    assert parsed.output_text == 'answer'
    assert parsed.output[0].type == 'reasoning'


def test_manual_output_item_replay_keeps_tool_call_id():
    req = ResponsesRequest(input=[{'type':'function_call','id':'fc_item', 'call_id':'call_actual',
        'name':'weather','arguments':'{}'}, {'type':'function_call_output','call_id':'call_actual','output':'ok'}])
    messages, _ = normalize_input(req, [])
    assert messages[0]['tool_calls'][0]['id'] == 'call_actual'
    assert messages[1]['tool_call_id'] == 'call_actual'


@pytest.mark.parametrize('tool', [
    {'type':'function','name':5}, {'type':'function','name':'x','strict':'yes'},
    {'type':'function','name':'x','description':{}}, {'type':'function','name':'x','parameters':False}])
def test_invalid_nested_tool_types_are_client_errors(tool):
    with pytest.raises(ResponseError): ResponsesRequest(input='x', tools=[tool])


@pytest.mark.parametrize('name', [[], {}])
def test_nested_tool_choice_name_must_be_a_string(name):
    with pytest.raises(ResponseError):
        ResponsesRequest(input='x', tools=[{'type':'function','name':'f'}],
                         tool_choice={'type':'function','name':name})


def test_dangling_local_schema_reference_is_rejected_before_execution():
    with pytest.raises(ResponseError):
        ResponsesRequest(input='x', tools=[{'type':'function','name':'f',
                         'parameters':{'$ref':'#/$defs/missing'}}])
    valid = ResponsesRequest(input='x', tools=[{'type':'function','name':'f', 'parameters':{
        'type':'object', '$defs':{'value':{'type':'string'}},
        'properties':{'city':{'$ref':'#/$defs/value'}}}}])
    validate_calls(valid, [{'id':'call-valid','function':{'name':'f','arguments':'{"city":"Paris"}'}}])
