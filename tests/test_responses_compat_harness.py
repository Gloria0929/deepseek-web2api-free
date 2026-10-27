import sys


def test_real_http_harness_exercises_app_and_cleans_owned_server():
    from tests.responses_compat.server_process import running_server
    from openai import OpenAI, DefaultHttpxClient
    with running_server() as backend:
        process = backend.process
        with OpenAI(base_url=backend.url + '/v1',api_key=backend.key,max_retries=0,
                    http_client=DefaultHttpxClient(trust_env=False)) as client:
            response = client.responses.create(model='deepseek-chat',input='case:text')
            assert response.output_text == 'answer'
        state = backend.state()
        assert state['active'] == 0 and state['busy'] == 0
    assert process.poll() is not None


def test_driver_failures_and_timeouts_are_not_success():
    from scripts.run_responses_compat import run_driver
    assert not run_driver([sys.executable,'-c','raise SystemExit(3)'],timeout=3)['passed']
    assert not run_driver([sys.executable,'-c','import time; time.sleep(10)'],timeout=.1)['passed']
