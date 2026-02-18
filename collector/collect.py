import os, json, datetime, csv
import requests

# ===== 사용자가 나중에 조정할 값(임계치/기준온도) =====
TBASE_C = float(os.getenv("TBASE_C", "5"))          # GDD 기준온도(섭씨). 예: 5
H_BUD = float(os.getenv("H_BUD", "80"))             # 발아10% 임계 누적GDD(예시값, 나중에 보정)
H_BLOOM = float(os.getenv("H_BLOOM", "180"))        # 만개50% 임계 누적GDD(예시값, 나중에 보정)
GDD_START_MMDD = "02-15"                            # 적산 시작일(월-일 고정)
CHILL_SEASON_START_MMDD = "11-01"
CHILL_SEASON_END_MMDD = "02-14"
CHILL_TMIN = 2.0
CHILL_TMAX = 10.0
CHILL_70 = float(os.getenv("CHILL_70", "70"))       # 목표치의 70% 날짜 표시(아래 CHILL_TARGET로 환산)
CHILL_90 = float(os.getenv("CHILL_90", "90"))
CHILL_TARGET_DAYS = float(os.getenv("CHILL_TARGET_DAYS", "100"))  # 시즌 목표 ChillDay (예: 100일=100점)

# ===== 유틸 =====
def f_to_c(f):
    return (float(f) - 32.0) * 5.0 / 9.0  # C = (F-32)*5/9 [web 근거는 답변에서]

def parse_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except:
        return None

def get_path(d, path):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur

def ymd(date_obj):
    return date_obj.strftime("%Y-%m-%d")

def mmdd(date_obj):
    return date_obj.strftime("%m-%d")

def in_chill_season(date_obj):
    # 시즌: 11/1 ~ 12/31 AND 1/1 ~ 2/14 (연도 걸침)
    m = date_obj.month
    d = date_obj.day
    mmdd_s = f"{m:02d}-{d:02d}"
    return (mmdd_s >= CHILL_SEASON_START_MMDD) or (mmdd_s <= CHILL_SEASON_END_MMDD)

def is_gdd_season(date_obj):
    return mmdd(date_obj) >= GDD_START_MMDD

# ===== Ecowitt 호출 =====
def fetch_realtime():
    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    url = (
        "https://api.ecowitt.net/api/v3/device/real_time"
        f"?application_key={app_key}&api_key={api_key}&mac={mac}&call_back=all"
    )
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    raw = fetch_realtime()

    os.makedirs("data", exist_ok=True)
    with open("data/raw_last.json", "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)

    # ===== 여기서 “외기”가 없어서 임시로 ch1 온도를 외기로 사용 =====
    # 너가 보내준 JSON에는 outdoor가 안 보이고, temp_and_humidity_ch1/2/3가 있어요.
    # 우선: 외기 = temp_and_humidity_ch1.temperature.value (ºF)
    # 실내 = indoor.temperature.value (ºF)
    indoor_f = get_path(raw, ["data", "indoor", "temperature", "value"])
    outdoor_f = get_path(raw, ["data", "temp_and_humidity_ch1", "temperature", "value"])

    indoor_c = f_to_c(indoor_f) if indoor_f is not None else None
    outdoor_c = f_to_c(outdoor_f) if outdoor_f is not None else None

    # 토양수분 채널(soil_ch1~6) 모으기
    soils = {}
    for ch in range(1, 7):
        key = f"soil_ch{ch}"
        v = get_path(raw, ["data", key, "soilmoisture", "value"])
        if v is not None:
            soils[key] = parse_float(v)

    # ===== daily.csv 업데이트(“오늘” 행만 관리: tmin/tmax를 누적) =====
    today = datetime.datetime.now().date()  # Actions 러너 시간(UTC일 수 있음)
    daily_path = "data/daily.csv"
    fieldnames = ["date", "tmin_c", "tmax_c", "tmean_c", "gdd", "gdd_cum_from_0215", "chillday", "chill_cum"]

    rows = {}
    if os.path.exists(daily_path):
        with open(daily_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows[r["date"]] = r

    # 오늘 온도값이 없으면 daily 계산 못함
    if outdoor_c is not None:
        key = ymd(today)
        if key not in rows:
            rows[key] = {
                "date": key,
                "tmin_c": str(outdoor_c),
                "tmax_c": str(outdoor_c),
                "tmean_c": "",
                "gdd": "",
                "gdd_cum_from_0215": "",
                "chillday": "",
                "chill_cum": "",
            }
        else:
            tmin = parse_float(rows[key].get("tmin_c"))
            tmax = parse_float(rows[key].get("tmax_c"))
            tmin = outdoor_c if tmin is None else min(tmin, outdoor_c)
            tmax = outdoor_c if tmax is None else max(tmax, outdoor_c)
            rows[key]["tmin_c"] = str(tmin)
            rows[key]["tmax_c"] = str(tmax)

    # 날짜순으로 계산(누적 GDD, 누적 chill)
    all_dates = sorted(rows.keys())
    gdd_cum = 0.0
    chill_cum = 0.0
    for dstr in all_dates:
        d = datetime.datetime.strptime(dstr, "%Y-%m-%d").date()
        tmin = parse_float(rows[dstr].get("tmin_c"))
        tmax = parse_float(rows[dstr].get("tmax_c"))
        if tmin is None or tmax is None:
            continue

        tmean = (tmin + tmax) / 2.0
        rows[dstr]["tmean_c"] = f"{tmean:.2f}"

        # GDD (2/15부터 누적)
        gdd = max(0.0, tmean - TBASE_C)
        rows[dstr]["gdd"] = f"{gdd:.2f}"
        if is_gdd_season(d):
            gdd_cum += gdd
        rows[dstr]["gdd_cum_from_0215"] = f"{gdd_cum:.2f}"

        # ChillDay(겨울 시즌에만): 일평균 2~10C면 1점
        chillday = 0.0
        if in_chill_season(d) and (CHILL_TMIN <= tmean <= CHILL_TMAX):
            chillday = 1.0
        chill_cum += chillday
        rows[dstr]["chillday"] = f"{chillday:.0f}"
        rows[dstr]["chill_cum"] = f"{chill_cum:.0f}"

    # daily.csv 저장(전체 다시 씀)
    with open(daily_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for dstr in all_dates:
            w.writerow({k: rows[dstr].get(k, "") for k in fieldnames})

    # 예측일 계산(임계 누적GDD 도달 첫날)
    bud_date = None
    bloom_date = None
    for dstr in all_dates:
        val = parse_float(rows[dstr].get("gdd_cum_from_0215"))
        if val is None:
            continue
        if bud_date is None and val >= H_BUD:
            bud_date = dstr
        if bloom_date is None and val >= H_BLOOM:
            bloom_date = dstr

    # 칠링 상태(목표 대비 %)
    chill_pct = None
    if CHILL_TARGET_DAYS > 0:
        chill_pct = min(100.0, (chill_cum / CHILL_TARGET_DAYS) * 100.0)

    chill70_date = None
    chill90_date = None
    target70 = CHILL_TARGET_DAYS * (CHILL_70 / 100.0)
    target90 = CHILL_TARGET_DAYS * (CHILL_90 / 100.0)
    for dstr in all_dates:
        v = parse_float(rows[dstr].get("chill_cum"))
        if v is None:
            continue
        if chill70_date is None and v >= target70:
            chill70_date = dstr
        if chill90_date is None and v >= target90:
            chill90_date = dstr

    # status.json 저장(대시보드용)
    status = {
        "updated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "now": {
            "indoor_c": None if indoor_c is None else round(indoor_c, 2),
            "outdoor_c": None if outdoor_c is None else round(outdoor_c, 2),
            "soil": soils,
        },
        "gdd": {
            "tbase_c": TBASE_C,
            "start_mmdd": GDD_START_MMDD,
            "cum_from_0215": None if not all_dates else rows[all_dates[-1]].get("gdd_cum_from_0215"),
            "H_BUD": H_BUD,
            "H_BLOOM": H_BLOOM,
            "pred_bud10_date": bud_date,
            "pred_bloom50_date": bloom_date,
        },
        "chill": {
            "season": f"{CHILL_SEASON_START_MMDD}~{CHILL_SEASON_END_MMDD}",
            "rule": "ChillDay=1 if daily mean 2~10C else 0",
            "target_days": CHILL_TARGET_DAYS,
            "cum": int(chill_cum),
            "pct": None if chill_pct is None else round(chill_pct, 1),
            "date_70": chill70_date,
            "date_90": chill90_date,
        },
        "note": "현재는 실시간을 '오늘 tmin/tmax 누적'으로 일자료를 만듭니다. 다음 단계에서 history API로 누락일 자동 보정 붙일 수 있어요."
    }

    with open("data/status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
