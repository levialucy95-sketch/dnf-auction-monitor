"""던파 경매장 수집기.

한 라운드마다 items.csv의 아이템별로
  - 시세(최근 거래 100건)를 받아 새 거래만 data/sales/YYYY-MM-DD.csv 에 추가하고
  - 현재 매물 상태를 요약해 data/snapshots/YYYY-MM-DD.csv 에 한 줄 추가한다.

시세 API는 최근 100건만 주기 때문에, 거래가 많은 아이템은 수집 간격이
벌어지면 거래가 누락된다. 누락 가능성이 있으면 스냅샷의 sales_gap=1 로 남긴다.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
SALES_DIR = ROOT / "data" / "sales"
SNAP_DIR = ROOT / "data" / "snapshots"
KST = dt.timezone(dt.timedelta(hours=9))

SOLD_LIMIT = 100      # API가 주는 최대치 (limit를 더 줘도 100건)
AUCTION_LIMIT = 400   # 매물 조회 최대치

SALES_FIELDS = ["sold_date", "item_id", "item_name", "count", "price", "unit_price"]
SNAP_FIELDS = ["collected_at", "item_id", "item_name", "min_unit_price", "listing_rows",
               "total_qty", "api_average_price", "listings_truncated", "new_sales", "sales_gap"]


def load_api_key():
    key = os.environ.get("NEOPLE_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("NEOPLE_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("NEOPLE_API_KEY 가 없습니다 (.env 또는 환경 변수)")
    return key


def api_get(key, endpoint, **params):
    params["apikey"] = key
    url = f"https://api.neople.co.kr/df/{endpoint}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.load(resp)["rows"]


def sale_key(row):
    return (row["item_id"], row["sold_date"], str(row["count"]), str(row["price"]))


def load_stored_sales():
    """저장된 전체 거래의 키 개수와 아이템별 마지막 거래 시각.

    거래가 드문 아이템은 최근 100건이 며칠 전까지 거슬러 올라가므로
    최근 파일만 읽으면 옛 거래를 새 거래로 착각한다. 그래서 전부 읽는다.
    """
    keys, latest = Counter(), {}
    for path in sorted(SALES_DIR.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                keys[sale_key(row)] += 1
                if row["sold_date"] > latest.get(row["item_id"], ""):
                    latest[row["item_id"]] = row["sold_date"]
    return keys, latest


def append_rows(path, fields, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            w.writeheader()
        w.writerows(rows)


def collect_item(key, item, stored_keys, latest):
    iid, name = item["item_id"], item["item_name"]

    sold = [{"sold_date": r["soldDate"], "item_id": iid, "item_name": name,
             "count": r["count"], "price": r["price"], "unit_price": r["unitPrice"]}
            for r in api_get(key, "auction-sold", itemId=iid, limit=SOLD_LIMIT)]

    # 같은 초에 같은 수량·가격으로 팔린 거래가 실제로 여러 건일 수 있어서
    # 집합이 아니라 개수로 비교한다: 이미 저장된 개수를 넘는 만큼만 새 거래.
    batch = Counter(sale_key(r) for r in sold)
    new_rows, added = [], Counter()
    for r in sold:
        k = sale_key(r)
        if stored_keys[k] + added[k] < batch[k]:
            added[k] += 1
            new_rows.append(r)
    stored_keys.update(added)

    # 받아온 100건 중 가장 오래된 거래가 이미 저장된 마지막 거래보다 뒤라면
    # 그 사이 거래를 놓쳤을 수 있다. (처음 수집하는 아이템은 판단 불가라 0)
    oldest = min((r["sold_date"] for r in sold), default=None)
    gap = int(len(sold) >= SOLD_LIMIT and iid in latest and oldest > latest[iid])
    for r in new_rows:
        if r["sold_date"] > latest.get(iid, ""):
            latest[iid] = r["sold_date"]

    listings = api_get(key, "auction", itemId=iid, limit=AUCTION_LIMIT, sort="unitPrice:asc")
    snap = {
        "item_id": iid, "item_name": name,
        "min_unit_price": listings[0]["unitPrice"] if listings else "",
        "listing_rows": len(listings),
        "total_qty": sum(r["count"] for r in listings),
        "api_average_price": listings[0]["averagePrice"] if listings else "",
        "listings_truncated": int(len(listings) >= AUCTION_LIMIT),
        "new_sales": len(new_rows),
        "sales_gap": gap,
    }
    return new_rows, snap


def run_round(key, items):
    now = dt.datetime.now(KST)
    stored_keys, latest = load_stored_sales()
    all_sales, snaps = [], []
    for item in items:
        try:
            new_rows, snap = collect_item(key, item, stored_keys, latest)
        except Exception as e:  # 한 아이템 실패로 라운드 전체를 버리지 않는다
            print(f"  [실패] {item['item_name']}: {e}")
            continue
        snap["collected_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        all_sales += new_rows
        snaps.append(snap)
        flag = " (누락 가능)" if snap["sales_gap"] else ""
        print(f"  {item['item_name']}: 새 거래 {snap['new_sales']}건, 최저가 {snap['min_unit_price']}{flag}")

    by_day = {}
    for r in all_sales:
        by_day.setdefault(r["sold_date"][:10], []).append(r)
    for day, rows in sorted(by_day.items()):
        append_rows(SALES_DIR / f"{day}.csv", SALES_FIELDS, sorted(rows, key=lambda r: r["sold_date"]))
    append_rows(SNAP_DIR / f"{now.date().isoformat()}.csv", SNAP_FIELDS, snaps)
    return len(snaps)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=1, help="수집 라운드 수")
    p.add_argument("--interval", type=int, default=300, help="라운드 간격(초)")
    args = p.parse_args()

    key = load_api_key()
    with (ROOT / "items.csv").open(encoding="utf-8", newline="") as f:
        items = list(csv.DictReader(f))

    ok_rounds = 0
    for i in range(args.rounds):
        if i:
            time.sleep(args.interval)
        print(f"[{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S} KST] 라운드 {i + 1}/{args.rounds}")
        if run_round(key, items):
            ok_rounds += 1
    if ok_rounds == 0:
        sys.exit("모든 라운드가 실패했습니다")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
