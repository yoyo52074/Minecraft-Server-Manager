# Minecraft Server Manager 工程師交接報告

> 文件類型：專案接手、開發路線與驗收報告  
> 文件版本：v1.0  
> 最後更新：2026-10-12  
> 專案：`yoyo52074/Minecraft-Server-Manager`  
> 目前分支：`main`  
> 最新提交：`3dcbd13 fix: remove duplicate server settings link`

---

## 1. 交接摘要

本專案原本是一個 Windows Python/Tkinter Minecraft 開服管理器，後續擴充為「單台 Windows 主機、多個 Minecraft 伺服器 Profile、繁體中文 Web 管理面板」的架構。

目前的產品方向是：

1. 桌面程式只負責啟動 Web 面板、顯示整體狀態與安全關閉。
2. 所有伺服器的啟動、停止、主控台、檔案、設定、備份、玩家、排程與監控功能，集中在 Web 面板。
3. 支援多個 Minecraft 伺服器。
4. 支援 Vanilla、Paper、Forge、Fabric 基礎啟動適配。
5. 使用者登入後依照「帳號／伺服器／功能」三層權限操作。
6. 預設 Web 位址為 `127.0.0.1:8765`，目前仍以本機安全使用為主；若要出門連線，應優先使用 Tailscale、VPN 或 HTTPS 反向代理，不建議直接裸露連接埠到公網。

目前已完成的是一個可以實際使用的基礎管理面板，不是完整的 Pterodactyl 替代品。下一位工程師應以「補齊可靠性、安全性與完整管理體驗」為主要工作，而不是重新建立目前已存在的登入、Profile、權限與基本伺服器控制功能。

---

## 2. 目前版本與交付物

### 2.1 Git 狀態

```text
分支：main
最新提交：3dcbd13
工作樹：已驗證乾淨
```

最近的重要提交：

| Commit | 內容 |
|---|---|
| `3dcbd13` | 移除重複的「設定／伺服器設定」入口，更新完成度清單 |
| `63171e5` | 加入桌面簡化、Java、監控歷史、玩家、排程、備份中心 |
| `1f6e90d` | 首頁改為伺服器入口卡片，移除首頁啟停按鈕 |
| `89e1b2e` | 加入詳細帳號／伺服器／功能權限矩陣與設定中心 |
| `3b03e84` | 強化伺服器狀態同步與主控台行為 |
| `ac78ed8` | 修正資源監控頁 JavaScript 錯誤 |
| `849a044` | 修正 Windows PyInstaller 的 psutil 打包 |
| `04de61a` | 加入即時資源圖表與進階檔案操作後端 |
| `5775512` | 加入即時 WebSocket 監控與檔案導覽 |
| `353516a` | 加入伺服器設定與備份 API |
| `3844a60` | 加入多伺服器 Profile 與檔案管理 |

### 2.2 最新 Windows 執行檔

最新已驗證執行檔：

[下載最新 Windows 執行檔](</home/ubuntu/Minecraft-Server-Manager/artifacts/Minecraft-Server-Manager.exe>)

SHA-256：

```text
0129921cb3d452749e9bd1d06cb404757d8a7aab63b9947661b7a535ade48b00
```

檔案類型：Windows PE32+ GUI x86-64。建置流程已通過：

- PyInstaller GUI Build
- PyInstaller Console Diagnostic Build
- Windows EXE Smoke Test
- `http://127.0.0.1:8765/login` 回應測試

### 2.3 目前重要文件

| 文件 | 用途 |
|---|---|
| `WEB_PANEL_BLUEPRINT.md` | 原始 Web 面板藍圖；部分內容已落後，應以本交接報告與實際程式碼為準 |
| `UNIMPLEMENTED_FEATURES.md` | 目前完成度與未完成項目清單 |
| `ENGINEERING_HANDOFF_REPORT.md` | 本次完整工程交接報告 |
| `README.md` | 使用者安裝與基本使用說明 |
| `.github/workflows/build-windows.yml` | Windows EXE 建置與 Smoke Test |
| `tests/` | 現有 Python 測試 |

---

## 3. 產品目標

### 3.1 最終產品目標

建立一個在 Windows 上執行、以繁體中文為主、類似 Pterodactyl/Pelican 面板操作方式的 Minecraft 多伺服器管理工具。

每台伺服器應能被視為獨立 Profile，擁有：

- 獨立資料夾
- 獨立 Minecraft 核心
- 獨立 Java 路徑
- 獨立記憶體設定
- 獨立遊戲連接埠
- 獨立啟動檔案
- 獨立檔案、設定、備份、玩家與排程
- 獨立使用者權限
- 獨立監控資料

### 3.2 明確非目標

目前不應直接把範圍擴大到：

- Docker/Kubernetes 節點編排
- 多台遠端主機叢集
- 任意 Windows Shell 遠端執行
- 自動修改 Windows 防火牆規則
- 預設把管理面板暴露到公網
- 沒有版本鎖定的自動插件／模組下載

這些功能若要做，必須另立安全設計與版本管理方案。

---

## 4. 已完成項目

## 4.1 桌面程式與 Web 面板啟動

目前 `main.py` 仍由 `ApplicationController` 啟動 Web 面板。Web 面板透過 Uvicorn 在背景執行緒啟動。

預設行為：

- 啟動 Web 面板：`127.0.0.1:8765`
- 顯示簡化桌面狀態視窗
- 顯示 Web 面板網址
- 提供「開啟 Web 面板」
- 提供「停止所有伺服器並關閉」
- Web 面板啟動失敗時寫入 `web_panel_startup.log`

備援行為：

```powershell
Minecraft-Server-Manager.exe --legacy-ui
```

`--legacy-ui` 會保留原本完整 Tkinter 介面，方便排查或兼容舊使用習慣。

### 目前限制

簡化視窗是在現有完整 Tkinter root 上建立後將 root 隱藏，並不是完全拆除舊 UI。後續如果要真正降低資源使用與維護複雜度，應將桌面啟動器與舊 UI 分成兩個明確入口或兩個類別。

---

## 4.2 認證與帳號

目前使用 SQLite 保存：

- 使用者
- 密碼雜湊
- Session
- 使用者啟用狀態
- 角色
- 稽核資料
- 每台伺服器的功能權限
- 監控歷史
- 排程

已完成：

- 首次啟動建立第一位管理員
- 登入
- 登出
- Session Cookie
- CSRF Token
- 管理員建立帳號
- 帳號啟用/停用
- 管理員頁面
- 帳號權限矩陣

目前要特別注意：

- 密碼與 Session 的安全策略要在正式對外部署前再次檢查。
- 尚未完成登入失敗限速、密碼重設、二步驟驗證與全部裝置登出。
- 尚未完成完整稽核報表頁面。

---

## 4.3 多伺服器 Profile

每台伺服器使用 Profile 保存：

- 名稱
- 資料夾
- 核心類型
- Minecraft 版本
- 遊戲 Port
- Java 路徑
- 最小記憶體
- 最大記憶體
- 啟動檔案
- 自動重啟設定欄位

Profile 由 `web_panel/multi_server.py` 內的 `ServerProfileStore` 與 `MultiServerManager` 管理。

目前支援：

- 建立 Profile
- 修改 Profile
- 刪除 Profile
- 多台伺服器同時存在
- 舊版既有伺服器作為 legacy Profile
- 每台伺服器獨立狀態
- 每台伺服器獨立日誌
- 每台伺服器獨立權限

---

## 4.4 伺服器生命週期

`MultiServerManager` 目前負責：

- 啟動
- 停止
- 強制停止
- 重啟
- 指令輸入
- 輸出讀取
- 狀態階段
- 啟動時間
- 進程結束偵測
- 啟動/停止逾時的基本保護

狀態階段包含：

```text
stopped
starting
running
stopping
error
```

狀態傳遞方式：

1. WebSocket 即時推送。
2. 前端定期回補 API。
3. 狀態頁面對啟動/停止逾時做基本判斷。

這一層是目前最重要的核心，後續新增排程、玩家、備份或自動重啟時，必須使用這個生命週期服務，不要從新功能直接 `subprocess.Popen`。

---

## 4.5 即時主控台

已完成：

- WebSocket 主控台輸出
- 最近日誌保存
- 前端自動滾到最下方
- 指令送出
- Broken Pipe/輸入串流錯誤處理
- 指令長度與空白檢查
- 主控台斷線後前端重連
- Legacy bridge 與多伺服器流程整合

後續需要優化：

- 目前主控台指令仍然接近任意 Minecraft console command，應增加高風險指令二次確認或白名單。
- 應將主控台日誌以 ring buffer/資料流方式抽出，不要讓 UI 與程序管理互相綁定。
- 應增加每台伺服器的日誌大小限制、下載日誌與搜尋。

---

## 4.6 檔案管理

目前已完成：

- 資料夾點擊進入
- 返回上一層
- 麵包屑/路徑導覽
- 檔案列表
- 上傳
- 下載
- 刪除
- 改名
- 文字檔讀取
- 文字檔線上編輯
- 路徑穿越防護
- 複製、移動、ZIP、解壓縮的後端服務基礎

尚未完整：

- 複製/移動/壓縮/解壓縮的完整前端操作介面
- 批次操作
- 搜尋
- 大檔案分段上傳
- 檔案版本歷史
- 衝突處理

所有檔案功能都必須繼續使用 `web_panel/file_service.py` 的安全路徑解析，不能在路由直接拼接使用者提供的路徑。

---

## 4.7 設定中心

目前每台伺服器只有一個正式設定入口：

```text
伺服器設定
```

先前曾經同時出現「設定」與「伺服器設定」兩個指向相同 URL 的按鈕，已於 `3dcbd13` 修正。後續修改 UI 時不要重新加入重複入口。

已完成：

- `server.properties` 讀取
- `server.properties` 寫入
- 常用欄位編輯
- Java 路徑
- 最小/最大記憶體
- 啟動檔案
- 啟動相關設定欄位
- 伺服器 Port 基本驗證

尚未完整：

- 設定修改前自動備份
- 設定變更差異比較
- 分類顯示所有 Minecraft 設定
- 設定格式與核心版本相容性檢查
- 設定變更歷史

---

## 4.8 備份中心

目前已完成基礎版本：

- 手動建立 ZIP 備份
- 備份列表
- 備份大小與時間
- 備份下載
- 備份刪除
- 備份還原
- 伺服器運行時禁止還原
- ZIP path traversal 防護
- 備份權限
- 排程建立備份

目前 `backup_manager.py` 的預設備份範圍包括：

- `server.properties`
- `eula.txt`
- `ops.json`
- `whitelist.json`
- 封鎖名單
- `world`
- `world_nether`
- `world_the_end`
- `plugins`
- `mods`
- `config`
- `resourcepacks`
- `datapacks`
- `logs`
- `crash-reports`

需要補強：

- 還原前自動建立 `pre-restore` 安全備份
- 備份進度與取消
- 備份範圍選擇
- 保留數量與自動清理
- ZIP 完整性檢查
- 背景任務狀態
- 備份歷史與錯誤頁面

---

## 4.9 Java 與核心管理

目前已完成基礎 Java 管理頁：

- 掃描 `JAVA_HOME`
- 掃描 PATH
- 掃描 Windows 常見 Java 路徑
- 執行 `java -version`
- 顯示 Java 路徑與版本
- 每台伺服器保存 Java 路徑
- Vanilla、Paper、Forge、Fabric 基礎啟動適配

實作位置：

```text
web_panel/operations.py
web_panel/multi_server.py
```

尚未完整：

- Java 與 Minecraft 版本相容性自動判斷
- Java 安裝/下載
- Paper 下載與版本切換
- Fabric Loader 安裝
- Forge 多步驟安裝
- Vanilla Jar 版本管理
- 核心 Hash 驗證
- 核心升級前備份與回滾

注意：Java 掃描應被視為「偵測功能」，不是完整 Java 套件管理器。若下一階段加入下載，必須增加來源驗證、版本鎖定、Hash 與錯誤回滾。

---

## 4.10 進階監控

目前基礎監控已完成：

- Java 程序 CPU
- Java 程序 RSS 記憶體
- 記憶體百分比
- 磁碟使用率
- 磁碟剩餘 GB
- 網路已傳輸 MB
- 網路已接收 MB
- 監控歷史寫入 SQLite
- 最近 24 小時清理
- WebSocket/API 推送

資料表：

```text
resource_metrics
```

API：

```text
GET /api/servers/{profile_id}/metrics
```

尚未完整：

- TPS/MSPT
- 玩家人數歷史圖表
- Java Heap/GC
- 磁碟讀寫速度
- 圖表前端歷史視覺化
- CPU/RAM/磁碟告警
- 伺服器停止告警
- Email/Discord/Webhook 通知

後續監控應避免只把所有資料塞進前端 WebSocket。建議採用：

```text
監控採集器 → SQLite/聚合表 → REST 歷史查詢
                         └→ WebSocket 即時摘要
```

---

## 4.11 玩家管理

目前已完成基礎頁面與指令：

- `list` 輸出解析
- 線上玩家列表
- kick
- ban
- pardon
- op
- deop
- whitelist add/remove 的後端指令基礎
- 玩家操作確認
- 權限檢查
- 稽核呼叫

尚未完整：

- UUID
- 最後登入時間
- 玩家歷史
- 完整解封/白名單/IP 封鎖頁面
- 不同核心輸出格式的穩定解析
- 玩家操作紀錄頁
- 玩家搜尋與排序

玩家管理不能假設所有核心輸出格式完全相同。下一位工程師應先建立 parser 測試資料，涵蓋 Vanilla、Paper、Forge、Fabric 的實際輸出。

---

## 4.12 排程與自動化

目前使用內建背景執行緒與 SQLite：

資料表：

```text
schedules
schedule_runs
```

支援動作：

- start
- stop
- restart
- backup
- command_save_all

目前支援：

- 建立排程
- 固定間隔秒數
- 啟用/停用
- 刪除
- 執行結果保存
- 錯誤細節保存
- 每台伺服器獨立排程

尚未完整：

- Cron/指定星期/指定日期
- 玩家全離線自動停止
- 失敗重試與退避
- 執行歷史頁面
- 啟動完成事件等待
- 排程通知
- 時區與夏令時間處理

重要：排程 route 曾被通用 `/api/servers/{profile_id}/{action}` 攔截，已加入更早註冊的明確 schedule route 修正。未來新增 API 時要注意 FastAPI route 順序。

---

## 4.13 權限模型

權限採用：

```text
使用者 × 伺服器 × 權限鍵
```

非管理員預設拒絕；管理員可通過全域管理權限。

權限涵蓋：

- server.view/start/stop/restart
- console.view/send_command
- files.view/upload/download/create_folder/edit/rename/delete/copy/move/compress/extract
- settings.view/edit/edit_java/edit_startup
- backups.view/create/download/delete/restore
- players.view/kick/ban/unban/op/deop/whitelist
- schedule.view/create/edit/delete
- resources.view

權限變更必須同時確認：

1. HTML 頁面有權限檢查。
2. API route 有權限檢查。
3. WebSocket 不推送未授權伺服器。
4. 使用者無法靠猜測 URL 取得其他伺服器資料。

下一階段需要補做權限穿透測試矩陣，至少測試 viewer/operator/admin 三種角色與兩台伺服器。

---

## 5. 目前未完成項目總表

### P0：建議下一位工程師優先處理

1. 備份還原前自動安全備份。
2. 備份保留策略與過期清理。
3. 排程執行歷史頁面。
4. 玩家操作歷史頁面。
5. 監控歷史圖表。
6. 伺服器停止/啟動逾時告警。
7. 完整跨角色、跨伺服器權限測試。
8. 設定修改前自動備份與變更記錄。

### P1：核心產品功能

1. 插件/模組專用管理頁。
2. `.disabled` 啟用/停用。
3. Java/Minecraft 相容性檢查。
4. Paper/Fabric/Forge 核心安裝與版本管理。
5. TPS/MSPT。
6. 玩家 UUID、白名單、封鎖 IP。
7. Cron/每週排程。
8. 排程重試與啟動完成等待。

### P2：正式遠端部署與安全

1. HTTPS。
2. Tailscale/VPN 引導。
3. Cloudflare Tunnel。
4. 登入限速。
5. 二步驟驗證。
6. 密碼重設。
7. Session 全部登出。
8. API Rate Limit。
9. 完整稽核與報表。

---

## 6. 已知技術風險與需要優化的地方

### 6.1 `main.py` 仍然過大

桌面 UI、Java 下載、伺服器控制、備份、Web 啟動仍有相當多責任集中在 `main.py`。

長期建議拆成：

```text
services/server_runtime.py
services/java_manager.py
services/backup_service.py
services/core_service.py
services/monitor_service.py
ui/legacy_window.py
ui/status_launcher.py
```

Web route 與桌面 UI 都只呼叫 service，不直接操作對方元件。

### 6.2 監控採集與 WebSocket 耦合

目前 `MultiServerManager.status()` 被呼叫時會計算並寫入監控資料。這容易造成：

- 頁面越多，採集次數越多
- WebSocket 與 API 同時查詢時重複採集
- 背景沒有固定採集週期

建議下一版建立獨立 MonitorService，每秒採集一次並快取。

### 6.3 SQLite migration 尚未正式版本化

目前使用 `CREATE TABLE IF NOT EXISTS` 方式補表。短期可用，但長期應建立：

```text
schema_version
migration_001_...
migration_002_...
```

避免未來欄位變更時無法升級既有使用者資料庫。

### 6.4 玩家資料目前依賴文字解析

不同核心、語系與版本的 `list` 輸出可能不同。應建立：

- 原始輸出保存
- parser fixture
- 核心類型分流 parser
- 解析失敗明確顯示

### 6.5 對外連線尚未完成正式安全方案

現在面板預設 localhost，這是合理的安全預設。若使用者要在外面連線，不應只把 `8765` 直接 port forward。

建議正式支援順序：

1. Tailscale。
2. HTTPS 反向代理。
3. Cloudflare Tunnel。
4. 最後才考慮直接公開 port，並必須搭配限速、白名單與 HTTPS。

### 6.6 任意 Minecraft 指令的風險

目前操作員有能力送出 Minecraft console command。後續應加入：

- 高風險指令清單
- 管理員二次確認
- 指令白名單
- 指令長度/頻率限制
- 完整稽核記錄

---

## 7. 開發與測試方式

### 7.1 安裝依賴

```powershell
python -m pip install -r requirements.txt
```

### 7.2 執行測試

```powershell
python -m unittest discover -s tests -v
```

目前既有測試檔：

```text
tests/test_server_manager.py
tests/test_v2_features.py
tests/test_web_panel_services.py
```

最近驗證結果：

```text
11 tests passed
```

### 7.3 語法檢查

```powershell
python -m compileall -q main.py web_panel tests
python -m unittest discover -s tests -q
git diff --check
```

### 7.4 本地執行

```powershell
python main.py
```

預設網址：

```text
http://127.0.0.1:8765
```

### 7.5 Windows EXE 建置

建置由 GitHub Actions 處理：

```text
.github/workflows/build-windows.yml
```

手動觸發：

```bash
gh workflow run build-windows.yml --ref main
```

查看結果：

```bash
gh run list --workflow build-windows.yml --limit 5
gh run watch <RUN_ID> --exit-status --interval 10
```

下載 artifact：

```bash
gh run download <RUN_ID> --name Minecraft-Server-Manager-windows --dir artifacts
```

### 7.6 Smoke Test 驗收

Workflow 會建立 console diagnostic EXE，啟動後等待：

```text
http://127.0.0.1:8765/login
```

並確認頁面包含首次建立管理員文字。

---

## 8. 交接時的手動驗收清單

下一位工程師接手後，應依序完成：

### 基本啟動

- [ ] 從乾淨資料夾啟動 EXE。
- [ ] 確認簡化桌面視窗顯示。
- [ ] 確認 `127.0.0.1:8765` 可開啟。
- [ ] 確認首次建立管理員流程。
- [ ] 確認關閉程式不會無預期刪除資料。
- [ ] 確認 `--legacy-ui` 仍可啟動舊 UI。

### 多伺服器

- [ ] 建立兩台伺服器 Profile。
- [ ] 分別指定 Fabric 與 Paper。
- [ ] 確認兩台伺服器可以分別啟動/停止。
- [ ] 確認一台伺服器的檔案不會被另一台看到。
- [ ] 確認首頁卡片只保留入口資訊。
- [ ] 確認每台伺服器只有一個「伺服器設定」入口。

### 權限

- [ ] 建立 admin、operator、viewer。
- [ ] 只把 viewer 指派給伺服器 A。
- [ ] 確認 viewer 看不到伺服器 B。
- [ ] 確認 viewer 不能啟動、停止、發指令、上傳、備份還原。
- [ ] 確認 operator 只能操作被分配的功能。
- [ ] 確認 WebSocket 不洩漏未授權伺服器。

### 備份

- [ ] 伺服器停止時建立備份。
- [ ] 伺服器運行時確認還原被拒絕。
- [ ] 下載備份。
- [ ] 刪除備份。
- [ ] 測試惡意 ZIP path traversal。
- [ ] 確認還原失敗時不會破壞伺服器資料。

### 排程

- [ ] 建立 60 秒測試排程。
- [ ] 確認排程執行。
- [ ] 確認停用後不再執行。
- [ ] 確認刪除排程。
- [ ] 確認不同帳號不能跨伺服器修改排程。
- [ ] 確認排程錯誤會被保存。

### 監控

- [ ] 啟動 Java 伺服器後 CPU/RAM 不為固定 0。
- [ ] 確認停止後狀態轉為 stopped。
- [ ] 確認磁碟與網路欄位可取得。
- [ ] 確認 SQLite 有 `resource_metrics` 資料。
- [ ] 確認 WebSocket 斷線後會重連。

### 玩家

- [ ] 執行 `list`。
- [ ] 確認玩家名稱解析。
- [ ] 測試 kick/ban/op。
- [ ] 確認高風險操作需要確認。
- [ ] 確認操作會寫入稽核資料。

---

## 9. 下一位工程師建議工作切分

### Sprint 1：可靠性與資料安全

- 備份還原前安全備份。
- 備份完整性檢查。
- 備份保留策略。
- SQLite migration 版本化。
- 權限矩陣整合測試。
- 設定變更稽核。

### Sprint 2：監控與排程完整化

- MonitorService 獨立採集。
- 圖表前端。
- TPS/MSPT adapter。
- 排程 run history 頁。
- Cron/每週排程。
- 告警模型與通知接口。

### Sprint 3：插件、模組與核心管理

- 插件/模組列表。
- 啟用/停用。
- 核心下載來源抽象化。
- Paper/Fabric/Forge 安裝器。
- Hash、版本與回滾。

### Sprint 4：正式遠端安全

- HTTPS 反向代理文件與檢查。
- Tailscale/VPN 指引。
- 登入限速。
- 二步驟驗證。
- 密碼重設。
- Session 管理。
- 稽核報表。

---

## 10. 交接注意事項

1. 不要直接刪除 `web_panel.db`，裡面有帳號、權限、Session、排程與監控資料。
2. 不要把使用者輸入的伺服器資料夾直接傳給 `subprocess` 或檔案 API，必須透過 Profile 與 `SafeFileService`。
3. 不要把所有啟停邏輯重新寫在 route；請使用 `MultiServerManager`。
4. 不要讓 Web route 直接操作 Tkinter widget。
5. 不要把 Web Port `8765` 與 Minecraft 遊戲 Port `25565` 混為一談。
6. 修改路由時注意 FastAPI route 註冊順序，具體路徑應放在通用動態路徑前面。
7. 新增權限時要同步修改：權限定義、資料庫、route、頁面顯示與測試。
8. 新增模板時要確認 PyInstaller `--add-data "web_panel/templates;web_panel/templates"` 可以打包。
9. Windows GUI EXE 不應依賴 console stdout；Uvicorn 已使用 `log_config=None` 避免 formatter 讀取空 stdout。
10. 每次修改後都要跑 Python 測試、compileall、diff check，並重新執行 Windows Smoke Test。

---

## 11. 交接結論

目前專案已經具備可執行的多伺服器 Web 管理面板基礎：登入、帳號、細部權限、伺服器 Profile、啟停、主控台、檔案、設定、備份、Java 掃描、玩家、排程與基礎監控都已存在。

下一位工程師的主要任務不是重做 MVP，而是：

1. 把基礎功能變成可長期運行的可靠功能。
2. 補齊備份安全、監控圖表、排程歷史與玩家資料完整性。
3. 建立插件/模組與核心版本管理。
4. 補足正式遠端使用的安全防護。
5. 把目前集中在 `main.py` 的責任拆成清楚的服務層。
6. 擴大權限、跨伺服器隔離與資料遷移測試。

目前最新版本可以作為下一階段開發基線，最新 Windows 執行檔與 Git `main` 分支內容一致，並已通過現有測試與 Windows EXE Smoke Test。
