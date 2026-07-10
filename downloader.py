import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["OSU_CLIENT_ID"]
CLIENT_SECRET = os.environ["OSU_CLIENT_SECRET"]
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_BASE = "https://osu.ppy.sh/api/v2"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIRRORS = [
    "https://api.nerinyan.moe/d/{}",
    "https://catboy.best/d/{}",
]


@dataclass
class MapEntry:
    set_id: int
    artist: str
    title: str
    count: int


def get_token() -> str:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "public",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def gather_entries(user_id: int, num: int) -> list[MapEntry]:
    token = get_token()
    seen: set[int] = set()
    entries: list[MapEntry] = []
    offset = 0

    with httpx.Client(headers={"Authorization": f"Bearer {token}"}) as client:
        while len(entries) < num:
            resp = client.get(
                f"{API_BASE}/users/{user_id}/beatmapsets/most_played",
                params={"limit": 100, "offset": offset},
            )
            resp.raise_for_status()
            page = resp.json()

            if not page:
                break

            for e in page:
                if len(entries) >= num:
                    break
                sid = e["beatmap"]["beatmapset_id"]
                if sid in seen:
                    continue
                seen.add(sid)
                bms = e["beatmapset"]
                entries.append(MapEntry(sid, bms["artist"], bms["title"], e["count"]))

            if len(page) < 100:
                break
            offset += 100

    return entries


async def try_download(client: httpx.AsyncClient, url: str) -> tuple[bytes | None, str]:
    for attempt in range(3):
        try:
            resp = await client.get(url, follow_redirects=True, timeout=60)
            if not resp.is_success:
                reason = f"HTTP {resp.status_code}"
                await asyncio.sleep(2**attempt)
                continue
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                data = resp.json()
                if "download_url" in data:
                    return await try_download(client, data["download_url"])
                return None, f"mirror returned JSON without download_url: {data}"
            if any(t in ct for t in ("osz", "octet-stream", "zip", "x-osu-beatmap-archive")):
                return resp.content, ""
            return None, f"unexpected content-type: {ct}"
        except httpx.TimeoutException:
            reason = "timeout"
            await asyncio.sleep(2**attempt)
        except httpx.HTTPError as exc:
            reason = f"HTTP error: {exc}"
            await asyncio.sleep(2**attempt)
    return None, reason


async def download_one(client: httpx.AsyncClient, entry: MapEntry, dest_dir: Path) -> tuple[bool, str]:
    dest_file = dest_dir / f"{entry.set_id}.osz"
    if dest_file.exists() and dest_file.stat().st_size > 1000:
        return True, ""

    last_reason = "all mirrors exhausted"
    for template in MIRRORS:
        data, reason = await try_download(client, template.format(entry.set_id))
        if data is not None and len(data) > 1000:
            dest_file.write_bytes(data)
            return True, ""
        last_reason = reason if reason else last_reason

    return False, last_reason


async def main() -> None:
    parser = argparse.ArgumentParser(description="Download most-played osu! beatmaps for a user.")
    parser.add_argument("user_id", type=int, help="osu! user ID")
    parser.add_argument("num_beatmaps", type=int, help="number of beatmaps to download")
    parser.add_argument("download_directory", type=Path, help="destination directory")
    parser.add_argument("--workers", "-w", type=int, default=10, help="concurrent downloads (default: 10)")
    args = parser.parse_args()

    user_id = args.user_id
    num = args.num_beatmaps
    dest_dir = args.download_directory
    dest_dir.mkdir(parents=True, exist_ok=True)

    print("Fetching beatmap metadata...")
    entries = gather_entries(user_id, num)
    if not entries:
        print("No beatmaps found.")
        return
    print(f"Found {len(entries)} unique beatmapsets to download\n")

    sem = asyncio.Semaphore(args.workers)
    failed: list[tuple[MapEntry, str]] = []

    async def worker(e: MapEntry) -> None:
        async with sem:
            print(f"({e.count:>5} plays) {e.artist} - {e.title}")
            ok, reason = await download_one(client, e, dest_dir)
            if not ok:
                print(f"  FAILED: {reason}")
                failed.append((e, reason))

    async with httpx.AsyncClient(
        headers={"User-Agent": UA}, timeout=httpx.Timeout(60.0)
    ) as client:
        await asyncio.gather(*[worker(e) for e in entries])

    ok_count = len(entries) - len(failed)
    print(f"\nDownloaded {ok_count}/{len(entries)} beatmapsets to {dest_dir}")

    if failed:
        print("\nFailed beatmaps (run again to retry):")
        for e, reason in failed:
            url = f"https://osu.ppy.sh/beatmapsets/{e.set_id}"
            print(f"  {url}")
            print(f"    ({e.count} plays) {e.artist} - {e.title}")
            print(f"    reason: {reason}")
            print()


if __name__ == "__main__":
    asyncio.run(main())