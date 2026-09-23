# Minecraft 伺服器架設工具 - Produced by yoyo


歡迎使用！這是一個使用 Python 打造的 Minecraft 伺服器圖形化管理工具，致力於簡化 Minecraft Java 版伺服器的架設流程，讓任何人都能輕鬆地與朋友同樂。

---

## 📥 下載

您可以隨時從我們的 **Releases 頁面**下載最新版本：

**[➡️ 點此前往下載最新版 (v2.1)](https://github.com/yoyo52074/Minecraft-Server-Manager/releases/latest)**

Windows 使用者可直接從 Release 下載 `Minecraft-Server-Manager.exe`，不需要另外安裝 Python。

---

## ✨ 主要功能 (Features)

* **圖形化介面**：所有操作都在視窗內完成，直覺好上手。
* **核心下載**：支援 Paper 伺服器核心，可自動抓取最新的遊戲版本。
* **一鍵安裝**：自動處理伺服器首次啟動、下載核心檔案、同意 EULA 等繁瑣步驟。
* **Playit.gg 整合**：內建 Playit.gg 穿隧功能，打包即用，無需手動下載，輕鬆實現外網連線。
* **完全獨立**：打包好的 `.exe` 檔已包含所有必要元件，真正的「單一檔案」綠色軟體。
* **Java 環境偵測**：若使用者的電腦未安裝 Java，程式會引導使用者自動下載。
* **安裝精靈**：以步驟化流程引導核心選擇、Java 檢查、EULA 與首次安裝。
* **啟動前診斷**：啟動前檢查 Java、記憶體、EULA、核心檔案與連接埠。
* **備份與還原**：支援世界、插件與伺服器設定的一鍵 ZIP 備份與安全還原。
* **核心完整性驗證**：保存核心 metadata 並支援 SHA-256 校驗。
* **深色專業介面**：使用側邊導覽列分離總覽、安裝、控制台與設定頁面。

---

## 🚀 如何執行 (How to Run)

### 從原始碼執行

請先安裝 Python 3.10 或更新版本，再執行：

```bash
pip install -r requirements.txt
python main.py
```

完整變更請參閱 [CHANGELOG.md](CHANGELOG.md)。

當您第一次執行程式時，可能會看到 Windows SmartScreen 的安全提示。這是正常的保護機制。

1.  在藍色提示畫面中，點擊「**其他資訊**」。
2.  接著點擊新出現的「**仍要執行**」按鈕，程式即可順利啟動！

---

© 廢人伺服器 版權所有
