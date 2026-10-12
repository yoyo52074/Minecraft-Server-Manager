# Minecraft Server Manager 資安審查

**審查日期：** 2026-10-12  
**審查範圍：** FastAPI Web 面板、登入／權限／Session／CSRF／WebSocket、伺服器檔案與備份、ZIP 還原、Java／核心下載、啟動程序、Webhook、Python 相依套件。  
**審查方式：** 人工程式碼審查、Bandit、pip-audit、Python 單元／整合測試；本輪未對實際 Windows 主機或內網穿透端點執行外部滲透測試。

## 結論

找到並修補多項授權、CSRF、路徑／連結處理、ZIP 解壓／還原、上傳資源管理、設定檔寫入及伺服器生命週期問題。修補後 **94 項測試通過**；Bandit 掃描 **6,326 行：High 0、Medium 0、Low 19**；pip-audit 回報 **No known vulnerabilities found**。

這些結果表示已知程式碼問題有所改善，**不等於 Windows 主機、隧道服務、反向代理或網路設定已通過滲透測試或安全認證**。前一版安全修補曾由 GitHub Actions 建置並通過 Smoke Test（Run `38184656310`；commit `ea78eb721bff0e587a0feca9d65965286a50bceb`）；但該產物**不包含本輪的世界鎖、關閉流程及空目錄安裝修正**。目前工作階段的 GitHub 授權未啟用，推送新 commit 被拒，因此這輪尚無新的 Windows EXE CI 結果。

## 修補摘要

| 風險 | 問題 | 修補 |
|---|---|---|
| 高 | 初次設定時未登入遠端使用者可能搶先建立第一個管理員。 | 首位管理員初始化限定 loopback 本機連線、檢查來源／Fetch Metadata，並以 SQLite 原子交易建立。首次建立管理員需在執行管理器的主機本地操作。 |
| 中 | 舊版全域狀態／Console API 與 WebSocket 權限不足；帳號撤權後既有連線可能繼續讀取。 | API、WebSocket 檢查相應伺服器權限與來源；長連線重新驗證 Session／權限；停用或變更角色會撤銷既有 Session。刪除伺服器時同步清除其 permission records；刪除 legacy profile 後 API／WebSocket 不再使用 stale profile。 |
| 中 | 登入／登出 CSRF、跨 scheme Origin 判斷，以及 HTTPS 代理下 Cookie 保護不足。 | 補上登入來源檢查及登出 CSRF Token；同源判斷比對 scheme 與 authority；透過 HTTPS／`X-Forwarded-Proto: https` 時設定 Secure Cookie。 |
| 高 | 檔案路徑及伺服器資料夾中的 symlink／Windows junction 可能導致外部檔案讀寫或存取內部版本／上傳資料。 | 檔案路徑拒絕 symlink／reparse point；比對 Unicode casefold、Windows 尾點／尾空白、保留 device names、NTFS ADS 與內部目錄別名（例如 `.MCM_VERSIONS.`）。列舉、搜尋、快照及複製不遞迴追蹤 reparse point。 |
| 高 | ZIP／備份檔可能使用大小寫或正規化別名、特殊 entry、路徑衝突；解壓失敗可能留半套檔案；還原中途 I/O 失敗可能留下部分替換。 | ZIP preflight 檢查 Windows canonical duplicate、檔案／目錄父子衝突、保留路徑、特殊檔案及類型；解壓先進 staging 後提交，失敗清除／回滾；備份還原提交失敗會還原已替換的舊檔並清理新建目錄。 |
| 中 | 分段上傳可累積大量未完成暫存，失敗 final 或程序中斷可能留下 staging。 | 單次最多 8 MiB；每伺服器最多 20 個未完成工作、暫存總量 2 GiB、單檔 512 MiB；暫存工作 24 小時過期；同伺服器配額更新序列化；狀態檔原子寫入，final 失敗清除 part／metadata。 |
| 中 | 固定暫存檔名或 `server.properties` symlink 可能把寫入導向伺服器目錄外。 | `server.properties` 拒絕 symlink／reparse point，設定透過同目錄暫存檔原子替換；一般上傳、文字編輯、版本回存、ZIP 建立使用獨佔隨機暫存檔。設定安全快照只讀一般檔案，並限制 4 MiB。 |
| 中 | 核心／Java 下載可能被重新導向、惡意檔名或超大 ZIP 利用。 | 下載限定 HTTPS 主機 allowlist 並逐跳驗證 redirect；遠端檔名限制 basename；核心下載上限 1 GiB，Java ZIP 限制下載／展開量與 entry 數，拒絕 symlink／特殊項目。 |
| 中 | Webhook 重新導向／URL port 差異可能突破預期 allowlist，排程保存端與實際送出端驗證不一致。 | 設定與送出共用 validator，只接受 Discord／Slack 官方 HTTPS host、預期路徑、443 port、無 userinfo，並限制 500 字元；送出有 timeout 且不跟隨 redirect。 |
| 中 | Java 不足時伺服器無法啟動；POSIX Forge `run.sh` 曾被當作 JAR 啟動。 | Java 不足會搜尋／下載符合版本後重試；Forge `.sh` 在 POSIX 以 `/bin/sh` argv 啟動，Windows 明確拒絕 POSIX 腳本；未使用 `shell=True`。 |
| 低 | metrics、玩家查詢及 profile 更新曾使用動態 SQL 字串。 | 改用固定 SQL 與參數繫結。 |
| 低 | 總覽頁有多餘核心支援標語。 | 已移除並由測試覆蓋。 |
| 中 | 管理器關閉時固定等待 3 秒、Web 停止時 15 秒後自動 kill，可能在 Paper 世界存檔仍進行時關閉／殺掉 Java，留下持鎖程序或造成世界損壞。 | 桌面關閉改為等待 Java 與輸出執行緒結束；Web 正常停止不再自動強制終止；重啟等待舊程序退出；啟動與核心更新前以 OS file lock 檢查世界 `session.lock`，提示使用者勿刪鎖檔。 |
| 低 | 新伺服器未指定路徑時可能解析到目前工作目錄，空目錄啟動也只回報找不到核心。 | 空路徑改在程式資料夾建立安全化的伺服器名稱目錄；Paper/Vanilla 空目錄啟動會先建立備份、下載／安裝核心，再自動重試啟動。 |

## 驗證結果

- Python 語法編譯：本輪修改模組通過。
- `python -m unittest discover -s tests -v`：**94 項通過**。
- Bandit：**6,326 行；High 0、Medium 0、Low 19**。剩餘 Low 主要是使用參數陣列且未啟用 shell 的 `subprocess` 呼叫，以及監控迴圈的寬泛例外處理；未發現 High／Medium 項目。
- pip-audit：**No known vulnerabilities found**。
- 前一版 Windows EXE（不含本輪修改）：GitHub Actions 驗證成功，PE32+ x86-64，大小 **33,645,308 bytes**，SHA-256：`d3bc01eff27eb3e759676d1c7bbd761ead0e0ba4b02061a3fc09e140437f3ae9`。本輪 commit 尚待推送並執行 Windows CI，不能用此舊 EXE 代替。
- 本機安全案例包含：無效 ZIP 衝突策略拒絕、ZIP staging 失敗不半套覆寫、備份還原 commit 失敗回滾、Windows 大小寫重複路徑拒絕、上傳超額／過期／final 清理、properties symlink 拒絕、profile 刪除後的 API／WebSocket 撤銷。
- 本輪另覆蓋：模擬 Java OS lock 時阻止啟動與核心備份、stale `session.lock` 可安全重用、正常停止不會自動 kill、空目錄安裝 Paper 核心與未指定路徑預設。

## 部署與剩餘風險

- 本次依內網穿透的使用情境做應用程式層檢查，未調整使用者的隧道網路設定或嘗試探測外部 IP。隧道可隱藏／轉送來源，但不取代帳號驗證、最小權限或 HTTPS；仍建議使用強密碼、移除不必要帳號，並確認代理傳遞 `X-Forwarded-Proto: https`。
- 具備 `files.upload`、檔案管理或伺服器啟動權限的帳號，仍可能透過核心／插件程式在 Minecraft 程序權限下執行程式碼。請使用專用、低權限 Windows 帳號執行管理器及伺服器。
- 尚未對真實 Windows 主機、實際內網穿透／代理 ACL 或網路防火牆執行外部滲透測試；Bandit Low 提示也不等同已證實漏洞。
