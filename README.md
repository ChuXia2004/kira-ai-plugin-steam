# Kira-AI-plugin-steam

KiraAI 的 Steam 集成插件，提供商店搜索、游戏库存、好友列表、社区市场价格与游戏内物品（饰品/皮肤）查询等能力。

## ✨ 功能特性

- 🔍 **商店搜索**：按关键词搜索 Steam 商店游戏，返回价格、折扣与简介
- 🎮 **游戏库存**：查询用户拥有的游戏列表及游玩时长
- 👥 **好友列表**：查询好友在线状态与当前游玩游戏
- 🧑 **个人资料**：获取 Steam 用户昵称、状态、国家、注册时间等
- 💰 **市场价格**：查询社区市场物品当前售价、最低挂单价与 24h 成交量
- 📊 **历史成交**：查询市场物品近期成交记录

## 📦 文件结构

```
kira-ai-plugin-steam/
├── manifest.json      # 插件清单（插件 ID、版本、描述）
├── main.py            # 插件主代码
├── requirements.txt   # Python 依赖
├── schema.json        # 配置项 schema（插件配置面板）
└── README.md          # 本文档
```

## 🚀 安装

1. 将本仓库 clone 到 KiraAI 的 `plugins/` 目录：

```bash
git clone https://github.com/ChuXia2004/kira-ai-plugin-steam.git
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 在配置面板中填写 `Steam API Key` 和 `SteamID (64位)`。

## ⚙️ 配置

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `api_key` | sensitive | 空 | Steam API Key，从 [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) 获取 |
| `steam_id` | string | 空 | 你的 SteamID64， `76561198000000000` |
| `enabled` | switch | true | 是否启用插件 |
| `proxy_enabled` | switch | false | 启用代理（解决国内访问超时） |
| `proxy_url` | string | `http://127.0.0.1:7890` | 代理地址，仅启用代理时生效 |
| `cache_ttl` | int | 300 | API 请求缓存时间（秒），范围 60~86400 推荐600 |

## 🛠 工具（LLM 自动调用）

| 工具名 | 说明 |
| --- | --- |
| `steam_search_games` | 搜索商店游戏 |
| `steam_inventory` | 查询用户游戏库存 |
| `steam_friends` | 查询好友列表 |
| `steam_player_summary` | 查询用户资料 |
| `steam_market_price` | 查询市场物品价格（需精确物品名称） |
| `steam_market_history` | 查询市场物品历史成交记录 |

常用游戏缩写：`csgo/cs=730`、`dota2/dota=570`、`tf2=440`、`pubg=578080`。

## 💬 斜杠命令

```
/steam market <物品名>    查询市场价格
/steam inventory         查询我的游戏库存
/steam friends           查询好友列表
/steam me                查询我的个人资料
/steam search <关键词>    搜索商店游戏
/steam help              显示帮助
```

示例：

```
/steam market AK-47 | Redline
/steam market csgo:AK-47 | Redline
/steam search 空洞骑士
```

## 📝 注意事项

-Steam API Key（钥匙）
  1. 登录：用你的 Steam 账号登录上述网站（账号必须有消费      
  记录，受限账号不行）
  2. 填域名：页面有个“Domain Name”输入框，填 localhost 
  或你的网站域名
  3. 注册：点击“Register”，页面刷新后就会生成一串密钥
  4. 复制：把那一串字符（类似 
  ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890）复制出来
  · 这是“钥匙”，绝对不能公开分享
  · 存放在本地配置文件 data/config/plugins/
  steam.json 里，只有你能看到

-SteamID64
  登录steam打开个人资料看浏览器地址栏  
https://steamcommunity.com/profiles/76561198000000000/ 你的steamID64`76561198000000000`
- 查询库存时如遇 HTTP 429，说明 Steam 限流，请等待 1-2 分钟再试；插件已内置同一目标 3 秒间隔的限流保护。
- Steam API 国内访问可能超时，建议开启代理。

## 📄 License

AGPL-3.0 许可证