import json
from datetime import datetime, timedelta
from typing import Optional

from pathlib import Path

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


ANIME_LIST_URL = "https://anime1.me/animelist.json"
ANIME_WATCH_URL = "https://anime1.me/?cat={}"
PLUGIN_NAME = "astrbot_plugin_anime1_list"


@register("astrbot_plugin_anime1_list", "YourName", "获取anime1.me番剧更新列表", "1.0.0")
class Anime1ListPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.scheduler = AsyncIOScheduler()
        self.data_path = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.list_file = self.data_path / "anime_list.json"

    async def initialize(self):
        update_times_str = self.config.get("update_times", "1")
        hours = self._parse_update_times(update_times_str)
        
        for hour in hours:
            self.scheduler.add_job(
                self._fetch_and_merge_anime_list,
                trigger="cron",
                hour=hour,
                minute=0,
                id=f"anime_update_{hour}",
                replace_existing=True,
            )
            logger.info(f"[Anime1List] 已添加定时任务：每天 {hour}:00 更新番剧列表")
        
        self.scheduler.start()
        logger.info("[Anime1List] 插件初始化完成")

    def _parse_update_times(self, times_str: str) -> list[int]:
        hours = []
        for part in times_str.split(","):
            part = part.strip()
            if part:
                try:
                    hour = int(part)
                    if 0 <= hour <= 23:
                        hours.append(hour)
                    else:
                        logger.warning(f"[Anime1List] 忽略无效小时值: {hour}")
                except ValueError:
                    logger.warning(f"[Anime1List] 忽略无效时间配置: {part}")
        return hours if hours else [1]

    async def _fetch_anime_list_from_api(self) -> Optional[list]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ANIME_LIST_URL, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data
                    else:
                        logger.error(f"[Anime1List] API请求失败，状态码: {resp.status}")
                        return None
        except Exception as e:
            logger.error(f"[Anime1List] 获取番剧列表失败: {e}")
            return None

    def _load_saved_list(self) -> list:
        try:
            if self.list_file.exists():
                with open(self.list_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"[Anime1List] 加载保存的列表失败: {e}")
        return []

    def _save_list(self, anime_list: list):
        try:
            with open(self.list_file, "w", encoding="utf-8") as f:
                json.dump(anime_list, f, ensure_ascii=False, indent=2)
            logger.info(f"[Anime1List] 已保存 {len(anime_list)} 条番剧数据")
        except Exception as e:
            logger.error(f"[Anime1List] 保存列表失败: {e}")

    async def _fetch_and_merge_anime_list(self):
        logger.info("[Anime1List] 开始获取番剧列表...")
        new_data = await self._fetch_anime_list_from_api()
        if new_data is None:
            return
        
        saved_list = self._load_saved_list()
        saved_map = {item.get("id"): item for item in saved_list if "id" in item}
        
        current_time = datetime.now().isoformat()
        merged_list = []
        new_ids = set()
        
        for item in new_data:
            anime_id = item[0] if len(item) > 0 else None
            if anime_id is None:
                continue
            new_ids.add(anime_id)
            
            if anime_id in saved_map:
                saved_item = saved_map[anime_id]
                saved_item["title"] = item[1] if len(item) > 1 else saved_item.get("title", "")
                saved_item["status"] = item[2] if len(item) > 2 else saved_item.get("status", "")
                saved_item["year"] = item[3] if len(item) > 3 else saved_item.get("year", "")
                saved_item["season"] = item[4] if len(item) > 4 else saved_item.get("season", "")
                saved_item["extra"] = item[5] if len(item) > 5 else saved_item.get("extra", "")
                merged_list.append(saved_item)
            else:
                anime_entry = {
                    "id": anime_id,
                    "title": item[1] if len(item) > 1 else "",
                    "status": item[2] if len(item) > 2 else "",
                    "year": item[3] if len(item) > 3 else "",
                    "season": item[4] if len(item) > 4 else "",
                    "extra": item[5] if len(item) > 5 else "",
                    "updated_at": current_time,
                }
                merged_list.append(anime_entry)
        
        for saved_item in saved_list:
            if saved_item.get("id") not in new_ids:
                merged_list.append(saved_item)
        
        self._save_list(merged_list)
        logger.info(f"[Anime1List] 番剧列表更新完成，共 {len(merged_list)} 条")

    def _filter_by_time_range(self, anime_list: list, time_range: str) -> list:
        if not time_range or time_range not in ["年", "月", "周", "日"]:
            return anime_list
        
        now = datetime.now()
        filtered = []
        
        for item in anime_list:
            updated_at_str = item.get("updated_at", "")
            if not updated_at_str:
                continue
            
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
            except:
                continue
            
            if time_range == "年":
                if updated_at.year == now.year:
                    filtered.append(item)
            elif time_range == "月":
                if updated_at.year == now.year and updated_at.month == now.month:
                    filtered.append(item)
            elif time_range == "周":
                week_start = now - timedelta(days=now.weekday())
                week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
                if updated_at >= week_start:
                    filtered.append(item)
            elif time_range == "日":
                if updated_at.date() == now.date():
                    filtered.append(item)
        
        return filtered

    @filter.llm_tool(name="get_anime_list")
    async def get_anime_list(self, event: AstrMessageEvent, use_cache: bool = True, time_range: str = "日", limit: int = 100) -> str:
        """获取番剧更新列表。

        Args:
            use_cache(boolean): 是否使用缓存数据，默认true使用缓存快速返回，false则重新获取最新数据
            time_range(string): 时间范围过滤，可选值：年、月、周、日、空。默认为日
            limit(number): 返回数量限制，默认100，-1表示无限制
        """
        if not use_cache:
            await self._fetch_and_merge_anime_list()
        
        anime_list = self._load_saved_list()
        
        if not anime_list:
            return "暂无番剧数据，请稍后再试或设置 use_cache=false 重新获取。"
        
        if time_range and time_range in ["年", "月", "周", "日"]:
            anime_list = self._filter_by_time_range(anime_list, time_range)
        
        if not anime_list:
            return f"在{time_range}范围内没有更新的番剧。"
        
        total = len(anime_list)
        if limit != -1 and limit > 0:
            anime_list = anime_list[:limit]
        
        result_lines = []
        for item in anime_list:
            line = f"[{item.get('id')}] {item.get('title', '')} - {item.get('status', '')} ({item.get('year', '')}年{item.get('season', '')})"
            result_lines.append(line)
        
        return f"共 {total} 部番剧，返回 {len(anime_list)} 部：\n" + "\n".join(result_lines)

    @filter.llm_tool(name="get_watch_url")
    async def get_watch_url(self, event: AstrMessageEvent, anime_id: int) -> str:
        """通过id获取anime1观看地址。

        Args:
            anime_id(number): 番剧ID
        """
        url = ANIME_WATCH_URL.format(anime_id)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30, allow_redirects=False) as resp:
                    if resp.status in (301, 302):
                        location = resp.headers.get("Location", "")
                        if location:
                            return f"观看链接：{location}"
                        else:
                            return f"无法获取重定向地址，请手动访问：{url}"
                    else:
                        return f"请求异常，状态码：{resp.status}，请手动访问：{url}"
        except Exception as e:
            logger.error(f"[Anime1List] 获取观看链接失败: {e}")
            return f"请求失败：{e}，请手动访问：{url}"

    @filter.command("anime_update")
    async def force_update(self, event: AstrMessageEvent):
        """手动触发更新番剧列表"""
        await self._fetch_and_merge_anime_list()
        yield event.plain_result("番剧列表已更新")

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
        logger.info("[Anime1List] 插件已卸载")
