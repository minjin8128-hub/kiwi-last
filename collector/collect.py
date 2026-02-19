#!/usr/bin/env python3
import os
import json
import requests
import pandas as pd
from datetime import datetime, timedelta

# GitHub Secrets (Settings → Secrets에서 설정)
ECOWITT_APPLICATION_KEY = os.environ.get('ECOWITT_APPLICATION_KEY', '')
ECOWITT_API_KEY = os.environ.get('ECOWITT_API_KEY', '')
ECOWITT_DEVICE_ID = os.environ.get('ECOWITT_DEVICE_ID', '')
ECOWITT_MAC = os.environ.get('ECOWITT_MAC', '')
TBASE_C = float(os.environ.get('TBASE_C', '5.0'))
H_BUD = float(os.environ.get('H_BUD', '80.0'))
H_BLOOM = float(os.environ.get('H_BLOOM', '180.0'))
CHILL_TARGET_DAYS = float(os.environ.get('CHILL_TARGET_DAYS', '100.0'))


def _to_celsius(value, unit=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if unit and 'f' in str(unit).lower():
        return (v - 32) * 5.0 / 9.0
    return v


def _read_temp(data, direct_key, nested_key):
    if direct_key in data:
        return _to_celsius(data.get(direct_key))

    node = data.get(nested_key, {})
    if isinstance(node, dict):
        temp_node = node.get('temperature', {})
        if isinstance(temp_node, dict):
            return _to_celsius(temp_node.get('value'), temp_node.get('unit'))

    dotted = data.get(f'{nested_key}.temperature')
    if dotted is not None:
        return _to_celsius(dotted)

    return 0.0


def _extract_device_data(payload):
    """Ecowitt 응답 변형 대응"""
    if not isinstance(payload, dict):
        raise ValueError('Ecowitt 응답이 JSON 객체가 아님')

    code = payload.get('code')
    if code not in (None, 0, '0'):
        raise ValueError(f"Ecowitt API 오류(code={code}): {payload.get('msg', 'unknown')}")

    data = payload.get('data', {})

    if isinstance(data, dict) and 'device' in data:
        devices = data.get('device')
        if not isinstance(devices, list) or not devices:
            raise ValueError('Ecowitt 응답 device 목록이 비어있음')
        first = devices[0]
        if isinstance(first, dict) and isinstance(first.get('data'), dict):
            return first['data']
        raise ValueError('Ecowitt 응답 device[0].data 형식이 잘못됨')

    if isinstance(data, dict):
        return data

    raise ValueError('Ecowitt 응답에서 device data를 찾지 못함')


def _read_soil_moisture(data):
    if 'soil_moisture' in data:
        try:
            return float(data.get('soil_moisture', 0))
        except (TypeError, ValueError):
            return 0.0

    soil = data.get('soil_ch1', {})
    if isinstance(soil, dict):
        sm = soil.get('soilmoisture', {})
        if isinstance(sm, dict):
            try:
                return float(sm.get('value', 0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def get_ecowitt_recent():
    try:
        if not (ECOWITT_APPLICATION_KEY and ECOWITT_API_KEY):
            raise ValueError('ECOWITT 환경변수(application/api) 누락')

        if ECOWITT_DEVICE_ID:
            device_selector = f'&device_id={ECOWITT_DEVICE_ID}'
        elif ECOWITT_MAC:
            device_selector = f'&mac={ECOWITT_MAC}'
        else:
            raise ValueError('ECOWITT 환경변수(device_id 또는 mac) 누락')

        url = (
            'https://api.ecowitt.net/api/v3/device/current'
            f'?application_key={ECOWITT_APPLICATION_KEY}'
            f'&api_key={ECOWITT_API_KEY}'
            f'{device_selector}&time_zone=KST'
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        data = _extract_device_data(payload)

        outdoor = _read_temp(data, 'outdoor_c', 'outdoor')
        t2 = _read_temp(data, '2동_c', 'temp_and_humidity_ch1')
        t3 = _read_temp(data, '3동_c', 'temp_and_humidity_ch2')

        return {
            'timestamp': datetime.now().isoformat(),
            '외부온도': outdoor,
            '외부_c': outdoor,
            '2동_c': t2,
            '3동_c': t3,
            '토양수분': _read_soil_moisture(data)
        }
    except Exception as exc:
        return {
            'timestamp': datetime.now().isoformat(),
            '외부온도': None,
            '외부_c': None,
            '2동_c': None,
            '3동_c': None,
            'error': f'API 연결 오류: {exc}'
        }


def load_or_create_daily():
    if os.path.exists('data/daily.csv'):
        df = pd.read_csv('data/daily.csv')
    else:
        df = pd.DataFrame(columns=['date', 'tmin_2dong', 'tmax_2dong', 'tmean', 'gdd', 'cum_gdd', 'chill'])
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)


def update_daily_data(df):
    now = get_ecowitt_recent()
    today = datetime.now().date()
    today_row = df[df['date'].dt.date == today]

    if today_row.empty:
        new_row = {
            'date': pd.Timestamp(today),
            'tmin_2dong': now.get('2동_c', 0),
            'tmax_2dong': now.get('2동_c', 0),
            'tmean': now.get('2동_c', 0)
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    else:
        df.loc[df['date'].dt.date == today, 'tmean'] = now.get('2동_c', 0)

    df['tmean'] = pd.to_numeric(df['tmean'], errors='coerce').fillna(5)
    df['gdd'] = (df['tmean'] - TBASE_C).clip(lower=0)
    df['cum_gdd'] = df['gdd'].cumsum()
    df['chill'] = (df['tmean'] < 10).astype(int).cumsum()
    return df, now


def build_status(df, now_data):
    total_gdd = float(df['cum_gdd'].iloc[-1])
    chill_days = float(df['chill'].iloc[-1])
    avg_daily_gdd = float(df['gdd'].tail(7).mean()) if len(df) >= 7 else 2.0

    bud_remaining = max(0, H_BUD - total_gdd)
    bloom_remaining = max(0, H_BLOOM - total_gdd)
    today = datetime.now().date()
    bud_date = today + timedelta(days=int(bud_remaining / max(avg_daily_gdd, 0.5) * 1.2))
    bloom_date = today + timedelta(days=int(bloom_remaining / max(avg_daily_gdd, 0.5) * 1.2))
    chill_pct = min(100, chill_days / CHILL_TARGET_DAYS * 100) if CHILL_TARGET_DAYS > 0 else 0

    return {
        'timestamp': datetime.now().isoformat(),
        'updated_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
        'now': now_data,
        'gdd': {
            'total': round(total_gdd, 2),
            'cum_from_0215': round(total_gdd, 2),
            'target_bud': H_BUD,
            'target_bloom': H_BLOOM,
            'progress': f"{min(100, total_gdd / H_BUD * 100):.0f}%",
            'bud_remaining': round(bud_remaining, 1),
            'bloom_remaining': round(bloom_remaining, 1),
            'bud_date': bud_date.strftime('%m/%d'),
            'bloom_date': bloom_date.strftime('%m/%d'),
            'pred_bud10_date': bud_date.strftime('%m/%d'),
            'pred_bloom50_date': bloom_date.strftime('%m/%d'),
            'basis': '2동 실시간 + 히스토리 보정'
        },
        'chill': {
            'total_days': round(chill_days, 1),
            'cum': round(chill_days, 1),
            'target': CHILL_TARGET_DAYS,
            'progress': f'{chill_pct:.0f}%',
            'pct': round(chill_pct, 1)
        },
        'config': {'TBASE_C': TBASE_C, 'H_BUD': H_BUD, 'H_BLOOM': H_BLOOM}
    }


def main():
    os.makedirs('data', exist_ok=True)
    df = load_or_create_daily()
    df, now_data = update_daily_data(df)
    status = build_status(df, now_data)

    df[['date', 'tmean', 'gdd', 'cum_gdd', 'chill']].to_csv('data/daily.csv', index=False)
    with open('data/status.json', 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

    print(f"✅ 업데이트 완료: GDD {status['gdd']['total']} ({status['gdd']['progress']}), 발아예상 {status['gdd']['bud_date']}")


if __name__ == '__main__':
    main()
