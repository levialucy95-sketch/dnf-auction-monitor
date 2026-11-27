# DNF Auction Monitor

네오플 Open API로 던전앤파이터 경매장 시세를 자동 수집하는 프로젝트입니다.

## 수집 방식

- GitHub Actions가 매시 정각에 시작해 3분 간격으로 수집합니다 (`.github/workflows/collect.yml`).
- 대상 아이템 15종: `items.csv`
- 시세 API는 최근 거래 100건만 주기 때문에, 짧은 간격으로 받아 새 거래만 쌓습니다.

## 데이터

| 파일 | 내용 |
|---|---|
| `data/sales/YYYY-MM-DD.csv` | 실거래 내역 (거래 시각 기준 날짜) |
| `data/snapshots/YYYY-MM-DD.csv` | 수집 시점마다 아이템별 매물 요약 (최저가, 매물 수, 총 수량 등) |

스냅샷의 `sales_gap=1`은 수집 간격 사이에 거래가 100건을 넘어 일부 거래가 누락됐을 수 있다는 뜻입니다.
`listings_truncated=1`은 매물이 400건을 넘어 `total_qty`가 일부만 집계됐다는 뜻입니다 (최저가는 정확함).

## 로컬 실행

```
# .env 에 NEOPLE_API_KEY=발급받은키
python collect.py              # 1회 수집
python collect.py --rounds 3 --interval 60
```

외부 라이브러리 없이 Python 표준 라이브러리만 사용합니다.
