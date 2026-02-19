#!/usr/bin/env python3
import os
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

ECOWITT_API_KEY = os.environ.get('ECOWITT_API_KEY', 'dummy')
ECOWITT_DEVICE_ID = os.environ.get('ECOWITT_DEVICE_ID', 'YOUR_DEVICE_ID')
TBASE_C = float(os.environ.get('TBASE_C', '5.0'))
H_BUD = float(os.environ.get('H_BUD', '80.0'))
H_BLOOM = float(os.environ.get('H_BLOOM', '180.0'))
CHILL_TARGET_DAYS = float(os.environ.get('CHILL_TARGET_DAYS', '100.0'))
IRR_MM_PER_H_DEFAULT = float(os.environ.get('IRR_MM_PER_H_DEFAULT', '6.0'))
IRR_TARGET_THETA_REL_DEFAULT = float(os.environ.get('IRR_TARGET_THETA_REL_DEFAULT', '0.70'))
IRR_K_DEFAULT = float(os.environ.get('IRR_K_DEFAULT', '0.04'))


def _to_float(v, default=None):
    try:
        if isinstance(v, dict):
            v = v.get('value')
        return float(v)
    except Exception:
        return default


def _extract(data, *paths):
    for path in paths:
        cur = data
        ok = True
        for p in path.split('.'):
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok:
            val = _to_float(cur)
            if val is not None:
                return val
    return None


def _calc_slope_per_hour(df, value_col, hours):
    if df.empty:
        return 0.0
    now_ts = df['timestamp'].iloc[-1]
    recent = df[df['timestamp'] >= now_ts - pd.Timedelta(hours=hours)]
    if len(recent) < 2:
        return 0.0
    x = (recent['timestamp'] - recent['timestamp'].iloc[0]).dt.total_seconds() / 3600.0
    y = recent[value_col]
    denom = (x * x).sum()
    if denom == 0:
        return 0.0
    return float((x * (y - y.iloc[0])).sum() / denom)


def get_ecowitt_recent():
    try:
        url = (
            'https://api.ecowitt.net/api/v3/device/current'
            f'?application_key={ECOWITT_API_KEY}'
            f'&api_key={ECOWITT_API_KEY}'
            f'&device_id={ECOWITT_DEVICE_ID}&time_zone=KST'
        )
        resp = requests.get(url, timeout=10)
        payload = resp.json()
        raw = payload['data']['device'][0]['data']

        outside = _extract(raw, 'outdoor', 'outdoor_c', 'outdoor.temperature')
        t2 = _extract(raw, '2동_c', 'temp_and_humidity_ch1.temperature')
        t3 = _extract(raw, '3동_c', 'temp_and_humidity_ch2.temperature')
        tin = _extract(raw, 'indoor.temperature', 'tin')
        tsoil = _extract(raw, 'temp_and_humidity_ch3.temperature', 'tsoil')

        soils = {}
        for i in range(1, 9):
            v = _extract(raw, f'soil_ch{i}.soilmoisture', f'soil_ch{i}', f'wh51_ch{i}', f'soil_moisture_ch{i}')
            if v is not None:
                soils[f'soil_ch{i}'] = round(v, 1)

        valid_soils = [v for v in soils.values() if 0 < v <= 100]
        soil_rep = float(pd.Series(valid_soils).median()) if valid_soils else None

        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            '외부온도': outside,
            '외부_c': outside,
            '2동_c': t2,
            '3동_c': t3,
            'tin_c': tin,
            'tsoil_c': tsoil,
            'soil': soils,
            'soil_moist_pct_rep': soil_rep,
        }
    except Exception:
        return {'timestamp': datetime.now(timezone.utc).isoformat(), 'error': 'API 연결 오류'}


def load_or_create_daily():
    if os.path.exists('data/daily.csv'):
        df = pd.read_csv('data/daily.csv')
    else:
        df = pd.DataFrame(columns=['date', 'tmin_2dong', 'tmax_2dong', 'tmean', 'gdd', 'cum_gdd', 'chill'])

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)


def load_or_create_soil_history():
    path = 'data/soil_history.csv'
    if os.path.exists(path):
        s = pd.read_csv(path)
    else:
        s = pd.DataFrame(columns=['timestamp', 'zone_id', 'theta_pct_rep', 'tin_c', 'tout_c', 'tsoil_c'])
    if not s.empty:
        s['timestamp'] = pd.to_datetime(s['timestamp'], errors='coerce', utc=True)
        s['theta_pct_rep'] = pd.to_numeric(s['theta_pct_rep'], errors='coerce')
        s['tin_c'] = pd.to_numeric(s['tin_c'], errors='coerce')
        s['tout_c'] = pd.to_numeric(s['tout_c'], errors='coerce')
        s['tsoil_c'] = pd.to_numeric(s['tsoil_c'], errors='coerce')
        s = s.dropna(subset=['timestamp']).sort_values('timestamp')
    return s


def update_daily_data(df):
    now = get_ecowitt_recent()
    today = datetime.now().date()
    today_mask = df['date'].dt.date == today

    t2 = _to_float(now.get('2동_c'), 0.0)
    if not today_mask.any():
        new_row = {'date': pd.Timestamp(today), 'tmin_2dong': t2, 'tmax_2dong': t2, 'tmean': t2}
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    else:
        df.loc[today_mask, 'tmean'] = t2

    df['tmean'] = pd.to_numeric(df['tmean'], errors='coerce').fillna(5)
    df['gdd'] = (df['tmean'] - TBASE_C).clip(lower=0)
    df['cum_gdd'] = df['gdd'].cumsum()
    df['chill'] = (df['tmean'] < 10).astype(int).cumsum()
    return df, now


def build_irrigation(zone_id, now, soil_hist, target_theta_rel=IRR_TARGET_THETA_REL_DEFAULT, mm_per_h=IRR_MM_PER_H_DEFAULT):
    sensors = now.get('soil') or {}
    sensor_vals = {k: float(v) for k, v in sensors.items() if _to_float(v) is not None and 0 < float(v) <= 100}
    rep_theta = _to_float(now.get('soil_moist_pct_rep'))

    if rep_theta is None and sensor_vals:
        rep_theta = float(pd.Series(sensor_vals.values()).median())

    ts = pd.to_datetime(now.get('timestamp'), utc=True)
    if pd.notna(ts) and rep_theta is not None:
        new_row = pd.DataFrame([{
            'timestamp': ts,
            'zone_id': zone_id,
            'theta_pct_rep': rep_theta,
            'tin_c': _to_float(now.get('tin_c')),
            'tout_c': _to_float(now.get('외부_c')),
            'tsoil_c': _to_float(now.get('tsoil_c')),
        }])
        soil_hist = pd.concat([soil_hist, new_row], ignore_index=True)

    if soil_hist.empty:
        return {
            'zone_id': zone_id,
            'sensor_values_pct': sensor_vals,
            'theta_pct_rep': rep_theta,
            'note': 'soil history 부족'
        }, soil_hist

    z = soil_hist[soil_hist['zone_id'] == zone_id].copy()
    z = z.dropna(subset=['theta_pct_rep']).sort_values('timestamp')
    z = z[z['timestamp'] >= z['timestamp'].max() - pd.Timedelta(days=30)]

    if z.empty:
        return {'zone_id': zone_id, 'sensor_values_pct': sensor_vals, 'theta_pct_rep': rep_theta, 'note': 'zone history 없음'}, soil_hist

    z['theta_med3'] = z['theta_pct_rep'].rolling(3, min_periods=1).median()
    theta_dry = float(z['theta_med3'].quantile(0.05))

    rises = z['theta_pct_rep'].diff()
    plateaus = []
    for i in range(len(z) - 2):
        if pd.notna(rises.iloc[i + 1]) and rises.iloc[i + 1] >= 1.5:
            post = z.iloc[i + 1:i + 4].copy()
            if len(post) >= 2:
                post_slope = _calc_slope_per_hour(post.rename(columns={'theta_pct_rep': 'theta'}), 'theta', 3)
                if abs(post_slope) <= 0.2:
                    plateaus.extend(post['theta_pct_rep'].tolist())

    theta_wet = float(pd.Series(plateaus).quantile(0.95)) if plateaus else float(z['theta_pct_rep'].quantile(0.95))
    if theta_wet <= theta_dry:
        theta_wet = theta_dry + 1.0

    theta_rel = (float(z['theta_pct_rep'].iloc[-1]) - theta_dry) / (theta_wet - theta_dry)
    theta_rel = max(0.0, min(1.0, theta_rel))

    slope_6h = _calc_slope_per_hour(z.rename(columns={'theta_pct_rep': 'theta'}), 'theta', 6)
    slope_24h = _calc_slope_per_hour(z.rename(columns={'theta_pct_rep': 'theta'}), 'theta', 24)

    recent6 = z[z['timestamp'] >= z['timestamp'].max() - pd.Timedelta(hours=6)]
    recent24 = z[z['timestamp'] >= z['timestamp'].max() - pd.Timedelta(hours=24)]
    mean_6h = float(recent6['theta_pct_rep'].mean()) if not recent6.empty else float(z['theta_pct_rep'].iloc[-1])
    mean_24h = float(recent24['theta_pct_rep'].mean()) if not recent24.empty else float(z['theta_pct_rep'].iloc[-1])

    events = []
    for i in range(len(z) - 1):
        dtheta = z['theta_pct_rep'].iloc[i + 1] - z['theta_pct_rep'].iloc[i]
        dt_h = (z['timestamp'].iloc[i + 1] - z['timestamp'].iloc[i]).total_seconds() / 3600.0
        if dtheta > 1.5 and 0 < dt_h <= 3:
            rel_before = (z['theta_pct_rep'].iloc[i] - theta_dry) / (theta_wet - theta_dry)
            rel_after = (z['theta_pct_rep'].iloc[i + 1] - theta_dry) / (theta_wet - theta_dry)
            drel = max(0.0, rel_after - rel_before)
            mm = mm_per_h * dt_h
            if mm > 0 and drel > 0:
                events.append(drel / mm)

    k = float(pd.Series(events).median()) if events else IRR_K_DEFAULT
    k = max(0.005, min(k, 0.3))

    deficit = max(0.0, target_theta_rel - theta_rel)
    needed_mm = deficit / k if k > 0 else 0.0
    needed_minutes = 0.0 if mm_per_h <= 0 else (needed_mm / mm_per_h) * 60.0
    needed_minutes = max(0.0, min(120.0, needed_minutes))

    predicted_after = min(1.0, theta_rel + k * mm_per_h * (needed_minutes / 60.0))
    tin = _to_float(now.get('tin_c'))

    cycle = False
    reasons = []
    if deficit > 0.10 and slope_6h < 0:
        cycle = True
        reasons.append('theta_rel 부족 + 건조 추세')
    if tin is not None and tin >= 28:
        cycle = True
        reasons.append('하우스 내부 고온(증발 리스크)')
    if predicted_after >= 0.90 and needed_minutes >= 25:
        cycle = True
        reasons.append('단발 관수 시 과상승 위험')

    if cycle and needed_minutes > 0:
        cycle_plan = {
            'enabled': True,
            'plan': f"{int(round(needed_minutes / 2))}분 × 2회, 90분 간격"
        }
    else:
        cycle_plan = {'enabled': False, 'plan': '단발 관수'}

    return {
        'zone_id': zone_id,
        'sensor_values_pct': sensor_vals,
        'theta_pct_rep': round(float(z['theta_pct_rep'].iloc[-1]), 2),
        'theta_dry': round(theta_dry, 2),
        'theta_wet': round(theta_wet, 2),
        'theta_rel': round(theta_rel, 3),
        'target_theta_rel': round(float(target_theta_rel), 3),
        'deficit_theta_rel': round(deficit, 3),
        'trend': {
            'mean_6h': round(mean_6h, 2),
            'mean_24h': round(mean_24h, 2),
            'slope_6h_per_h': round(slope_6h, 4),
            'slope_24h_per_h': round(slope_24h, 4)
        },
        'response': {
            'k_theta_rel_per_mm': round(k, 4),
            'source': 'median_ratio_30d' if events else 'default',
            'event_samples': len(events)
        },
        'recommendation': {
            'mm_per_h': mm_per_h,
            'needed_mm': round(needed_mm, 2),
            'needed_minutes': int(round(needed_minutes)),
            'predicted_theta_rel_after': round(predicted_after, 3),
            'cycle': cycle_plan,
            'reasons': reasons or ['목표 대비 결손량 기준 기본 추천']
        },
        'simulation': {
            'input_minutes': None,
            'predicted_theta_rel': round(theta_rel, 3),
            'target_reached': deficit <= 0
        }
    }, soil_hist


os.makedirs('data', exist_ok=True)

df = load_or_create_daily()
soil_history = load_or_create_soil_history()
df, now_data = update_daily_data(df)

# GDD/chill
total_gdd = float(df['cum_gdd'].iloc[-1])
chill_days = float(df['chill'].iloc[-1])
avg_daily_gdd = float(df['gdd'].tail(7).mean()) if len(df) >= 7 else 2.0

bud_remaining = max(0, H_BUD - total_gdd)
bloom_remaining = max(0, H_BLOOM - total_gdd)
today = datetime.now().date()
bud_date = today + timedelta(days=int(bud_remaining / max(avg_daily_gdd, 0.5) * 1.2))
bloom_date = today + timedelta(days=int(bloom_remaining / max(avg_daily_gdd, 0.5) * 1.2))

chill_pct = min(100, chill_days / CHILL_TARGET_DAYS * 100) if CHILL_TARGET_DAYS > 0 else 0

irrigation_zone, soil_history = build_irrigation('zone_main', now_data, soil_history)

status = {
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'updated_utc': datetime.now(timezone.utc).isoformat(),
    'now': now_data,
    'gdd': {
        'total': round(total_gdd, 2),
        'cum_from_0215': round(total_gdd, 2),
        'target_bud': H_BUD,
        'target_bloom': H_BLOOM,
        'H_BUD': H_BUD,
        'H_BLOOM': H_BLOOM,
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
        'progress': f"{chill_pct:.0f}%",
        'pct': round(chill_pct, 1)
    },
    'irrigation': {
        'method': {
            'combine_rule': 'zone 내 센서 중앙값(median) 대표값 사용',
            'theta_rel': 'clamp((theta_pct-theta_dry)/(theta_wet-theta_dry), 0..1)'
        },
        'defaults': {
            'target_theta_rel': IRR_TARGET_THETA_REL_DEFAULT,
            'mm_per_h': IRR_MM_PER_H_DEFAULT
        },
        'zones': [irrigation_zone]
    },
    'config': {
        'TBASE_C': TBASE_C,
        'H_BUD': H_BUD,
        'H_BLOOM': H_BLOOM
    }
}

df[['date', 'tmean', 'gdd', 'cum_gdd', 'chill']].to_csv('data/daily.csv', index=False)
soil_history = soil_history.sort_values('timestamp').drop_duplicates(subset=['timestamp', 'zone_id'], keep='last')
soil_history.to_csv('data/soil_history.csv', index=False)
with open('data/status.json', 'w', encoding='utf-8') as f:
    json.dump(status, f, indent=2, ensure_ascii=False)

print(f"✅ 업데이트 완료: GDD {status['gdd']['total']} ({status['gdd']['progress']}), zone_main 권장 {status['irrigation']['zones'][0].get('recommendation',{}).get('needed_minutes','-')}분")
