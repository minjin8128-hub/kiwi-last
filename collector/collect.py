import requests
import json
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO
import os

# GitHub Secrets (Settings → Secrets에서 설정)
ECOWITT_API_KEY = os.environ.get('ECOWITT_API_KEY', 'your_key_here')
TBASE_C = float(os.environ.get('TBASE_C', 5.0))
H_BUD = float(os.environ.get('H_BUD', 80.0))      # 발아10%
H_BLOOM = float(os.environ.get('H_BLOOM', 180.0)) # 만개50%
CHILL_TARGET_DAYS = float(os.environ.get('CHILL_TARGET_DAYS', 100.0))

def get_ecowitt_data():
    url = f"https://api.ecowitt.net/api/v3/device/history?application_key={ECOWITT_API_KEY}&api_key={ECOWITT_API_KEY}&device_id=your_device_id&start_date=2026-02-01&end_date=2026-02-19&time_zone=KST"
    resp = requests.get(url)
    data = resp.json()
    return data['data']['device'][0]['data']

def process_daily_data(raw_data):
    df = pd.read_csv(StringIO(raw_data))
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    
    # 2동 온도만 사용 (tmin, tmax 평균)
    df['tmean'] = (df['2동_tmin'] + df['2동_tmax']) / 2
    df['gdd'] = (df['tmean'] - TBASE_C).clip(lower=0)
    df['cum_gdd'] = df['gdd'].cumsum()
    
    # 칠링 (일평균<10°C만 카운트)
    df['chill'] = (df['tmean'] < 10).astype(int).cumsum()
    
    # 히스토리 보정 (최근 2일 재가져오기)
    recent = get_ecowitt_data()
    today = datetime.now().date()
    df.loc[df['date'].dt.date == today - timedelta(days=1), 'tmean'] = recent['2동_tmean']
    
    return df

# 메인 실행
raw_data = open('data/daily.csv').read() if os.path.exists('data/daily.csv') else ''
if raw_data:
    daily = process_daily_data(raw_data)
    
    # 최신값
    total_gdd = daily['cum_gdd'].iloc[-1]
    chill_days = daily['chill'].iloc[-1]
    avg_daily_gdd = daily['gdd'].tail(7).mean()
    
    # 예상일 계산 (안전계수 20%)
    bud_remaining = max(0, H_BUD - total_gdd)
    bloom_remaining = max(0, H_BLOOM - total_gdd)
    today = datetime.now().date()
    bud_date = today + timedelta(days=int(bud_remaining / avg_daily_gdd * 1.2))
    bloom_date = today + timedelta(days=int(bloom_remaining / avg_daily_gdd * 1.2))
    
    status = {
        "timestamp": datetime.now().isoformat(),
        "now": get_ecowitt_data(),
        "gdd": {
            "total": round(total_gdd, 2),
            "target_bud": H_BUD,
            "target_bloom": H_BLOOM,
            "progress": f"{min(100, total_gdd/H_BUD*100):.0f}%",
            "bud_remaining": f"{bud_remaining:.0f}",
            "bloom_remaining": f"{bloom_remaining:.0f}",
            "bud_date": bud_date.strftime("%m/%d"),
            "bloom_date": bloom_date.strftime("%m/%d"),
            "basis": "2동(tmin/tmax) 기반, 히스토리로 어제/오늘 보정"
        },
        "chill": {
            "total_days": round(chill_days, 1),
            "target": CHILL_TARGET_DAYS,
            "progress": f"{min(100, chill_days/CHILL_TARGET_DAYS*100):.0f}%"
        }
    }
    
    # 저장
    os.makedirs('data', exist_ok=True)
    daily.to_csv('data/daily.csv', index=False)
    with open('data/status.json', 'w') as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
else:
    print("daily.csv 없음. 처음 실행인가요?")
