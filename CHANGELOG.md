# 更新日誌

## [v2.0.1] - 2026-09-23

### 修正

- 修正程式啟動時無條件先下載 Java 17 的問題。
- 現在會先讀取核心 metadata 與 Minecraft 版本，再決定需要 Java 17、21 或 25。
- 修正 Paper `26.1` 這類檔名解析可能抓到 build number，導致錯誤判定為 Java 17 的問題。
- 舊版沒有正確 metadata 時，也會從核心檔名嘗試解析 Minecraft 版本。

## [v2.0] - 2026-09-23

### 新功能

- 新增安裝精靈入口，集中引導核心選擇、Java 檢查、下載與 EULA 設定。
- 新增啟動前診斷，檢查 Java、核心檔案、EULA、記憶體與伺服器連接埠。
- 新增核心 metadata，保存核心、Minecraft 版本、Java 需求與 SHA-256。
- 新增核心啟動前 SHA-256 驗證，以及核心升級前自動備份。
- 新增世界、插件與伺服器設定的一鍵 ZIP 備份與安全還原。
- 伺服器設定視窗新增搜尋功能，方便快速找到設定項目。
- 主畫面新增伺服器核心、版本、Java 與運行時間狀態卡。

### 技術整理

- 新增 `backup_manager.py`、`core_metadata.py` 與 `diagnostics.py` 模組。
- 新增 v2.0 功能的自動化回歸測試。

## [v1.8] - 2026-09-23

### 介面優化

- 新增伺服器狀態卡，集中顯示核心、Minecraft 版本、Java 版本與運行時間。
- 啟動後顯示即時運行時間，停止後自動清除。
- 核心與版本選擇、安裝完成及伺服器檢查結果會即時同步到狀態卡。
- 改善狀態文字可讀性，使用狀態指示符號區分目前階段。

## [v1.7.3] - 2026-09-23

### 修正

- 修正 Minecraft 26.1 以上版本需要 Java 25，但程式仍固定使用 Java 17 的問題。
- 啟動伺服器前會檢查核心版本與 Java 主版本；版本不足時會自動提示下載相容的 Java。
- Java 下載完成後會自動繼續啟動原本選擇的伺服器。

## [v1.7.2] - 2026-09-23

### 修正

- 修正 Windows EXE 啟動時 `cannot import name 'ttk' from 'ttkbootstrap'` 的問題，改用 ttkbootstrap 2.x 的公開 API。
- 更新 `ttkbootstrap` 最低版本要求至 2.0.0。

## [v1.7.1] - 2026-09-23

### 發布

本版本新增 GitHub Actions Windows 建置流程，會使用 PyInstaller 自動產生單檔 GUI `.exe`，並將 `playit.exe`、程式圖示與 `ttkbootstrap` 資源一併打包至 GitHub Release。

## [v1.7] - 2026-09-23

### 修正

- 修正 PaperMC v2 API 已 sunset 導致 Paper 版本列表與核心下載失效的問題，改用 PaperMC Fill v3 Downloads Service。
- Paper 下載請求加入符合官方要求的 User-Agent。
- 修正下載中斷後留下損壞 `.jar` 的問題，改用 `.part` 暫存檔並在下載完成後原子替換。
- 修正 EULA 只檢查檔案存在的問題，現在必須確認內容為 `eula=true` 才會啟用啟動按鈕。
- 修正伺服器輸出與 Java 下載背景執行緒直接操作 Tkinter 介面的問題。
- 修正替換核心時先刪除舊核心的問題；只有新核心成功下載後才清理舊核心。
- 修正多個伺服器核心 `.jar` 時可能隨機啟動錯誤核心的問題。
- 修正關閉視窗時，即使使用者取消確認仍會關閉 Playit 通道的問題。
- 修正停止 Playit 時使用全域 `taskkill /IM playit.exe`，改為只終止本工具啟動的程序。
- 修正 `server.properties` 遇到沒有等號的空白或異常行時中止解析的問題。

### 文件

- 新增 `requirements.txt`，列出 `requests` 與 `ttkbootstrap` 依賴。
