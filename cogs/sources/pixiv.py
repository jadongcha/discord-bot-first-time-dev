import asyncio
import os
import random
from pixivpy3 import AppPixivAPI

_api = None

def get_api(force_reauth: bool = False) -> AppPixivAPI | None:
    global _api
    if _api is not None and not force_reauth:
        return _api

    refresh_token = os.getenv("PIXIV_REFRESH_TOKEN")
    if not refresh_token:
        print("[Pixiv] PIXIV_REFRESH_TOKEN이 설정되지 않았습니다.")
        return None

    try:
        _api = AppPixivAPI()
        _api.auth(refresh_token=refresh_token)
        print("[Pixiv] 인증 성공")
        return _api
    except Exception as e:
        print(f"[Pixiv] 인증 실패: {e}")
        _api = None
        return None


def _make_image_url(illust: dict) -> str | None:
    """일러스트에서 이미지 URL을 추출하고 프록시 URL로 변환합니다."""
    # ugoira(움짤)는 첫 프레임만 나오므로 제외
    if illust.get("type") == "ugoira":
        return None

    # 단일 페이지 이미지
    urls = illust.get("image_urls", {})

    # large → medium → square_medium 순으로 시도
    url = (
        urls.get("large")
        or urls.get("medium")
        or urls.get("square_medium")
    )

    # 멀티페이지는 첫 번째 페이지 사용
    if not url:
        pages = illust.get("meta_pages", [])
        if pages:
            page_urls = pages[0].get("image_urls", {})
            url = page_urls.get("large") or page_urls.get("medium")

    if not url:
        return None

    # Pixiv 이미지는 Referer 필요 → i.pixiv.re 프록시 사용
    return url.replace("https://i.pximg.net", "https://i.pixiv.re")


def _build_result(illust: dict) -> dict | None:
    url = _make_image_url(illust)
    if not url:
        return None

    return {
        "id": illust["id"],
        "url": url,
        "source": "Pixiv",
        "title": illust.get("title", ""),
        "author": illust.get("user", {}).get("name", ""),
        "tags": ", ".join(t["name"] for t in illust.get("tags", [])[:5]),
        "page_url": f"https://www.pixiv.net/artworks/{illust['id']}",
    }


async def fetch_batch(query: str, offset: int = 0, safe: bool = True) -> list[dict]:
    def _fetch():
        api = get_api()
        if api is None:
            return []

        result = api.search_illust(
            query,
            search_target="partial_match_for_tags",
            sort="popular_desc",
            offset=offset,
        )

        illusts = result.get("illusts", [])
        if safe:
            illusts = [i for i in illusts if i.get("x_restrict") == 0]

        results = []
        for illust in illusts:
            item = _build_result(illust)
            if item:
                results.append(item)
        return results

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _fetch)
    except Exception as e:
        print(f"[Pixiv] fetch_batch 오류: {e} → 토큰 재인증 시도")
        get_api(force_reauth=True)
        try:
            return await loop.run_in_executor(None, _fetch)
        except Exception as e2:
            print(f"[Pixiv] 재시도 실패: {e2}")
            return []


async def search(query: str, safe: bool = True) -> dict | None:
    results = await fetch_batch(query, safe=safe)
    if not results:
        return None
    return random.choice(results)
