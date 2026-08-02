"""
Steam 插件 - 查询商店、库存、好友、市场
"""
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
