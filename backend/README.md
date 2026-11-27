# backend

Python 後端：資料管線、資料庫、指標、策略、回測、統計驗證。

從**專案根目錄**執行：

```bash
pip install -r requirements.txt
python3 -m pytest                       # 全部測試
python3 -m backend.cli init-db          # 建立資料庫
python3 -m backend.cli download reference
python3 -m backend.cli download prices --stocks 2330 2317
python3 -m backend.cli status
```

模組：`config/`（設定）、`core/`（日誌、重試、節流）、`data/providers`（資料來源介面）、
`data/collectors`（下載器）、`data/normalizers`（正規化、還原、漲跌停）、`data/validators`（資料品質）、
`database/`（schema、遷移、讀寫）、`indicators/`（指標、週線）、`strategies/`（訊號）、`tests/`。
