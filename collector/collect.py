import os, json, datetime, csv
import requests

# ===== 설정 =====
TBASE_C = float(os.getenv("TBASE_C", "5"))
H_BUD = float(os.getenv("H_BUD", "80"))
H_BLOOM = float(os.getenv("H_BLOOM", "180"))
GDD_START_MMDD = "02-15"

CHILL_SEASON_START_MMDD = "11-01"
CHILL_SEASON_END_MMDD = "02-14"
CHILL_TMIN = 2.0
CHILL_TMAX = 10.0
CHILL_TARGET_DAYS = float(os.getenv("CHILL_TARGET_DAYS", "100"))
CHILL_70 = 70.0
CHILL_90 = 90.0

KST = datetime.timezone(datetime.timedelta(hours=9))

def f_to_c(f):
    return (float(f) - 32.0) * 5.0 / 9.0

def safe_float(x):
    try:
        return float(x)
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
    s = mmdd(date_obj)
    return (s >= CHILL_SEASON_START_MMDD) or (s <= CHILL_SEASON_END_MMDD)

def is_gdd_season(date_obj):
    return mmdd(date_obj) >= GDD_START_MMDD

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

def fetch_history_temp_ch1(start_dt_kst, end_dt_kst):
    """2동(temp_and_humidity_ch1.temperature) 히스토리 가져오기.
       반환: list of (epoch_int, temp_c_float)
    """
    api_key = os.environ["ECOWITT_API_KEY"]
    app_key = os.environ["ECOWITT_APPLICATION_KEY"]
    mac = os.environ["ECOWITT_MAC"]

    start = start_dt_kst.strftime("%Y-%m-%d %H:%M:%S")
    end = end_dt_kst.strftime("%Y-%m-%d %H:%M:%S")

    url = "https://api.ecowitt.net/api/v3/device/history"
    params = {
        "application_key": app_key,
        "api_key": api_key,
        "mac": mac,
        "start_date": start,
        "end_date": end,
        "call_back": "temp_and_humidity_ch1.temperature",
    }

    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        return []

    # 구조: data.temp_and_humidity_ch1.temperature.unit="°F"
    #       data.temp_and_humidity_ch1.temperature.list = { "epoch": "F", ... }
    lst = get_path(j, ["data", "temp_and_humidity_ch1", "temperature", "list"])
    if not isinstance(lst, dict):
        return []

    out = []
    for k, v in lst.items():
        try:
            epoch = int(k)
            temp_f = float(v)
            out.append((epoch, f_to_c(temp_f)))
        except:
            pass
    out.sort(key=lambda x: x[0])
    return out

def upsert_daily_rows_from_history(rows_by_date, hist_points):
    """hist_points: list of (epoch, temp_c). KST 날짜로 묶어서 tmin/tmax 갱신."""
    for epoch, tc in hist_points:
        dt = datetime.datetime.fromtimestamp(epoch, tz=KST)
        dkey = ymd(dt.date())
        row = rows_by_date.get(dkey)
        if row is None:
            row = {}
            rows_by_date[dkey] = row
            row["date"] = dkey
            row["tmin_c"] = ""
            row["tmax_c"] = ""
            row["tmean_c"] = ""
            row["gdd"] = ""
            row["gdd_cum_from_0215"] = ""
            row["chillday"] = ""
            row["chill_cum"] = ""

        tmin = safe_float(row.get("tmin_c"))
        tmax = safe_float(row.get("tmax_c"))
        tmin = tc if tmin is None else min(tmin, tc)
        tmax = tc if tmax is None else max(tmax, tc)
        row["tmin_c"] = f"{tmin:.2f}"
        row["tmax_c"] = f"{tmax:.2f}"

def recompute_gdd_chill(rows_by_date):
    """daily.csv 전체를 날짜순으로 돌며 tmean/gdd/누적gdd/칠링 누적 재계산."""
    all_dates = sorted(rows_by_date.keys())
    gdd_cum = 0.0
    chill_cum = 0.0

    for dstr in all_dates:
        d = datetime.datetime.strptime(dstr, "%Y-%m-%d").date()
        r = rows_by_date[dstr]
        tmin = safe_float(r.get("tmin_c"))
        tmax = safe_float(r.get("tmax_c"))
        if tmin is None or tmax is None:
            continue

        tmean = (tmin + tmax) / 2.0
        r["tmean_c"] = f"{tmean:.2f}"

        gdd = max(0.0, tmean - TBASE_C)
        r["gdd"] = f"{gdd:.2f}"
        if is_gdd_season(d):
            gdd_cum += gdd
        r["gdd_cum_from_0215"] = f"{gdd_cum:.2f}"

        chillday = 0.0
        if in_chill_season(d) and (CHILL_TMIN <= tmean <= CHILL_TMAX):
            chillday = 1.0
        chill_cum += chillday
        r["chillday"] = f"{chillday:.0f}"
        r["chill_cum"] = f"{chill_cum:.0f}"

    return all_dates

def pick_threshold_dates(rows_by_date, all_dates):
    bud_date = None
    bloom_date = None
    for dstr in all_dates:
        val = safe_float(rows_by_date[dstr].get("gdd_cum_from_0215"))
        if val is None:
            continue
        if bud_date is None and val >= H_BUD:
            bud_date = dstr
        if bloom_date is None and val >= H_BLOOM:
            bloom_date = dstr

    # chill percent and 70/90% date
    chill_cum_last = 0.0
    if all_dates:
        last = all_dates[-1]
        chill_cum_last = safe_float(rows_by_date[last].get("chill_cum")) or 0.0

    chill_pct = None
    if CHILL_TARGET_DAYS > 0:
        chill_pct = min(100.0, (chill_cum_last / CHILL_TARGET_DAYS) * 100.0)

    target70 = CHILL_TARGET_DAYS * (CHILL_70 / 100.0)
    target90 = CHILL_TARGET_DAYS * (C
