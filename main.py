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
# 工具类
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
    """查询 Steam 游戏库存（已购游戏列表）"""
    name = "steam_inventory"
    description = "查询指定用户的 Steam 游戏库存列表（已购游戏）"
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
            "currency": "1",  # 人民币
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

    def _get_help(self) -> str:
        return """📖 Steam 命令帮助

/steam market <物品名>    查询市场价格（自动汇总所有磨损度）
/steam inventory          查询我的游戏库存（已购游戏列表）
/steam friends            查询好友列表
/steam me                 查询我的个人资料
/steam search <关键词>    搜索商店游戏
/steam help               显示此帮助

示例：
/steam market M4A1-S | Printstream
/steam market csgo:M4A1-S | Printstream
/steam inventory
/steam friends
/steam search 空洞骑士

💡 查询饰品价格时，无需指定磨损度，插件会自动汇总所有磨损度。
   如需精确查询某个磨损度：/steam market <物品名> (Factory New)"""

    # ============================================================
    # 核心业务方法
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
        """查询单个物品价格"""
        app_id = self._resolve_app_id(app_id)
        if not app_id:
            return "⚠️ 无法识别游戏 AppID"

        data = await self._market_request(app_id, item_name, "priceoverview")
        if not data:
            listing = await self._market_listing(app_id, item_name)
            if listing:
                return self._format_market_price_single(item_name, app_id, listing)
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

        encoded_name = item_name.replace(" ", "%20")

        lines = [
            f"📊 {item_name}",
            f"🎮 AppID: {app_id}",
            "",
            f"  当前售价    {price_str}",
            f"  最低挂单    {lowest_price}",
            f"  24h 成交量  {volume}",
        ]

        if current > 0 and lowest > 0 and lowest < current:
            diff = ((current - lowest) / current) * 100
            lines.append(f"  折扣        -{diff:.1f}%")

        lines.append("")
        lines.append(f"🔗 https://steamcommunity.com/market/listings/{app_id}/{encoded_name}")

        return "\n".join(lines)

    def _format_market_price_single(self, item_name: str, app_id: int, listing: dict) -> str:
        """格式化单物品价格（备用方案）"""
        encoded_name = item_name.replace(" ", "%20")
        lines = [
            f"📊 {item_name}",
            f"🎮 AppID: {app_id}",
            "",
            f"  当前售价    {listing.get('current_price', '未知')}",
        ]
        if listing.get("listing_count"):
            lines.append(f"  在售数量    {listing['listing_count']}")
        if listing.get("volume"):
            lines.append(f"  24h 成交量  {listing['volume']}")

        lines.append("")
        lines.append(f"🔗 https://steamcommunity.com/market/listings/{app_id}/{encoded_name}")
        return "\n".join(lines)

    async def _search_wear_variants(self, base_name: str, app_id: Optional[str] = None) -> str:
        """搜索物品的所有磨损度价格并汇总（纯文本格式）"""
        resolved_app_id = self._resolve_app_id(app_id)
        if not resolved_app_id:
            return "⚠️ 无法识别游戏 AppID"

        wear_levels = [
            ("Factory New", "崭新出厂"),
            ("Minimal Wear", "略有磨损"),
            ("Field-Tested", "久经沙场"),
            ("Well-Worn", "破损不堪"),
            ("Battle-Scarred", "战痕累累"),
        ]

        results = []
        found_any = False

        for en_name, cn_name in wear_levels:
            full_name = f"{base_name} ({en_name})"
            price_data = await self._market_request(resolved_app_id, full_name, "priceoverview")

            if price_data and price_data.get("success") is not False:
                found_any = True
                results.append({
                    "wear_cn": cn_name,
                    "price": price_data.get("price", "未知"),
                    "volume": price_data.get("volume", "N/A"),
                })
            else:
                # 尝试不带括号的写法
                full_name_alt = f"{base_name} {en_name}"
                if full_name_alt != full_name:
                    price_data = await self._market_request(resolved_app_id, full_name_alt, "priceoverview")
                    if price_data and price_data.get("success") is not False:
                        found_any = True
                        results.append({
                            "wear_cn": cn_name,
                            "price": price_data.get("price", "未知"),
                            "volume": price_data.get("volume", "N/A"),
                        })

        if not found_any:
            return f"❌ 未找到「{base_name}」的任何磨损度版本。\n\n请确认物品名称是否正确，例如：\n`AK-47 | Redline`\n`M4A1-S | Printstream`"

        # 计算对齐宽度
        max_price_len = max(len(r["price"]) for r in results)
        max_vol_len = max(len(r["volume"]) for r in results)

        game_names = {730: "CS:GO/CS2", 570: "Dota2", 440: "TF2", 578080: "PUBG"}
        game_name = game_names.get(resolved_app_id, f"AppID {resolved_app_id}")

        lines = [
            f"📊 {base_name} 各磨损度价格",
            f"🎮 {game_name}",
            "",
        ]

        for r in results:
            price_pad = r["price"].ljust(max_price_len + 2)
            vol_pad = r["volume"].ljust(max_vol_len + 2)
            lines.append(f"  {r['wear_cn']:<6}  {price_pad}  {vol_pad}")

        lines.append("")
        lines.append("💡 精确查询：/steam market <物品名> (磨损度英文)")
        lines.append("   例：/steam market M4A1-S | Printstream (Factory New)")

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

        for p in req.system_prompt:
            if p.name == "tools":
                p.content += (
                    "\n\n【Steam 插件工具】"
                    "\n- steam_search_games: 搜索商店游戏"
                    "\n- steam_inventory: 查询用户游戏库存（已购游戏列表）"
                    "\n- steam_friends: 查询好友列表"
                    "\n- steam_player_summary: 查询用户资料"
                    "\n- steam_market_price: 查询市场物品价格（自动汇总所有磨损度）"
                    "\n- steam_market_history: 查询市场物品历史成交记录"
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
        """/steam market 子命令 - 支持自动补全磨损度"""
        if not rest.strip():
            return "❌ 用法: /steam market <物品名>\n示例: /steam market M4A1-S | Printstream"

        app_id = None
        item_name = rest

        if ":" in rest:
            parts = rest.split(":", 1)
            app_id = parts[0].strip()
            item_name = parts[1].strip()

        # 检查是否已包含磨损度关键词
        wear_keywords = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred", "("]
        has_wear = any(kw in item_name for kw in wear_keywords)

        if has_wear:
            result = await self._market_price(item_name, app_id)
            if result.startswith("❌") or result.startswith("⚠️"):
                return result
            return result

        # 未指定磨损度 → 汇总所有磨损度
        return await self._search_wear_variants(item_name, app_id)

    async def _cmd_inventory(self, event: KiraMessageEvent, rest: str) -> str:
        return await self._get_inventory(steam_id=None, app_id=rest.strip() or None)

    async def _cmd_friends(self, event: KiraMessageEvent, rest: str) -> str:
        return await self._get_friends(steam_id=None, include_profile=True)

    async def _cmd_me(self, event: KiraMessageEvent, rest: str) -> str:
        return await self._get_player_summary(steam_id=None)

    async def _cmd_search(self, event: KiraMessageEvent, rest: str) -> str:
        if not rest.strip():
            return "❌ 用法: /steam search <关键词>"
        return await self._search_games(rest.strip())

    async def _cmd_help(self, event: KiraMessageEvent, rest: str) -> str:
        return self._get_help()
