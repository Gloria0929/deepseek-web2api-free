"""Real upstream assertions; failures expose only class and safe HTTP status."""
import argparse
import json
from pathlib import Path
import time

from openai import OpenAI, DefaultHttpxClient
from .server_process import ServerHandle

parser=argparse.ArgumentParser()
for arg in ('base-url','api-key','probes'): parser.add_argument('--'+arg,required=True)


def main():
    args=parser.parse_args(); probes=json.loads(Path(args.probes).read_text())
    backend=ServerHandle(args.base_url,None,args.api_key); passed=[]
    with OpenAI(base_url=args.base_url+'/v1',api_key=args.api_key,max_retries=0,
                _strict_response_validation=True,http_client=DefaultHttpxClient(trust_env=False),timeout=180) as client:
        def create(**kwargs):
            time.sleep(2)
            return client.responses.create(model='deepseek-chat',**kwargs)
        def check(name,action):
            try:
                action(); row={'name':name,'passed':True}
            except Exception as error:
                row={'name':name,'passed':False,'failure_type':type(error).__name__,'http_status':getattr(error,'status_code',None)}
            passed.append(row['passed']); print(json.dumps(row),flush=True)
        def text():
            first=create(input='Remember code CEDAR-904. Reply only ACK.')
            assert first.output_text.strip().strip('.!')=='ACK'
            second=create(input='Repeat the code only.',previous_response_id=first.id)
            assert 'CEDAR-904' in second.output_text.upper()
        check('live_text_previous',text)
        def images():
            before=backend.state()['uploads']
            first=create(input=[{'role':'user','content':[{'type':'input_text','text':'Inspect silently. Reply only ACK without describing the image.'},
                {'type':'input_image','image_url':probes['red']}]}])
            assert first.output_text.strip().strip('.!')=='ACK'
            time.sleep(2)
            with client.responses.stream(model='deepseek-chat',input='Describe the color and shape of my previous image.',previous_response_id=first.id) as stream:
                list(stream); recalled=stream.get_final_response()
            assert all(w in recalled.output_text.lower() for w in ('red','circle'))
            assert backend.state()['uploads']==before+1 and backend.state()['requests'][-1]['refs']==1
            compared=create(input=[{'role':'user','content':[{'type':'input_text','text':'Compare the first image with this new image, including color, shape and count, in chronological order.'},
                {'type':'input_image','image_url':probes['blue']}]}],previous_response_id=recalled.id)
            assert all(w in compared.output_text.lower() for w in ('red','circle','blue','square'))
            assert backend.state()['uploads']==before+2 and backend.state()['requests'][-1]['refs']==2
        check('live_hidden_image_and_append',images)
        def tools():
            definitions=[{'type':'function','name':'weather','description':'Get the weather of one city.', 'strict':True,
                'parameters':{'type':'object','properties':{'city':{'type':'string'}},'required':['city'],'additionalProperties':False}}]
            result=create(input='Call weather exactly twice: once for Paris and once for London. Do not answer directly.',
                tools=definitions,tool_choice='required',parallel_tool_calls=True)
            calls=[i for i in result.output if i.type=='function_call']
            assert len(calls)==2 and len({i.call_id for i in calls})==2
            assert {json.loads(i.arguments)['city'].lower() for i in calls}=={'paris','london'}
            assert result.tools[0].strict is True
            final=create(input=[{'type':'function_call_output','call_id':item.call_id,
                'output':json.dumps({'city':json.loads(item.arguments)['city'],'temperature_c':19+index})} for index,item in enumerate(reversed(calls))],
                previous_response_id=result.id,instructions='Report both cities and both temperatures in Celsius.')
            assert '19' in final.output_text and '20' in final.output_text
        check('live_strict_multi_function_loop',tools)
    return 0 if all(passed) else 1


if __name__ == '__main__': raise SystemExit(main())
