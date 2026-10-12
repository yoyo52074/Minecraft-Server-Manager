# Java 相容版本

管理器會依 Minecraft 版本選擇 Java。舊版伺服器會固定使用對應的 Java 主版本，避免只因電腦裝了較新的 Java，就錯誤地把它當成可用版本。Java 8 的舊式版本字串（例如 `1.8.0_452`）也會正確辨識為 Java 8。

| Minecraft 版本 | Paper／一般核心的 Java 相容目標 |
|---|---|
| 1.7.10–1.11 | Java 8，主版本需相同 |
| 1.12–1.16.4 | Java 11，主版本需相同 |
| 1.16.5 | Java 16，主版本需相同 |
| 1.17–1.20.4 | Java 17 或更新版本 |
| 1.20.5–1.21.x | Java 21 或更新版本 |
| 26.1 及更新版本 | Java 25 或更新版本 |

PaperMC 建議 1.20–1.21.11 使用 Java 21；管理器對 1.20.0–1.20.4 保留 Java 17 的版本需求下限，並從 1.20.5 起改為 Java 21。Minecraft 1.20.5 官方更新說明明確指出該版開始需要 Java 21。若要遵循 PaperMC 對舊 1.20.x 的建議，也可手動指定 Java 21。[PaperMC Java 版本表](https://docs.papermc.io/paper/getting-started/) [Minecraft 1.20.5 更新說明](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-20-5)

**Forge 有額外例外：1.13 以前的 Forge（包含 Forge 1.12.2）必須使用 Java 8。** PaperMC 的表格是 Paper 建議，不應套用到舊 Forge。管理器現在依核心類型計算 Java 需求，Forge 1.12.2 即使偵測到 Java 11 或更新版本，也會改用／自動準備 Java 8。[Forge 論壇：pre-1.13 Forge only works with Java 8](https://forums.minecraftforge.net/topic/73597-starting-forge-1122-with-java-11-solved/)

若 Java 8、11 或 16 的伺服器目前指向不同主版本，網頁面板會搜尋相符安裝；找不到時自動下載並套用 Temurin 對應版本，然後重試啟動。桌面管理器則會提示是否下載相符版本。Java 17 及以上的 Minecraft 版本採最低需求判斷，較新的 Java 仍可使用。
