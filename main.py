import asyncio
import json
import re
import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

import aiohttp
from aiohttp.resolver import ThreadedResolver
from bs4 import BeautifulSoup

from core.plugin import BasePlugin, on, Priority
from core.provider import LLMRequest
from core.utils.tool_utils import BaseTool
from core.logging_manager import get_logger
from core.chat.message_utils import KiraMessageEvent, KiraMessageBatchEvent
from core.chat import MessageChain
from core.chat.message_elements import Text

logger = get_logger("steam", "cyan")


# ============================================================
# 工具类（每个工具继承 BaseTool）
# ============================================================

class SteamSearchGamesTool(BaseTool):
    """搜索 Steam 商店游戏"""
    name = "steam_search_games"
    description = "搜索 Steam 商店游戏，返回游戏名称、价格、简介等"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，如游戏名称"}
        },
        "required": ["query"],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, query: str, *args, **kwargs) -> str:
        return await self.plugin._search_games(query)


class SteamInventoryTool(BaseTool):
    """查询 Steam 游戏库存"""
    name = "steam_inventory"
    description = "查询指定用户的 Steam 游戏库存列表"
    parameters = {
        "type": "object",
        "properties": {
            "steam_id": {"type": "string", "description": "SteamID64，不填则使用配置的默认值"},
            "app_id": {"type": "string", "description": "可选：指定游戏 AppID"}
        },
        "required": [],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, steam_id: str = None, app_id: str = None, *args, **kwargs) -> str:
        return await self.plugin._get_inventory(steam_id, app_id)


class SteamFriendsTool(BaseTool):
    """查询 Steam 好友列表"""
    name = "steam_friends"
    description = "查询指定用户的 Steam 好友列表"
    parameters = {
        "type": "object",
        "properties": {
            "steam_id": {"type": "string", "description": "SteamID64，不填则使用配置的默认值"},
            "include_profile": {"type": "boolean", "description": "是否包含好友资料，默认 true"}
        },
        "required": [],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, steam_id: str = None, include_profile: bool = True, *args, **kwargs) -> str:
        return await self.plugin._get_friends(steam_id, include_profile)


class SteamPlayerSummaryTool(BaseTool):
    """获取 Steam 用户资料"""
    name = "steam_player_summary"
    description = "获取 Steam 用户的个人资料（昵称、头像、状态等）"
    parameters = {
        "type": "object",
        "properties": {
            "steam_id": {"type": "string", "description": "SteamID64，不填则使用配置的默认值"}
        },
        "required": [],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, steam_id: str = None, *args, **kwargs) -> str:
        return await self.plugin._get_player_summary(steam_id)


class SteamMarketPriceTool(BaseTool):
    """查询 Steam 市场价格"""
    name = "steam_market_price"
    description = "查询 Steam 社区市场物品的当前价格"
    parameters = {
        "type": "object",
        "properties": {
            "item_name": {"type": "string", "description": "物品市场名称，如 'AK-47 | Redline (Field-Tested)'"},
            "app_id": {"type": "string", "description": "游戏 AppID 或缩写(csgo/dota2/tf2)"}
        },
        "required": ["item_name"],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, item_name: str, app_id: str = None, *args, **kwargs) -> str:
        return await self.plugin._market_price(item_name, app_id)


class SteamMarketHistoryTool(BaseTool):
    """查询市场历史成交"""
    name = "steam_market_history"
    description = "查询 Steam 社区市场物品的历史成交记录"
    parameters = {
        "type": "object",
        "properties": {
            "item_name": {"type": "string", "description": "物品市场名称"},
            "app_id": {"type": "string", "description": "游戏 AppID 或缩写"},
            "days": {"type": "integer", "description": "查询天数，默认 7", "default": 7}
        },
        "required": ["item_name"],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, item_name: str, app_id: str = None, days: int = 7, *args, **kwargs) -> str:
        return await self.plugin._market_history(item_name, app_id, days)


class SteamGameItemsTool(BaseTool):
    """查询游戏内物品库存"""
    name = "steam_game_items"
    description = "查询用户在特定游戏中的物品/道具库存（CS:GO 皮肤、Dota2 饰品等）"
    parameters = {
        "type": "object",
        "properties": {
            "app_id": {"type": "string", "description": "游戏 AppID 或缩写(csgo/dota2/tf2)"},
            "steam_id": {"type": "string", "description": "SteamID64，不填则使用配置的默认值"},
            "context_id": {"type": "string", "description": "上下文 ID，默认 2", "default": "2"},
            "limit": {"type": "integer", "description": "返回数量上限，默认 30", "default": 30}
        },
        "required": ["app_id"],
    }

    def __init__(self, plugin):
        self.plugin = plugin

    async def execute(self, event: KiraMessageBatchEvent, app_id: str, steam_id: str = None, context_id: str = "2", limit: int = 30, *args, **kwargs) -> str:
        return await self.plugin._get_game_items(app_id, steam_id, context_id, limit)


# ============================================================
# 主插件类
# ============================================================

class SteamPlugin(BasePlugin):
    """Steam 集成插件"""

    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)

        steam_cfg = cfg.get("section_steam", {})
        self.api_key = steam_cfg.get("api_key", "")
        self.steam_id = steam_cfg.get("steam_id", "")
        self.enabled = steam_cfg.get("enabled", True)
        self.proxy_enabled = steam_cfg.get("proxy_enabled", False)
        self.proxy_url = steam_cfg.get("proxy_url", "")
        self.cache_ttl = steam_cfg.get("cache_ttl", 300)

        self.APP_IDS = {
            "csgo": 730, "cs": 730, "counterstrike": 730,
            "dota2": 570, "dota": 570,
            "tf2": 440, "teamfortress": 440,
            "pubg": 578080,
            "rust": 252490,
            "gta5": 271590, "gta": 271590,
        }

        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._cleanup_task: Optional[asyncio.Task] = None

    async def initialize(self):
        if not self.enabled:
            logger.info("[steam] 已禁用")
            return

        resolver = ThreadedResolver()
        connector = aiohttp.TCPConnector(resolver=resolver)

        if self.proxy_enabled and self.proxy_url:
            logger.info(f"[steam] 代理已启用: {self.proxy_url}")
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "KiraAI-SteamPlugin/1.0"},
                proxy=self.proxy_url,
            )
        else:
            logger.info("[steam] 直连模式（未启用代理）")
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "KiraAI-SteamPlugin/1.0"},
            )

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("[steam] 初始化完成")

    async def terminate(self):
        if self._session:
            await self._session.close()
            self._session = None

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

        self._cache.clear()
        logger.info("[steam] 已终止")

    # ============================================================
    # 缓存
    # ============================================================

    def _cache_get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        ts, data = self._cache[key]
        if time.time() - ts > self.cache_ttl:
            self._cache.pop(key, None)
            return None
        return data

    def _cache_set(self, key: str, data: Any):
        self._cache[key] = (time.time(), data)

    async def _cleanup_loop(self):
        try:
            while True:
                await asyncio.sleep(self.cache_ttl)
                now = time.time()
                expired = [k for k, (ts, _) in self._cache.items() if now - ts > self.cache_ttl]
                for k in expired:
                    self._cache.pop(k, None)
                if expired:
                    logger.debug(f"[steam] 清理 {len(expired)} 条缓存")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[steam] 缓存清理异常")

    # ============================================================
    # Steam API 请求
    # ============================================================

    def _resolve_app_id(self, app_id: Optional[str]) -> Optional[int]:
        if not app_id:
            return None
        if str(app_id).isdigit():
            return int(app_id)
        return self.APP_IDS.get(str(app_id).lower())

    async def _steam_request(self, interface: str, method: str, version: str = "v1", params: dict = None) -> Optional[dict]:
        if not self.api_key:
            logger.warning("[steam] API Key 未配置")
            return None

        url = f"https://api.steampowered.com/{interface}/{method}/{version}/"
        params = params or {}
        params["key"] = self.api_key

        cache_key = f"api:{interface}:{method}:{json.dumps(params, sort_keys=True)}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"[steam] API 失败: {resp.status}")
                    return None
                data = await resp.json()
                self._cache_set(cache_key, data)
                return data
        except asyncio.TimeoutError:
            logger.warning("[steam] API 超时")
            return None
        except Exception as e:
            logger.exception(f"[steam] API 异常: {e}")
            return None

    async def _market_request(self, app_id: int, item_name: str, endpoint: str = "priceoverview") -> Optional[dict]:
        encoded_name = item_name.replace(" ", "%20")
        url = f"https://steamcommunity.com/market/{endpoint}/"
        params = {
            "country": "CN",
            "currency": "1",
            "appid": app_id,
            "market_hash_name": item_name,
        }
        if endpoint == "priceoverview":
            params["format"] = "json"

        cache_key = f"market:{app_id}:{item_name}:{endpoint}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json() if endpoint == "priceoverview" else await resp.text()
                self._cache_set(cache_key, data)
                return data
        except Exception as e:
            logger.exception(f"[steam] 市场请求异常: {e}")
            return None

    async def _market_listing(self, app_id: int, item_name: str) -> Optional[dict]:
        encoded_name = item_name.replace(" ", "%20")
        url = f"https://steamcommunity.com/market/listings/{app_id}/{encoded_name}/"
        cache_key = f"market_listing:{app_id}:{item_name}"

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")

                price_span = soup.find("span", class_="market_listing_price")
                if not price_span:
                    return None

                result = {
                    "current_price": price_span.text.strip(),
                    "listing_count": None,
                    "volume": None,
                }

                listing_count_span = soup.find("span", class_="market_listing_num_listings_qty")
                if listing_count_span:
                    result["listing_count"] = listing_count_span.text.strip()

                volume_span = soup.find("span", class_="market_listing_volume")
                if volume_span:
                    result["volume"] = volume_span.text.strip()

                self._cache_set(cache_key, result)
                return result
        except Exception as e:
            logger.exception(f"[steam] 市场列表解析异常: {e}")
            return None

    def _format_market_price(self, item_name: str, app_id: int, listing: dict) -> str:
        lines = [
            f"💰 **Steam 市场价格**",
            f"🎮 AppID: {app_id}",
            f"📦 物品: {item_name}",
            f"💵 当前售价: {listing.get('current_price', '未知')}",
        ]
        if listing.get("listing_count"):
            lines.append(f"📋 在售数量: {listing['listing_count']}")
        if listing.get("volume"):
            lines.append(f"📊 24h 成交量: {listing['volume']}")

        encoded_name = item_name.replace(" ", "%20")
        lines.append(f"🔗 市场链接: https://steamcommunity.com/market/listings/{app_id}/{encoded_name}")
        return "\n".join(lines)

    def _get_help(self) -> str:
        return """📖 Steam 命令帮助

/steam market <物品名>    查询市场价格
/steam inventory          查询我的游戏库存
/steam friends            查询好友列表
/steam me                 查询我的个人资料
/steam items <游戏>       查询游戏物品库存 (csgo/dota2/tf2/pubg)
/steam search <关键词>    搜索商店游戏
/steam help               显示此帮助

示例：
/steam market AK-47 | Redline
/steam market csgo:AK-47 | Redline
/steam items csgo
/steam search 空洞骑士"""

    # ============================================================
    # 核心业务方法（供工具类调用）
    # ============================================================

    async def _search_games(self, query: str) -> str:
        if not self.api_key:
            return "⚠️ 未配置 Steam API Key"

        url = "https://store.steampowered.com/api/storesearch"
        params = {"term": query, "l": "zh", "cc": "cn"}

        cache_key = f"store_search:{query}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    return f"❌ 搜索失败 (HTTP {resp.status})"

                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    return f"🔍 未找到与「{query}」相关的游戏"

                results = []
                for idx, item in enumerate(items[:5], 1):
                    name = item.get("name", "未知游戏")
                    price = item.get("price", 0)
                    if isinstance(price, dict):
                        final = price.get("final", 0)
                        initial = price.get("initial", 0)
                        discount = price.get("discount_percent", 0)
                        if discount > 0:
                            price_str = f"¥{final / 100:.2f} (原价 ¥{initial / 100:.2f}, -{discount}%)"
                        else:
                            price_str = f"¥{final / 100:.2f}" if final > 0 else "免费"
                    else:
                        price_str = "免费" if price == 0 else f"¥{price / 100:.2f}"

                    tiny_desc = item.get("tiny_description", "")
                    app_id = item.get("id", "")
                    results.append(
                        f"{idx}. **{name}** (AppID: {app_id})\n"
                        f"   💰 {price_str}\n"
                        f"   📝 {tiny_desc[:100]}{'...' if len(tiny_desc) > 100 else ''}"
                    )

                output = f"🔍 Steam 搜索结果（「{query}」）：\n\n" + "\n\n".join(results)
                self._cache_set(cache_key, output)
                return output

        except asyncio.TimeoutError:
            return "⏰ 搜索超时"
        except Exception as e:
            logger.exception("[steam] 搜索异常")
            return f"❌ 搜索失败：{str(e)[:100]}"

    async def _get_inventory(self, steam_id: str = None, app_id: str = None) -> str:
        if not self.api_key:
            return "⚠️ 未配置 Steam API Key"

        target_id = steam_id or self.steam_id
        if not target_id:
            return "⚠️ 未指定 SteamID"
        if not target_id.isdigit() or len(target_id) < 10:
            return f"❌ SteamID 格式错误：{target_id}"

        params = {
            "steamid": target_id,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json"
        }
        if app_id:
            params["appids_filter"] = app_id

        data = await self._steam_request("IPlayerService", "GetOwnedGames", "v1", params)
        if not data:
            return "❌ 获取库存失败，请检查 SteamID 或隐私设置"

        response = data.get("response", {})
        games = response.get("games", [])
        total_count = response.get("game_count", 0)

        if not games:
            return f"📭 用户 {target_id} 的库存为空（或隐私不公开）"

        games_sorted = sorted(games, key=lambda g: g.get("playtime_forever", 0), reverse=True)

        result_lines = [f"🎮 用户 {target_id} 的 Steam 游戏库存（共 {total_count} 款）：", ""]

        for idx, game in enumerate(games_sorted[:20], 1):
            name = game.get("name", "未知游戏")
            appid = game.get("appid", "")
            playtime = game.get("playtime_forever", 0)
            playtime_str = f"{playtime // 60}h" if playtime >= 60 else f"{playtime}m" if playtime > 0 else "未玩"
            recent = game.get("playtime_2weeks", 0)
            recent_str = f"（最近两周 {recent // 60}h）" if recent > 0 else ""

            result_lines.append(f"{idx}. {name} (AppID: {appid})")
            result_lines.append(f"   游玩时间: {playtime_str} {recent_str}")

        if len(games_sorted) > 20:
            result_lines.append(f"\n... 还有 {len(games_sorted) - 20} 款游戏")

        return "\n".join(result_lines)

    async def _get_friends(self, steam_id: str = None, include_profile: bool = True) -> str:
        if not self.api_key:
            return "⚠️ 未配置 Steam API Key"

        target_id = steam_id or self.steam_id
        if not target_id:
            return "⚠️ 未指定 SteamID"
        if not target_id.isdigit() or len(target_id) < 10:
            return f"❌ SteamID 格式错误：{target_id}"

        params = {"steamid": target_id, "relationship": "friend", "format": "json"}
        data = await self._steam_request("ISteamUser", "GetFriendList", "v1", params)
        if not data:
            return "❌ 获取好友列表失败"

        friends_list = data.get("friendslist", {}).get("friends", [])
        if not friends_list:
            return f"📭 用户 {target_id} 的好友列表为空（或隐私不公开）"

        friend_ids = [f["steamid"] for f in friends_list]

        profiles = {}
        if include_profile and friend_ids:
            chunks = [friend_ids[i:i+100] for i in range(0, len(friend_ids), 100)]
            for chunk in chunks:
                profile_params = {"steamids": ",".join(chunk), "format": "json"}
                profile_data = await self._steam_request("ISteamUser", "GetPlayerSummaries", "v2", profile_params)
                if profile_data:
                    for player in profile_data.get("response", {}).get("players", []):
                        profiles[player["steamid"]] = player

        result_lines = [f"👥 用户 {target_id} 的好友列表（共 {len(friend_ids)} 人）：", ""]

        status_map = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "休眠", 5: "想玩", 6: "想玩"}

        for idx, fid in enumerate(friend_ids[:30], 1):
            profile = profiles.get(fid, {})
            name = profile.get("personaname", "未知用户")
            status = status_map.get(profile.get("personastate", 0), "未知")
            game = profile.get("gameextrainfo", "")
            game_str = f" - 游戏中: {game}" if game else ""

            result_lines.append(f"{idx}. {name} (ID: {fid})")
            result_lines.append(f"   状态: {status}{game_str}")

        if len(friend_ids) > 30:
            result_lines.append(f"\n... 还有 {len(friend_ids) - 30} 位好友")

        return "\n".join(result_lines)

    async def _get_player_summary(self, steam_id: str = None) -> str:
        if not self.api_key:
            return "⚠️ 未配置 Steam API Key"

        target_id = steam_id or self.steam_id
        if not target_id:
            return "⚠️ 未指定 SteamID"

        params = {"steamids": target_id, "format": "json"}
        data = await self._steam_request("ISteamUser", "GetPlayerSummaries", "v2", params)
        if not data:
            return f"❌ 获取用户 {target_id} 信息失败"

        players = data.get("response", {}).get("players", [])
        if not players:
            return f"❌ 未找到用户 {target_id}"

        p = players[0]
        status_map = {0: "离线", 1: "在线", 2: "忙碌", 3: "离开", 4: "休眠", 5: "想玩", 6: "想玩"}
        lines = [
            f"🎮 Steam 用户信息",
            f"📛 昵称: {p.get('personaname', '未知')}",
            f"🆔 SteamID: {p.get('steamid', '')}",
            f"📊 状态: {status_map.get(p.get('personastate', 0), '未知')}",
            f"🌐 国家: {p.get('loccountrycode', '未知')}",
            f"🔗 个人资料: {p.get('profileurl', '')}",
        ]
        if p.get("realname"):
            lines.append(f"📝 真实姓名: {p['realname']}")
        if p.get("timecreated"):
            created = datetime.fromtimestamp(p["timecreated"]).strftime("%Y-%m-%d")
            lines.append(f"📅 注册时间: {created}")
        if p.get("gameextrainfo"):
            lines.append(f"🎯 游戏中: {p['gameextrainfo']}")

        return "\n".join(lines)

    async def _market_price(self, item_name: str, app_id: str = None) -> str:
        app_id = self._resolve_app_id(app_id)
        if not app_id:
            return "⚠️ 无法识别游戏 AppID"

        data = await self._market_request(app_id, item_name, "priceoverview")
        if not data:
            listing = await self._market_listing(app_id, item_name)
            if listing:
                return self._format_market_price(item_name, app_id, listing)
            return f"❌ 未找到物品「{item_name}」的市场信息"

        if data.get("success") is False:
            return f"❌ 未找到物品「{item_name}」的市场信息"

        price_str = data.get("price", "未知")
        lowest_price = data.get("lowest_price", "未知")
        volume = data.get("volume", "未知")

        def parse_price(p):
            if not p or p == "未知":
                return 0
            cleaned = re.sub(r"[^\d,.]", "", p)
            try:
                return float(cleaned.replace(",", ""))
            except:
                return 0

        current = parse_price(price_str)
        lowest = parse_price(lowest_price)

        lines = [
            f"💰 **Steam 市场价格**",
            f"🎮 AppID: {app_id}",
            f"📦 物品: {item_name}",
            f"💵 当前售价: {price_str}",
            f"📉 最低挂单价: {lowest_price}",
            f"📊 24h 成交量: {volume}",
        ]

        if current > 0 and lowest > 0 and lowest < current:
            diff = ((current - lowest) / current) * 100
            lines.append(f"📌 挂单低于市价: -{diff:.1f}%")

        encoded_name = item_name.replace(" ", "%20")
        lines.append(f"🔗 市场链接: https://steamcommunity.com/market/listings/{app_id}/{encoded_name}")

        return "\n".join(lines)

    async def _market_history(self, item_name: str, app_id: str = None, days: int = 7) -> str:
        app_id = self._resolve_app_id(app_id)
        if not app_id:
            return "⚠️ 无法识别游戏 AppID"

        days = min(max(days, 1), 30)

        encoded_name = item_name.replace(" ", "%20")
        url = f"https://steamcommunity.com/market/listings/{app_id}/{encoded_name}/"

        cache_key = f"market_history:{app_id}:{item_name}:{days}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return f"❌ 无法获取「{item_name}」的历史记录"

                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")

                rows = soup.select("table.market_listing_table tbody tr")

                if not rows:
                    return f"📭 暂无「{item_name}」的近期成交记录"

                history = []
                for row in rows[:days]:
                    cols = row.select("td")
                    if len(cols) < 3:
                        continue
                    time_elem = cols[0].text.strip()
                    price_elem = cols[1].text.strip()
                    quantity_elem = cols[2].text.strip() if len(cols) > 2 else "1"

                    try:
                        dt = datetime.strptime(time_elem, "%b %d, %Y %H:%M")
                        time_str = dt.strftime("%m-%d %H:%M")
                    except:
                        time_str = time_elem

                    history.append(f"{time_str}  |  {price_elem}  |  成交 {quantity_elem} 件")

                if not history:
                    return f"📭 暂无「{item_name}」的近期成交记录"

                price_data = await self._market_request(app_id, item_name, "priceoverview")
                current_price = "未知"
                if price_data and price_data.get("success"):
                    current_price = price_data.get("price", "未知")

                output = (
                    f"📊 **{item_name}** 近期成交记录 (AppID: {app_id})\n"
                    f"💰 当前价格: {current_price}\n\n"
                    f"📋 最近 {min(len(history), days)} 笔成交:\n"
                    + "\n".join(f"  {h}" for h in history[:days])
                )

                self._cache_set(cache_key, output)
                return output

        except Exception as e:
            logger.exception("[steam] 市场历史查询异常")
            return f"❌ 查询失败：{str(e)[:100]}"

    async def _get_game_items(self, app_id: str, steam_id: str = None, context_id: str = "2", limit: int = 30) -> str:
        """
        查询游戏内物品库存 - 使用 Steam 社区公开接口
        接口：https://steamcommunity.com/inventory/{steam_id}/{app_id}/{context_id}
        此接口不需要 API Key
        """
        resolved_app_id = self._resolve_app_id(app_id)
        if not resolved_app_id:
            return "⚠️ 无法识别游戏 AppID"

        target_id = steam_id or self.steam_id
        if not target_id:
            return "⚠️ 未指定 SteamID"
        if not target_id.isdigit() or len(target_id) < 10:
            return f"❌ SteamID 格式错误：{target_id}"

        limit = min(max(limit, 1), 100)
        context_id = context_id or "2"

        url = f"https://steamcommunity.com/inventory/{target_id}/{resolved_app_id}/{context_id}"
        params = {"l": "zh", "count": limit}

        cache_key = f"inventory:{target_id}:{resolved_app_id}:{context_id}:{limit}"
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        # ---------- 限流保护 ----------
        # 同一目标 + 同一游戏，至少间隔 3 秒
        import time
        rate_limit_key = f"inventory:{target_id}:{resolved_app_id}"
        now = time.time()
        last_req = getattr(self, "_last_request_time", {})
        if rate_limit_key in last_req:
            elapsed = now - last_req[rate_limit_key]
            if elapsed < 3.0:
                wait_time = 3.0 - elapsed
                logger.warning(f"[steam] 请求太频繁，等待 {wait_time:.1f} 秒后重试")
                await asyncio.sleep(wait_time)
        last_req[rate_limit_key] = time.time()
        self._last_request_time = last_req

        try:
            async with self._session.get(url, params=params) as resp:
                # ---------- 针对 429 的特殊提示 ----------
                if resp.status == 429:
                    return (
                        f"⏰ 请求太频繁，Steam 暂时限流了（HTTP 429）。\n\n"
                        "请等待 1-2 分钟后重试。\n"
                        "如果多次出现此提示，说明查询过于密集，建议放缓查询频率。"
                    )

                if resp.status == 404:
                    return (
                        f"❌ 该游戏（AppID: {resolved_app_id}）的库存接口不存在。\n\n"
                        "可能原因：\n"
                        "1. 该游戏不支持 Steam 库存系统\n"
                        "2. 游戏 ID 不正确\n\n"
                        "💡 试试用 `/steam inventory` 查询你拥有的游戏列表，确认 AppID 是否正确。"
                    )

                if resp.status != 200:
                    return f"❌ 获取库存失败 (HTTP {resp.status})，请确认库存是否公开"
    
                data = await resp.json()

                if data.get("success") is False:
                    error_msg = data.get("error", "未知错误")
                    if "profile" in error_msg.lower() or "private" in error_msg.lower():
                        return "❌ 该用户的 Steam 库存设为「不公开」，无法查看物品。\n\n请在 Steam 个人资料设置中将库存设为「公开」后重试。"
                    return f"❌ {error_msg}"

                assets = data.get("assets", [])
                descriptions = {d.get("classid", ""): d for d in data.get("descriptions", [])}

                if not assets:
                    game_names = {730: "CS:GO/CS2", 570: "Dota2", 440: "TF2", 578080: "PUBG"}
                    game_name = game_names.get(resolved_app_id, f"AppID {resolved_app_id}")
                    return f"📭 用户在游戏 {game_name} 中没有任何物品（或库存为空）。"

                enriched_items = []
                for item in assets[:limit]:
                    class_id = item.get("classid", "")
                    desc = descriptions.get(class_id, {})
                    enriched_items.append({
                        "name": desc.get("market_hash_name", desc.get("name", "未知物品")),
                        "type": desc.get("type", "未知类型"),
                        "rarity": desc.get("rarity", ""),
                        "color": desc.get("color", ""),
                        "tradable": desc.get("tradable", False),
                        "marketable": desc.get("marketable", False),
                        "quantity": item.get("amount", 1),
                    })

                rarity_order = {"Common": 0, "Uncommon": 1, "Rare": 2, "Mythical": 3, "Legendary": 4, "Ancient": 5, "Immortal": 6}
                enriched_items.sort(key=lambda x: rarity_order.get(x.get("rarity", ""), -1), reverse=True)

                total = len(assets)
                tradable_count = sum(1 for i in enriched_items if i.get("tradable"))
                marketable_count = sum(1 for i in enriched_items if i.get("marketable"))

                game_names = {730: "CS:GO/CS2", 570: "Dota2", 440: "TF2", 578080: "PUBG"}
                game_name = game_names.get(resolved_app_id, f"AppID {resolved_app_id}")

                lines = [
                    f"🎒 **{game_name} 物品库存**",
                    f"👤 用户: {target_id}",
                    f"📦 物品总数: {total}",
                    f"🔄 可交易: {tradable_count} 件",
                    f"🏪 可上架市场: {marketable_count} 件",
                    "",
                    "📋 物品列表:",
                ]

                for idx, item in enumerate(enriched_items[:limit], 1):
                    name = item["name"]
                    qty = f" x{item['quantity']}" if item['quantity'] > 1 else ""
                    rarity = f"[{item['rarity']}]" if item.get('rarity') else ""
                    tradable = "🔁" if item.get('tradable') else "🔒"
                    marketable = "🏪" if item.get('marketable') else ""
                    lines.append(f"{idx}. {tradable}{marketable} {name}{qty} {rarity}")

                if total > limit:
                    lines.append(f"\n... 还有 {total - limit} 件物品未显示（可调整 limit 参数获取更多）")

                lines.append(f"\n💡 使用 `/steam market` 可查询具体物品价格")
                output = "\n".join(lines)
                self._cache_set(cache_key, output)
                return output
    
        except asyncio.TimeoutError:
            return "⏰ 库存查询超时，请稍后再试"
        except Exception as e:
            logger.exception("[steam] 库存请求异常")
            return f"❌ 请求失败：{str(e)[:100]}"

    # ============================================================
    # 工具注入
    # ============================================================

    @on.llm_request(priority=Priority.HIGH)
    async def inject_tools(self, event, req: LLMRequest, *args, **kwargs):
        """把 Steam 工具注入到 LLM 请求中"""
        if not self.enabled or not self.api_key:
            return

        req.tool_set.add(SteamSearchGamesTool(self))
        req.tool_set.add(SteamInventoryTool(self))
        req.tool_set.add(SteamFriendsTool(self))
        req.tool_set.add(SteamPlayerSummaryTool(self))
        req.tool_set.add(SteamMarketPriceTool(self))
        req.tool_set.add(SteamMarketHistoryTool(self))
        req.tool_set.add(SteamGameItemsTool(self))

        for p in req.system_prompt:
            if p.name == "tools":
                p.content += (
                    "\n\n【Steam 插件工具】"
                    "\n- steam_search_games: 搜索商店游戏"
                    "\n- steam_inventory: 查询用户游戏库存"
                    "\n- steam_friends: 查询好友列表"
                    "\n- steam_player_summary: 查询用户资料"
                    "\n- steam_market_price: 查询市场物品价格（需要精确物品名称）"
                    "\n- steam_market_history: 查询市场物品历史成交记录"
                    "\n- steam_game_items: 查询特定游戏的物品/道具库存（如 CS:GO 皮肤）"
                    "\n\n常用游戏缩写: csgo/cs=730, dota2/dota=570, tf2=440, pubg=578080"
                    "\nSteamID 默认使用配置值，用户也可指定其他 SteamID。"
                )
                break

    # ============================================================
    # 斜杠命令 /steam
    # ============================================================

    @on.im_message(priority=Priority.HIGH)
    async def handle_slash_command(self, event: KiraMessageEvent):
        """处理 /steam 斜杠命令"""
        if not self.enabled:
            return

        text = ""
        for elem in event.message.chain:
            if hasattr(elem, "text"):
                text += elem.text

        if not text.strip().startswith("/steam"):
            return

        parts = text.strip().split(maxsplit=1)
        args = parts[1] if len(parts) > 1 else ""

        if not args.strip():
            await self._send_reply(event, self._get_help())
            event.stop()
            return

        subcmd_parts = args.strip().split(maxsplit=1)
        subcmd = subcmd_parts[0].lower()
        rest = subcmd_parts[1] if len(subcmd_parts) > 1 else ""

        handlers = {
            "market": self._cmd_market,
            "price": self._cmd_market,
            "inventory": self._cmd_inventory,
            "games": self._cmd_inventory,
            "friends": self._cmd_friends,
            "me": self._cmd_me,
            "profile": self._cmd_me,
            "items": self._cmd_items,
            "search": self._cmd_search,
            "help": self._cmd_help,
        }

        handler = handlers.get(subcmd)
        if not handler:
            await self._send_reply(event, f"❌ 未知子命令: {subcmd}\n\n{self._get_help()}")
            event.stop()
            return

        result = await handler(event, rest)
        await self._send_reply(event, result)
        event.stop()

    async def _send_reply(self, event: KiraMessageEvent, text: str):
        """发送回复消息"""
        await self.ctx.message_processor.send_message_chain(
            event.session.sid,
            MessageChain([Text(text)])
        )

    # ---------- 子命令 ----------

    async def _cmd_market(self, event: KiraMessageEvent, rest: str) -> str:
        if not rest.strip():
            return "❌ 用法: /steam market <物品名>\n示例: /steam market AK-47 | Redline"
        app_id = None
        item_name = rest
        if ":" in rest:
            parts = rest.split(":", 1)
            app_id = parts[0].strip()
            item_name = parts[1].strip()
        return await self._market_price(item_name, app_id)

    async def _cmd_inventory(self, event: KiraMessageEvent, rest: str) -> str:
        return await self._get_inventory(steam_id=None, app_id=rest.strip() or None)

    async def _cmd_friends(self, event: KiraMessageEvent, rest: str) -> str:
        return await self._get_friends(steam_id=None, include_profile=True)

    async def _cmd_me(self, event: KiraMessageEvent, rest: str) -> str:
        return await self._get_player_summary(steam_id=None)

    async def _cmd_items(self, event: KiraMessageEvent, rest: str) -> str:
        if not rest.strip():
            return "❌ 用法: /steam items <游戏>\n支持: csgo, dota2, tf2, pubg"
        return await self._get_game_items(rest.strip().lower(), steam_id=None, context_id="2", limit=30)

    async def _cmd_search(self, event: KiraMessageEvent, rest: str) -> str:
        if not rest.strip():
            return "❌ 用法: /steam search <关键词>"
        return await self._search_games(rest.strip())

    async def _cmd_help(self, event: KiraMessageEvent, rest: str) -> str:
        return self._get_help()
