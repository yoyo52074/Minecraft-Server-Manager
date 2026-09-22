# 更新日誌

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
