# 趨勢周報（雲端自動版）

GitHub Actions 每週一 08:00(台北) 自動抓 ESG/AI 新聞 -> 產 HTML -> Gmail 寄至 Outlook。

## 設定
需在 repo Settings > Secrets 設三個：`GMAIL_USER`、`GMAIL_APP_PW`、`MAIL_TO`。

改主題：編輯 `weekly_report.py` 開頭 `QUERIES`。
