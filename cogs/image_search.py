import asyncio
import re
import discord
from discord.ext import commands
from discord import app_commands
from cogs.sources import pixiv


class ImageFeed:
    """채널 하나에 대한 이미지 피드 상태를 관리합니다."""

    def __init__(self, channel: discord.TextChannel, query: str):
        self.channel = channel
        self.query = query
        self.seen_ids: set[int] = set()
        self.queue: list[dict] = []
        self.offset: int = 0
        self.first_pass_done: bool = False  # 첫 바퀴 완료 여부
        self.task: asyncio.Task | None = None


class ImageSearch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.feeds: dict[int, ImageFeed] = {}  # channel_id → ImageFeed
        self.query_history: dict[str, set[int]] = {}  # 검색어 → seen_ids (채널 삭제 후에도 유지)

    # ──────────────────────────────────────────
    # 슬래시 명령어
    # ──────────────────────────────────────────

    @app_commands.command(name="이미지", description="나만 볼 수 있는 채널을 만들어 5초마다 이미지를 올립니다.")
    @app_commands.describe(검색어="올리고 싶은 캐릭터 이름 또는 컨셉 (예: 에밀리아 리제로)")
    async def image_slash(self, interaction: discord.Interaction, 검색어: str):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("서버 안에서만 사용할 수 있어요!", ephemeral=True)
            return

        # 채널 이름 생성
        safe_name = re.sub(r"[^\w가-힣\s]", "", 검색어).strip()
        channel_name = f"{safe_name}-이미지"[:100]

        # 나만 볼 수 있는 채널 권한 설정
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

        # "김민서 봇" 카테고리 찾기
        category = discord.utils.get(guild.categories, name="김민서 봇")

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                category=category,  # 카테고리 없으면 None → 최상단에 생성
                topic=f"🖼️ '{검색어}' 이미지 피드 | /이미지중지 로 중지",
                nsfw=True,
                reason=f"이미지 피드: {검색어}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 채널 생성 권한이 없어요. 봇에게 **채널 관리** 권한을 주세요!", ephemeral=True
            )
            return

        feed = ImageFeed(channel, 검색어)
        # 이전에 본 이미지 기록 연결 (같은 검색어면 중복 방지)
        if 검색어 not in self.query_history:
            self.query_history[검색어] = set()
        feed.seen_ids = self.query_history[검색어]
        self.feeds[channel.id] = feed
        feed.task = asyncio.create_task(self._run_feed(feed))

        await interaction.followup.send(
            f"✅ {channel.mention} 채널을 만들었어요! 나만 볼 수 있어요.\n멈추려면 그 채널에서 `/이미지중지` 를 입력하세요.",
            ephemeral=True,
        )

    @app_commands.command(name="이미지중지", description="현재 채널의 이미지 자동 피드를 중지합니다.")
    async def stop_slash(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        feed = self.feeds.get(channel_id)

        if feed is None:
            await interaction.response.send_message(
                "이 채널에서 실행 중인 이미지 피드가 없어요.", ephemeral=True
            )
            return

        await self._stop_feed(channel_id)
        await interaction.response.send_message("⏹️ 이미지 피드를 중지했어요.", ephemeral=True)

    @app_commands.command(name="이미지재시작", description="현재 채널에서 이미지 피드를 다시 시작합니다.")
    async def restart_slash(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        channel = interaction.channel

        # 기존 피드가 있으면 중지
        if channel_id in self.feeds:
            old_feed = self.feeds[channel_id]
            query = old_feed.query
            await self._stop_feed(channel_id)
        else:
            await interaction.response.send_message(
                "이 채널에서 실행된 이미지 피드 정보가 없어요. `/이미지` 로 새로 시작해주세요.", ephemeral=True
            )
            return

        # 같은 검색어로 재시작 (seen_ids는 query_history에 유지됨)
        feed = ImageFeed(channel, query)
        if query not in self.query_history:
            self.query_history[query] = set()
        feed.seen_ids = self.query_history[query]
        self.feeds[channel.id] = feed
        feed.task = asyncio.create_task(self._run_feed(feed))

        await interaction.response.send_message(
            f"🔄 **{query}** 이미지 피드를 재시작했어요!", ephemeral=True
        )

    @app_commands.command(name="채널삭제", description="현재 이미지 피드 채널을 삭제합니다.")
    async def delete_slash(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        channel = interaction.channel

        # 피드 실행 중이면 중지
        if channel_id in self.feeds:
            await self._stop_feed(channel_id)

        await interaction.response.send_message("🗑️ 채널을 삭제할게요.", ephemeral=True)
        await asyncio.sleep(1)
        try:
            await channel.delete(reason="이미지 피드 채널 삭제")
        except discord.Forbidden:
            pass

    # ──────────────────────────────────────────
    # 피드 내부 로직
    # ──────────────────────────────────────────

    async def _run_feed(self, feed: ImageFeed):
        """5초마다 이미지를 가져와서 채널에 올립니다."""
        await feed.channel.send(f"🔍 **{feed.query}** 이미지 피드를 시작합니다!")
        consecutive_errors = 0
        posted = 0

        while True:
            try:
                if not feed.queue:
                    # Pixiv API는 offset 약 5000까지만 지원 → 첫 바퀴 종료 처리
                    if not feed.first_pass_done and feed.offset >= 5000:
                        feed.first_pass_done = True
                        feed.offset = 0
                        await feed.channel.send("✅ 검색 결과를 끝까지 다 봤어요. 이제 새로 올라오는 이미지만 보여드릴게요!")
                        await asyncio.sleep(30)
                        continue

                    # 첫 바퀴: 오래된 순, 이후: 최신 순으로 새 이미지만
                    sort = "date_asc" if not feed.first_pass_done else "date_desc"
                    batch = await pixiv.fetch_batch(feed.query, offset=feed.offset, sort=sort)
                    feed.offset += 30

                    new_items = [item for item in batch if item["id"] not in feed.seen_ids]
                    queued_ids = {i["id"] for i in feed.queue}
                    new_items = [item for item in new_items if item["id"] not in queued_ids]
                    feed.queue.extend(new_items)

                    if not feed.queue:
                        if not feed.first_pass_done:
                            # 첫 바퀴 완료 → 이후 최신 순으로 새 이미지만 체크
                            feed.first_pass_done = True
                            feed.offset = 0
                            await feed.channel.send("✅ 모든 이미지를 한 번씩 올렸어요. 이제 새로 올라오는 이미지만 보여드릴게요!")
                        else:
                            # 새 이미지 없음 → 30초 후 다시 체크
                            feed.offset = 0
                        await asyncio.sleep(30)
                        continue

                item = feed.queue.pop(0)
                feed.seen_ids.add(item["id"])

                embed = self._build_embed(item)
                await feed.channel.send(embed=embed)
                consecutive_errors = 0  # 성공 시 에러 카운트 초기화
                posted += 1
                if posted % 20 == 0:
                    print(f"[피드] {feed.query}: {posted}장 업로드됨 "
                          f"(대기열 {len(feed.queue)}, offset {feed.offset})")

            except asyncio.CancelledError:
                return
            except pixiv.PixivError as e:
                consecutive_errors += 1
                print(f"[피드] Pixiv 오류 ({consecutive_errors}/5): {e}")
                if consecutive_errors >= 5:
                    await feed.channel.send(
                        f"⚠️ Pixiv 연결이 계속 실패해서 피드를 중단했어요.\n"
                        f"오류 내용: `{e}`\n"
                        f"`/이미지재시작` 으로 다시 시도해보세요."
                    )
                    return
                await asyncio.sleep(min(60, 10 * consecutive_errors))
                continue
            except discord.HTTPException as e:
                consecutive_errors += 1
                print(f"[피드] Discord 오류 ({consecutive_errors}/5): {e}")
                if consecutive_errors >= 5:
                    print(f"[피드] {feed.query}: Discord 오류 5회 → 피드 중단")
                    return
                await asyncio.sleep(min(60, 10 * consecutive_errors))
                continue
            except Exception as e:
                print(f"[피드] 오류: {e}")
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    await feed.channel.send(
                        f"⚠️ 오류가 계속 발생해서 피드를 중단했어요.\n오류 내용: `{e}`\n봇을 재시작하거나 Pixiv 토큰을 갱신해주세요."
                    )
                    return

            await asyncio.sleep(5)

    async def _stop_feed(self, channel_id: int):
        feed = self.feeds.pop(channel_id, None)
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
        for channel_id in list(self.feeds.keys()):
            await self._stop_feed(channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageSearch(bot))
