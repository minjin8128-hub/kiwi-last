from pathlib import Path
import json
from unittest.mock import patch, Mock

import collector.collect as collect


def test_extract_device_data_with_device_list():
    payload = {
        'code': 0,
        'data': {
            'device': [
                {'data': {'temp_and_humidity_ch1': {'temperature': {'value': '50.0', 'unit': 'ºF'}}}}
            ]
        },
    }
    out = collect._extract_device_data(payload)
    assert 'temp_and_humidity_ch1' in out


def test_extract_device_data_with_flat_data():
    payload = {'code': 0, 'data': {'outdoor_c': '11.0'}}
    out = collect._extract_device_data(payload)
    assert out['outdoor_c'] == '11.0'


def test_extract_device_data_raises_api_error():
    payload = {'code': 1, 'msg': 'bad key'}
    try:
        collect._extract_device_data(payload)
    except ValueError as exc:
        assert 'code=1' in str(exc)
    else:
        raise AssertionError('ValueError not raised')


def test_read_temp_nested_fahrenheit_to_celsius():
    data = {
        'temp_and_humidity_ch1': {'temperature': {'value': '50.0', 'unit': 'ºF'}},
    }
    value = collect._read_temp(data, '2동_c', 'temp_and_humidity_ch1')
    assert round(value, 2) == 10.0


def test_read_temp_dotted_key_fallback():
    data = {'temp_and_humidity_ch1.temperature': '55.4'}
    value = collect._read_temp(data, '2동_c', 'temp_and_humidity_ch1')
    assert round(value, 1) == 55.4


def test_read_soil_moisture_fallback():
    data = {'soil_ch1': {'soilmoisture': {'value': '44'}}}
    assert collect._read_soil_moisture(data) == 44.0


def test_get_ecowitt_recent_with_mac_selector():
    fake_payload = {
        'code': 0,
        'data': {
            'device': [
                {
                    'data': {
                        'outdoor': {'temperature': {'value': '41.0', 'unit': 'ºF'}},
                        'temp_and_humidity_ch1': {'temperature': {'value': '50.0', 'unit': 'ºF'}},
                        'temp_and_humidity_ch2': {'temperature': {'value': '45.0', 'unit': 'ºF'}},
                        'soil_ch1': {'soilmoisture': {'value': '40'}},
                    }
                }
            ]
        },
    }

    fake_resp = Mock()
    fake_resp.raise_for_status = Mock()
    fake_resp.json.return_value = fake_payload

    with patch.object(collect, 'ECOWITT_APPLICATION_KEY', 'app'), \
         patch.object(collect, 'ECOWITT_API_KEY', 'api'), \
         patch.object(collect, 'ECOWITT_DEVICE_ID', ''), \
         patch.object(collect, 'ECOWITT_MAC', 'aa:bb'), \
         patch('collector.collect.requests.get', return_value=fake_resp) as mock_get:
        now = collect.get_ecowitt_recent()

    called_url = mock_get.call_args[0][0]
    assert '&mac=aa:bb' in called_url
    assert round(now['2동_c'], 2) == 10.0
    assert round(now['3동_c'], 2) == 7.22
    assert now['토양수분'] == 40.0


def test_no_merge_conflict_markers():
    targets = [
        Path('collector/collect.py'),
        Path('collector/test_collect.py'),
        Path('.github/workflows/collect.yml'),
        Path('index.html'),
        Path('docs/index.html'),
    ]
    for target in targets:
        text = target.read_text(encoding='utf-8')
        start = '<' * 7
        mid = '=' * 7
        end = '>' * 7
        assert start not in text, f'merge marker in {target}'
        assert mid not in text, f'merge marker in {target}'
        assert end not in text, f'merge marker in {target}'
