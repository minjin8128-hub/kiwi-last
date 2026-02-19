#!/usr/bin/env python3
import os
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO

# GitHub Secrets (Settings → Secrets에서 설정)
ECOWITT_API_KEY = os.environ.get('ECOWITT_API_KEY', 'dummy')
TBASE_C = float(os.environ.get('TBASE_C', '5.0'))
H_BUD = float(os.environ.get('H_BUD', '80.0'))
H_BLOOM = float(os.environ.get('H_BLOOM', '180.0'))
CHILL_TARGET_DAYS = float(os.environ.get('CHILL_TARGET_DAYS', '100.0'))

def get_ecowitt_recent():
    """실시간 데이터 가져오기"""
    try:
        url = f"https://api.ecowitt.net/api/v3/device/current?application_key={ECOWITT_API_KEY}&api_key={ECOWITT_API_KEY}&device_id=YOUR_DEVICE_ID&time_zone=KST"
        resp = requests.get(url, timeout=10)
        data = resp.json()['data']['device'][0]['data']
        return {
            'timestamp': datetime.now().isoformat(),
            '외부온도': float(data.get('outdoor', 0)),
            '2동_c': float(data.get('2동_c', 0)),
            '3동_c': float(data.get('3동_c', 0)),
            '토양수분': float(data.get('soil_moisture', 0))
        }
    except:
        return {'timestamp': datetime.now().isoformat(), 'error': 'API 연결 오류'}

def load_or_create_daily():
    """daily.csv 로드/생성"""
    if os.path.exists('data/daily.csv'):
        df = pd.read_csv('data/daily.csv')
    else:
        df = pd.DataFrame(columns=['date', 'tmin_2dong', 'tmax_2dong', 'tmean', 'gdd', 'cum_gdd', 'chill'])
    
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)

def update_daily_data(df):
    """2동 데이터로 daily 업데이트 + GDD 계산"""
    now = get_ecowitt_recent()
    
    # 오늘 데이터 추가/업데이트
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
    
    # GDD 계산 (기존 데이터 전체 재계산)
    df['tmean'] = pd.to_numeric(df['tmean'], errors='coerce').fillna(5)
    df['gdd'] = (df['tmean'] - TBASE_C).clip(lower=0)
    df['cum_gdd'] = df['gdd'].cumsum()
    df['chill'] = (df['tmean'] < 10).astype(int).cumsum()
    
    return df, now

# 메인 실행
os.makedirs('data', exist_ok=True)

df = load_or_create_daily()
df, now_data = update_daily_data(df)

# 최신 통계
total_gdd = df['cum_gdd'].iloc[-1]
chill_days = df['chill'].iloc[-1]
avg_daily_gdd = df['gdd'].tail(7).mean() if len(df) >= 7 else 2.0

# 예상일 (안전계수 20%)
bud_remaining = max(0, H_BUD - total_gdd)
bloom_remaining = max(0, H_BLOOM - total_gdd)
today = datetime.now().date()
bud_date = today + timedelta(days=int(bud_remaining / max(avg_daily_gdd, 0.5) * 1.2))
bloom_date = today + timedelta(days=int(bloom_remaining / max(avg_daily_gdd, 0.5) * 1.2))

status = {
    "timestamp": datetime.now().isoformat(),
    "now": now_data,
    "gdd": {
        "total": round(total_gdd, 2),
        "target_bud": H_BUD,
        "target_bloom": H_BLOOM,
        "progress": f"{min(100, total_gdd/H_BUD*100):.0f}%",
        "bud_remaining": round(bud_remaining, 1),
        "bloom_remaining": round(bloom_remaining, 1),
        "bud_date": bud_date.strftime("%m/%d"),
        "bloom_date": bloom_date.strftime("%m/%d"),
        "basis": "2동 실시간 + 히스토리 보정"
    },
    "chill": {
        "total_days": round(chill_days, 1),
        "target": CHILL_TARGET_DAYS,
        "progress": f"{min(100, chill_days/CHILL_TARGET_DAYS*100):.0f}%"
    },
    "config": {
        "TBASE_C": TBASE_C,
        "H_BUD": H_BUD,
        "H_BLOOM": H_BLOOM
    }
}

# 저장
df[['date', 'tmean', 'gdd', 'cum_gdd', 'chill']].to_csv('data/daily.csv', index=False)
with open('data/status.json', 'w', encoding='utf-8') as f:
    json.dump(status, f, indent=2, ensure_ascii=False)

print(f"✅ 업데이트 완료: GDD {status['gdd']['total']} ({status['gdd']['progress']}), 발아예상 {status['gdd']['bud_date']}")
