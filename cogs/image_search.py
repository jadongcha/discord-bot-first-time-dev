import asyncio
import re
import discord
from discord.ext import commands
from discord import app_commands
from cogs.sources import pixiv


class ImageFeed:
    """채널/스레드 하나에 대한 이미지 피드 상태를 관리합니다."""

    def __init__(self, thread: discord.Thread, query: str):
        self.thread = thread
        self.query = query
        self.seen_ids: set[int] = set()
        self.queue: list[dict] = []
        self.offset: int = 0
        self.task: asyncio.Task | None = None


class ImageSearch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.feeds: dict[int, ImageFeed] = {}  # thread_id → ImageFeed

    # ──────────────────────────────────────────
    # 슬래시 명령어
    # ──────────────────────────────────────────

    @app_commands.command(name="이미지", description="사진 포럼에 새 피드를 만들어 5초마다 이미지를 올립니다.")
    @app_commands.describe(검색어="올리고 싶은 캐릭터 이름 또는 컨셉 (예: 에밀리아 리제로)")
    async def image_slash(self, interaction: discord.Interaction, 검색어: str):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("서버 안에서만 사용할 수 있어요!", ephemeral=True)
            return

        # "사진" 포럼 채널 찾기
        forum = discord.utils.get(guild.forums, name="사진")
        if forum is None:
            await interaction.followup.send(
                "❌ '사진' 포럼 채널을 찾을 수 없어요.\n포럼 채널 이름이 정확히 **사진** 인지 확인해주세요!",
                ephemeral=True
            )
            return

        # 포럼에 새 스레드(피드) 생성
        safe_name = re.sub(r"[^\w가-힣\s]", "", 검색어).strip()
        thread_name = f"{safe_name} 이미지"[:100]

        try:
            thread = await forum.create_thread(
                name=thread_name,
                content=f"🔍 **{검색어}** 이미지 피드를 시작합니다!\n멈추려면 `/이미지중지` 를 입력하세요.",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 포럼에 스레드를 만들 권한이 없어요. 봇에게 **채널 관리** 권한을 주세요!",
                ephemeral=True
            )
            return

        feed = ImageFeed(thread, 검색어)
        self.feeds[thread.id] = feed

        feed.task = asyncio.create_task(self._run_feed(feed))

        await interaction.followup.send(
            f"✅ 포럼에 **{검색어}** 피드를 만들었어요!\n멈추려면 그 스레드에서 `/이미지중지` 를 입력하세요.",
            ephemeral=True,
        )

    @app_commands.command(name="이미지중지", description="현재 스레드의 이미지 자동 피드를 중지합니다.")
    async def stop_slash(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        feed = self.feeds.get(channel_id)

        if feed is None:
            await interaction.response.send_message(
                "이 스레드에서 실행 중인 이미지 피드가 없어요.", ephemeral=True
            )
            return

        await self._stop_feed(channel_id)
        await interaction.response.send_message("⏹️ 이미지 피드를 중지했어요.", ephemeral=True)

    # ──────────────────────────────────────────
    # 피드 내부 로직
    # ──────────────────────────────────────────

    async def _run_feed(self, feed: ImageFeed):
        """5초마다 이미지를 가져와서 스레드에 올립니다."""
        while True:
            try:
                if not feed.queue:
                    batch = await pixiv.fetch_batch(feed.query, offset=feed.offset)
                    feed.offset += 30

                    new_items = [item for item in batch if item["id"] not in feed.seen_ids]
                    feed.queue.extend(new_items)

                    if not feed.queue:
                        # 결과 없으면 처음부터 다시
                        feed.offset = 0
                        feed.seen_ids.clear()
                        await asyncio.sleep(10)
                        continue

                item = feed.queue.pop(0)
                feed.seen_ids.add(item["id"])

                embed = self._build_embed(item)
                await feed.thread.send(embed=embed)

            except discord.HTTPException as e:
                print(f"[피드] Discord 오류: {e}")
            except asyncio.CancelledError:
                return
            except Exception as e:
                print(f"[피드] 오류: {e}")

            await asyncio.sleep(5)

    async def _stop_feed(self, thread_id: int):
        feed = self.feeds.pop(thread_id, None)
        if feed and feed.task:
            feed.task.cancel()
            try:
                await feed.task
            except asyncio.CancelledError:
                pass

    def _build_embed(self, item: dict) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_image(url=item["url"])
        embed.set_footer(text="출처: Pixiv")

        if item.get("page_url"):
            embed.url = item["page_url"]

        desc = ""
        if item.get("title"):
            desc += f"**{item['title']}**"
        if item.get("author"):
            desc += f" — {item['author']}"
        if item.get("tags"):
            desc += f"\n`{item['tags']}`"
        if desc:
            embed.description = desc

        return embed

    async def cog_unload(self):
        for thread_id in list(self.feeds.keys()):
            await self._stop_feed(thread_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageSearch(bot))
