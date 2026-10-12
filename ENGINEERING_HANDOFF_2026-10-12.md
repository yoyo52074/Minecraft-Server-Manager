# Minecraft Server Manager 工程師交接書

**日期**：2026-10-12  
**分支**：`agent/backup-history-alerts`  
**專案狀態**：Web 面板與桌面版已完成一輪 Forge 1.12.2 相容性修正與回歸驗證；準備推送並觸發最新版 Windows 部署。

## 目前已完成的重點

### 1. Forge 1.12.2 的 Java 相容性修正

已修正舊版 Forge 的 Java 規則。原本系統把 Minecraft 1.12.2 當成一般核心版本處理，導致它被套用到 Java 11 的規則。實際上，**Forge 1.12.2 與所有 pre-1.13 Forge 都必須使用 Java 8**。

本次調整內容：
- 新增核心類型相依的 Java 需求函式。
- Forge 1.12.2 與更舊 Forge 版本強制使用 Java 8。
- Paper／一般核心仍沿用既有 Minecraft 版本相容表。
- Web 面板與桌面版都改成使用相同規則。

### 2. 啟動器與核心選擇修正

- Forge 啟動時優先選擇 Forge 專用 JAR / `run.sh` / `run.bat`。
- 不再讓一般的 `server.jar` 先被誤選，避免舊 Forge 伺服器啟動到錯誤核心。
- Web 面板與桌面版的核心檔案篩選邏輯同步更新。

### 3. Java 自動修復流程

- 當 Forge 1.12.2 只找到較新的 Java 時，系統會自動要求或補裝 Java 8。
- Web 啟動 API 會把 Java 8 自動修復任務排進去。
- 桌面版會在安裝 Forge 時先檢查 Java 版本，不符合時會先補 Java，再續接 Forge 安裝。

### 4. 測試與驗證

本輪已完成的驗證結果：
- 單元測試：**118 項通過，1 項在 sandbox 因無 `tkinter` 而跳過**。
- `main.py`、`web_panel/app.py`、`web_panel/core_update.py`、`web_panel/multi_server.py`、測試檔案皆可通過語法檢查。
- `git diff --check` 已通過。

### 5. 文件更新

- 已更新 `JAVA_COMPATIBILITY.md`，說明：
  - Paper 與一般核心的 Java 規則。
  - Forge 1.12.2 需要 Java 8。
  - 舊 Forge 不應套用 Paper 的 Java 建議表。

## 目前狀態

- 工作樹尚未提交，變更都保留在本地。
- 目前分支：`agent/backup-history-alerts`
- 下一步是：**提交 → 推送 → 觸發 Windows release build → 下載 EXE 檢查**。

## 部署注意事項

1. 若現有伺服器是 Forge 1.12.2，且先前被寫入過 Java 11 的設定，請重新觸發一次 Java 自動設定或讓系統重抓 Java 8。
2. 如果伺服器資料夾內已有舊版 `server.jar`，新版邏輯會優先尋找 Forge launcher / Forge JAR，不會再誤把原版核心當成 Forge 核心。
3. 桌面版測試在此 sandbox 沒有 `tkinter`，所以該測試自動跳過；這不影響 Windows 版實際執行。

## 建議後續工作

- 提交並推送目前變更。
- 重新觸發 Windows build。
- 若 build 成功，將新的 EXE 交付下載連結回報給使用者。
- 如果使用者仍回報 Forge 1.12.2 無法啟動，請優先檢查：
  - 伺服器目錄是否真的存在 `forge-1.12.2-...-universal.jar` 或 Forge 產生的 `run.sh` / `run.bat`。
  - `core-info.json` 是否仍殘留舊版 Java 設定。
  - 啟動時是否真的切到 Java 8，而不是 PATH 中的 Java 11/17/21/25。
