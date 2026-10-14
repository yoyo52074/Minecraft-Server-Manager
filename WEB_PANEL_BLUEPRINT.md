# Minecraft Server Manager — 網頁管理面板藍圖

> 文件版本：v1.0  
> 目前基準：Minecraft Server Manager v2.4.3  
> 狀態：設計討論稿，尚未實作

## 1. 結論

這項功能可行，而且可以保留現有的 Windows 桌面版啟動器，新增一個**可選擇啟用的繁體中文網頁管理面板**。

建議不要讓 Web 頁面直接操作 Tkinter 元件，也不要把 `main.py` 的方法直接暴露給網路。正確做法是先把伺服器程序、主控台、設定、備份等能力抽成共用的後端服務，讓桌面 UI 與 Web UI 都透過同一層服務操作。

```text
┌──────────────────────────────────────────┐
│ Minecraft Server Manager                  │
│                                          │
│  Tkinter 桌面 UI ─────┐                  │
│                       ├─ Server Service ─┼─ Java Minecraft Process
│  Web UI / REST API ───┘                  │
│          │                               │
│          ├─ 使用者、角色、Session        │
│          ├─ WebSocket 即時主控台          │
│          ├─ 設定、備份、稽核記錄          │
│          └─ SQLite 資料庫                 │
└──────────────────────────────────────────┘
```

## 2. 目前專案狀況

已檢視 GitHub 專案 `yoyo52074/Minecraft-Server-Manager` 的 `main` 分支與 v2.4.3：

- 技術：Python、Tkinter/ttkbootstrap、requests。
- 目前是單機桌面應用，依賴只有 `requests` 與 `ttkbootstrap`。
- `main.py` 的 `ApplicationController` 同時負責 UI 綁定、啟動/停止 Java、讀取主控台、設定及備份。
- `server_manager.py` 目前主要負責核心版本、下載與 `server.properties`，尚未負責程序生命週期。
- Java 程序目前在 `main.py` 直接以 `subprocess.Popen` 建立；標準輸出由背景執行緒讀取。
- 現有設定已有 `server-port`，預設 Minecraft 連接埠通常是 `25565`；網頁面板應使用另一個連接埠，例如 `8765`。
- 現有 UI 已經是繁體中文，因此 Web UI 可沿用相同詞彙與設定名稱。
- 現有測試已涵蓋核心下載、設定解析、備份還原、路徑穿越防護與核心完整性驗證，但尚未有 Web/API/帳號測試。

## 3. 目標與非目標

### 第一階段目標（MVP）

1. 在桌面程式中可啟用/停用 Web 面板。
2. 可設定 Web 監聽位址與連接埠，例如 `127.0.0.1:8765` 或 `0.0.0.0:8765`。
3. 使用者必須登入後才可使用面板。
4. 管理員可建立、停用、刪除使用者並設定角色。
5. 支援繁體中文介面。
6. 顯示伺服器狀態、運行時間、Java/Minecraft 資訊。
7. 遠端啟動、停止、重啟伺服器。
8. 即時查看主控台並發送 Minecraft 指令。
9. 讀取與修改常用 `server.properties`。
10. 建立備份與查看備份清單。
11. 重要操作寫入稽核記錄。

### 第一階段先不做

- 不直接在第一版做完整 Pterodactyl 節點/容器編排。
- 不在網頁上執行任意 Windows shell 指令。
- 不讓不同使用者任意指定電腦上的任意資料夾。
- 不先承諾內建 HTTPS 憑證與自動 DDNS；第一版建議配合反向代理、Tailscale 或 Cloudflare Tunnel。
- 不先做多台主機叢集；先以「一個桌面程式管理一個 Minecraft Server 目錄」為基礎。

## 4. 建議技術方案

### 後端

- **FastAPI**：提供 REST API、表單登入與 WebSocket。
- **Uvicorn**：在桌面程式內以背景執行緒啟動 Web 服務。
- **SQLite**：保存帳號、角色、Session、設定與稽核記錄，適合單機部署。
- **Argon2id**：儲存密碼雜湊，不保存明文密碼。
- **Signed session cookie**：第一版使用伺服器端 Session + HttpOnly Cookie；避免把高權限 Token 放在 localStorage。
- **Jinja2 + 少量 JavaScript**：第一版可避免導入 Node/前端編譯環境，降低 Windows 打包複雜度。介面採響應式 CSS，手機與桌面瀏覽器都能用。
- **WebSocket**：主控台採即時日誌串流；API 仍保留目前狀態查詢。

### 前端頁面

- `/login`：登入。
- `/`：伺服器總覽、狀態、啟動/停止/重啟。
- `/console`：即時主控台、發送指令。
- `/settings`：常用與進階 `server.properties`。
- `/backups`：建立、列出、還原備份；還原前必須確認且伺服器須停止。
- `/admin/users`：僅管理員可見，建立/停用/刪除帳號與角色管理。
- `/admin/audit-logs`：僅管理員可見，查詢登入與危險操作。
- `/admin/web`：管理監聽位址、連接埠、是否啟用面板。

## 5. 權限模型

建議先採三種角色，避免一開始做過度複雜的自訂 ACL：

| 角色 | 可做的事 |
|---|---|
| 管理員 `admin` | 所有功能、使用者管理、Web 設定、稽核記錄 |
| 操作者 `operator` | 查看狀態、啟停/重啟、主控台、發送白名單內的管理指令、修改一般伺服器設定、建立備份 |
| 查看者 `viewer` | 查看狀態、唯讀主控台與唯讀設定 |

### 指令安全

Web 端不應提供「任意系統命令」欄位。Minecraft 指令可先採兩種模式：

1. MVP：只允許管理員與操作者發送 Minecraft console command，並記錄完整指令。
2. 強化版：建立允許/禁止前綴清單，例如允許 `say`、`whitelist`、`save-all`、`tell`，對 `op`、`deop`、`stop`、`ban-ip` 等高風險指令要求管理員或二次確認。

## 6. 設定與首次啟動流程

### 桌面端

在現有設定頁新增「網頁管理面板」卡片：

- 啟用 Web 面板。
- 監聽位址：
  - `127.0.0.1`：只有本機可連線，最安全。
  - `0.0.0.0`：區域網路/外部可連線，但必須搭配防火牆、HTTPS 或安全隧道。
- Web 連接埠：預設 `8765`。
- Web 面板語言：預設繁體中文，預留未來英文。
- 啟動時自動啟動 Web 面板：預設開啟。
- 顯示目前存取網址與安全提示。

### 首次啟動

1. 第一次啟動若沒有管理員帳號，桌面端顯示本機初始化頁。
2. 使用者設定第一組管理員帳號與密碼。
3. 後續每次啟動 Web 面板前檢查資料庫與 migration。
4. 若設定為對外監聽，顯示明確警告：未使用 HTTPS/隧道時，不建議直接暴露在公網。

## 7. 程式重構方案

### 目前問題

`ApplicationController` 把 Tkinter UI 與伺服器邏輯綁在一起，例如：

- `start_server()` / `_actually_start_server()`：啟動流程與 messagebox/UI 狀態混合。
- `read_server_output()`：直接把 Java 輸出寫入 Tkinter。
- `stop_server()`：直接操作 `stdin`。
- 設定與備份流程也直接從 UI callback 呼叫。

Web 執行緒不能直接碰 Tkinter widget，因此需要抽層。

### 建議新模組

```text
web_panel/
  __init__.py
  app.py                 # FastAPI app 與路由組裝
  config.py              # Web 設定與環境檢查
  database.py            # SQLite 連線與 migration
  models.py              # User、Session、AuditLog
  auth.py                # 密碼雜湊、登入、session、CSRF
  permissions.py         # admin/operator/viewer 權限檢查
  routes_auth.py
  routes_dashboard.py
  routes_console.py
  routes_settings.py
  routes_backups.py
  routes_admin.py
  websocket.py           # 主控台廣播
  templates/              # 繁體中文 Jinja2 模板
  static/                 # CSS、JavaScript、圖示

services/
  server_runtime.py      # Java 程序生命週期與狀態
  console_buffer.py      # 日誌緩衝、訂閱與 WebSocket 廣播
  server_service.py      # 啟動前診斷、設定、備份等用例
```

`ApplicationController` 與 Web route 都只依賴 `ServerService`/`ServerRuntime`，不互相呼叫。

### 必要的共用 API（概念）

```python
runtime.start() -> Result
runtime.stop() -> Result
runtime.restart() -> Result
runtime.status() -> ServerStatus
runtime.send_console_command(command: str) -> Result
runtime.subscribe_logs(callback)
runtime.get_recent_logs(limit: int) -> list[str]
```

所有操作都必須有鎖與狀態檢查，避免桌面端與 Web 端同時啟動、同時停止，或同時還原備份。

## 8. 網路與安全設計

### 預設安全策略

- 預設監聽 `127.0.0.1`，預設連接埠 `8765`。
- 啟用 Web 面板時不要自動開 Windows 防火牆入站規則，除非未來另做明確設定流程。
- Cookie 設定 `HttpOnly`、`SameSite=Lax`；HTTPS 時加上 `Secure`。
- 登入失敗限速；管理員操作寫入稽核記錄。
- 所有會改變伺服器狀態的 POST/DELETE 操作檢查 CSRF token。
- 密碼至少 12 字元，Argon2id 雜湊。
- 伺服器路徑固定來自本機設定，不接受 Web 使用者傳入任意路徑。
- 備份還原沿用現有 path traversal 防護，並在還原前停止伺服器。
- 不把密碼、Session secret、完整 Cookie 寫入日誌。

### 出門在外連線的建議順序

1. **最推薦：Tailscale/ZeroTier**，只讓自己的私有網路存取 `127.0.0.1` 以外的服務。
2. **次推薦：Cloudflare Tunnel 或其他 HTTPS 反向代理**，由外部服務處理 HTTPS。
3. **不推薦：直接把 `8765` port forward 到公網**。若一定要做，至少使用 HTTPS 反向代理、防火牆白名單、強密碼與限速。

Minecraft 遊戲連接埠與 Web 管理連接埠是兩個不同服務：

- Minecraft：通常 `25565/TCP`。
- Web panel：例如 `8765/TCP`。

## 9. 實作分期

### Phase 0：設計與基礎測試

- 確認「單伺服器/單主機」模型。
- 定義角色、權限與安全預設。
- 增加 Web 依賴與測試工具。
- 建立 `ServerRuntime` 介面，不改變現有桌面功能。

### Phase 1：MVP Web 面板

- FastAPI 在桌面程式中啟停。
- SQLite migration。
- 首次建立 admin。
- 登入/登出/session/CSRF。
- 繁體中文總覽、狀態、啟停、主控台。
- WebSocket 即時日誌。
- 桌面端 Web 面板設定卡片。

### Phase 2：管理功能

- 使用者建立、停用、刪除、重設密碼。
- admin/operator/viewer 權限。
- `server.properties` 常用/進階設定。
- 備份清單、建立備份、停止後還原。
- 稽核記錄。

### Phase 3：遠端部署與強化

- HTTPS 反向代理/安全隧道文件與檢查。
- 登入限速、Session 失效、備份策略。
- 多伺服器 profile（若確實有需求）。
- 更細的指令白名單與自訂權限。
- Windows 打包與 Release 流程整合。

## 10. 驗收條件

### 功能

- 未登入訪問 `/` 會導向 `/login`。
- admin 能建立一位 operator；operator 看不到使用者管理頁。
- viewer 不能啟動、停止、重啟、發送指令或修改設定。
- 桌面 UI 啟動伺服器後，Web 頁面在 1 秒內反映狀態；反向亦然。
- WebSocket 可看到新的伺服器主控台輸出，斷線重連後可取得最近 N 行。
- 同一時間重複點擊啟動只會產生一個 Java 程序。
- 伺服器運行時不能還原備份。
- Web 面板關閉不會誤停掉 Minecraft，除非明確設定為關閉時一併停止。

### 安全

- 密碼資料庫中沒有明文密碼。
- CSRF、未授權角色與停用帳號測試通過。
- `../`、絕對路徑與 ZIP path traversal 測試通過。
- 高風險操作均有稽核記錄。
- 預設只監聽 localhost，且不自動暴露公網。

## 11. 需要你在實作前確認的選擇

建議採用以下預設即可開始：

1. **部署模型**：一台電腦上的一個 Minecraft Server Manager，先管理一個伺服器目錄。
2. **Web 技術**：FastAPI + Jinja2/少量 JavaScript + SQLite。
3. **預設 Web 位址**：`127.0.0.1:8765`。
4. **角色**：`admin`、`operator`、`viewer`。
5. **遠端存取**：先完成面板本身，再提供 Tailscale/Cloudflare Tunnel 的安全部署方式，不直接鼓勵裸 port forward。
6. **程式行為**：桌面 UI 與 Web UI 共用同一個伺服器控制服務。

若上述方向確認，下一步就會先實作 Phase 0 + Phase 1，先保證現有桌面版功能不被破壞，再逐步加入管理員帳號與使用者權限。
