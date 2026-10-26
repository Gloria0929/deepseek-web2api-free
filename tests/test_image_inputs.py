import base64
from io import BytesIO

import pytest
from PIL import Image

from image_inputs import ImageError, ImageLimits, normalize_images
from image_sources import public_target, load_image


def picture(color='red', format='PNG'):
    buf = BytesIO()
    Image.new('RGB', (24, 16), color).save(buf, format=format)
    return buf.getvalue()


def data_url(color='red'):
    return 'data:image/png;base64,' + base64.b64encode(picture(color)).decode()


def test_openai_images_keep_order_and_change_history_identity():
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'look'},
                 {'type': 'image_url', 'image_url': {'url': data_url()}}]}]
    normalized, images = normalize_images(messages, 'openai')
    assert normalized[0]['content'][0]['text'] == 'look'
    assert images[0].asset.mime == 'image/png'
    assert (images[0].asset.width, images[0].asset.height) == (24, 16)
    assert images[0].marker == normalized[0]['content'][1]['text']
    assert 'base64' not in images[0].marker
    messages[0]['content'][1]['image_url']['url'] = data_url('blue')
    changed, _ = normalize_images(messages, 'openai')
    assert changed != normalized


def test_anthropic_nested_tool_result_preserves_association():
    messages = [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'tool-1',
        'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                    'data': base64.b64encode(picture()).decode()}}]}]}]
    normalized, images = normalize_images(messages, 'anthropic')
    block = normalized[0]['content'][0]
    assert block['tool_use_id'] == 'tool-1'
    assert block['content'][0] == {'type': 'text', 'text': images[0].marker}


@pytest.mark.parametrize('format,mime', [('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp')])
def test_supported_formats(format, mime):
    asset = load_image({'type': 'base64', 'media_type': mime,
                        'data': base64.b64encode(picture(format=format)).decode()}, ImageLimits())
    assert asset.mime == mime


@pytest.mark.parametrize('source,status', [
    ({'type': 'base64', 'media_type': 'image/png', 'data': '%%%bad'}, 400),
    ({'type': 'url', 'url': 'file:///etc/passwd'}, 400),
    ({'type': 'base64', 'media_type': 'image/gif', 'data': 'AA=='}, 415),
    ({'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(b'not an image').decode()}, 400),
])
def test_invalid_sources_are_explicit_errors(source, status):
    with pytest.raises(ImageError) as exc:
        load_image(source, ImageLimits())
    assert exc.value.status == status


def test_declared_mime_must_match_bytes():
    with pytest.raises(ImageError):
        load_image({'type': 'base64', 'media_type': 'image/jpeg',
                    'data': base64.b64encode(picture()).decode()}, ImageLimits())


def test_image_count_bytes_and_pixels_are_bounded():
    blocks = [{'type': 'image_url', 'image_url': {'url': data_url()}}] * 2
    with pytest.raises(ImageError) as exc:
        normalize_images([{'role': 'user', 'content': blocks}], 'openai', ImageLimits(max_images=1))
    assert exc.value.status == 413
    for limits in [ImageLimits(max_bytes=5), ImageLimits(max_pixels=10), ImageLimits(max_total_bytes=5)]:
        with pytest.raises(ImageError) as exc:
            normalize_images([{'role': 'user', 'content': blocks[:1]}], 'openai', limits)
        assert exc.value.status == 413


def test_images_in_unsupported_roles_are_not_silently_dropped():
    with pytest.raises(ImageError):
        normalize_images([{'role': 'assistant', 'content': [
            {'type': 'image_url', 'image_url': {'url': data_url()}}]}], 'openai')


@pytest.mark.parametrize('url', ['http://127.0.0.1/x', 'http://[::1]/x',
    'http://169.254.169.254/latest', 'http://10.0.0.1/x', 'http://user:pw@example.com/x'])
def test_non_public_targets_are_rejected(url):
    with pytest.raises(ImageError):
        public_target(url)


def test_dns_answers_are_checked_and_pinned(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    target = public_target('https://images.example/test.png')
    assert target.address == '8.8.8.8'
    assert target.host == 'images.example'
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 443))])
    with pytest.raises(ImageError):
        public_target('https://images.example/test.png')
