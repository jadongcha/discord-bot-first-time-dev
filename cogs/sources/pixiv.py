import asyncio
import os
import random
import time

from pixivpy3 import AppPixivAPI

_api = None
_auth_time = 0.0
_AUTH_TTL = 1800  # 30분마다 토큰 선제 갱신 (Pixiv 액세스 토큰은 1시간이면 만료)


class PixivError(Exception):
    """Pixiv API 호출 실패. '결과 없음'과 반드시 구분해야 합니다."""


def get_api(force_reauth: bool = False) -> AppPixivAPI | None:
    global _api, _auth_time

    fresh = _api is not None and (time.time() - _auth_time) < _AUTH_TTL
    if fresh and not force_reauth:
        return _api

    refresh_token = os.getenv("PIXIV_REFRESH_TOKEN")
    if not refresh_token:
        print("[Pixiv] PIXIV_REFRESH_TOKEN이 설정되지 않았습니다.")
        return None

    try:
        api = AppPixivAPI()
        api.auth(refresh_token=refresh_token)
        _api = api
        _auth_time = time.time()
        print("[Pixiv] 인증 성공 (토큰 갱신)")
        return _api
    except Exception as e:
        print(f"[Pixiv] 인증 실패: {e}")
        _api = None
        _auth_time = 0.0
        return None


def _make_image_url(illust: dict) -> str | None:
    """일러스트에서 이미지 URL을 추출하고 프록시 URL로 변환합니다."""
    # ugoira(움짤)는 첫 프레임만 나오므로 제외
    if illust.get("type") == "ugoira":
        return None

    urls = illust.get("image_urls", {})
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


def _search_sync(query: str, offset: int, safe: bool, sort: str) -> list[dict]:
    api = get_api()
    if api is None:
        raise PixivError("Pixiv 인증 실패 (PIXIV_REFRESH_TOKEN 확인 필요)")

    result = api.search_illust(
        query,
        search_target="exact_match_for_tags",
        sort=sort,
        offset=offset,
    )

    if result is None:
        raise PixivError("Pixiv 응답이 비어 있습니다")
    if result.get("error"):
        raise PixivError(f"Pixiv API 오류: {result.get('error')}")
    if "illusts" not in result:
        raise PixivError(f"예상치 못한 응답 형식: {list(result.keys())[:5]}")

    illusts = result.get("illusts") or []
    if safe:
        illusts = [i for i in illusts if i.get("x_restrict") == 0]

    results = []
    for illust in illusts:
        item = _build_result(illust)
        if item:
            results.append(item)
    return results


async def fetch_batch(query: str, offset: int = 0, safe: bool = False, sort: str = "date_asc") -> list[dict]:
    """성공 시 리스트를 반환합니다.

    빈 리스트 = 정말로 결과가 없음.
    실패 = PixivError 발생 (호출부가 '결과 없음'으로 오해하지 않도록).
    """
    loop = asyncio.get_running_loop()
    last_err: Exception | None = None

    for attempt in range(3):
        if attempt > 0:
            get_api(force_reauth=True)
            await asyncio.sleep(3 * attempt)
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _search_sync, query, offset, safe, sort),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            last_err = PixivError("요청 타임아웃 (30초)")
            print(f"[Pixiv] 타임아웃 (시도 {attempt + 1}/3)")
        except PixivError as e:
            last_err = e
            print(f"[Pixiv] {e} (시도 {attempt + 1}/3)")
        except Exception as e:
            last_err = PixivError(f"{type(e).__name__}: {e}")
            print(f"[Pixiv] 오류: {e} (시도 {attempt + 1}/3)")

    raise last_err or PixivError("알 수 없는 오류")


async def search(query: str, safe: bool = True) -> dict | None:
    try:
        results = await fetch_batch(query, safe=safe)
    except PixivError as e:
        print(f"[Pixiv] search 실패: {e}")
        return None
    if not results:
        return None
    return random.choice(results)
