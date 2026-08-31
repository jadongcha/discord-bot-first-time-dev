import asyncio
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ 봇 로그인 완료: {bot.user} (ID: {bot.user.id})")
    try:
        for guild in bot.guilds:
            # 1단계: 해당 서버의 낡은 명령어 제거
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f"🗑️ [{guild.name}] 기존 명령어 삭제")

            # 2단계: 현재 cog 명령어를 서버에 등록
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ [{guild.name}] 동기화 완료: {len(synced)}개")
            for cmd in synced:
                print(f"   /{cmd.name}")

    except Exception as e:
        print(f"❌ 오류: {e}")


async def main():
    async with bot:
        await bot.load_extension("cogs.image_search")
        print("✅ image_search cog 로드 완료")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
