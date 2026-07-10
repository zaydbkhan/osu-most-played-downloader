import argparse
import asyncio
import os
import random
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

    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(30.0)) as client:
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
                beatmap = e.get("beatmap")
                if not beatmap:
                    continue
                sid = beatmap.get("beatmapset_id")
                if not isinstance(sid, int):
                    continue
                if sid in seen:
                    continue
                seen.add(sid)
                bms = e.get("beatmapset")
                if not bms:
                    continue
                entries.append(
                    MapEntry(
                        sid,
                        bms.get("artist", "?"),
                        bms.get("title", "?"),
                        e.get("count", 0),
                    )
                )

            if len(page) < 100:
                break
            offset += 100

    return entries


async def try_download(client: httpx.AsyncClient, url: str, dest_path: Path) -> tuple[bool, str]:
    tmp = dest_path.with_suffix(".tmp")
    reason = ""
    for attempt in range(3):
        try:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if not resp.is_success:
                    if resp.status_code in (429, 502, 503, 504):
                        reason = f"HTTP {resp.status_code}"
                        await asyncio.sleep(2**attempt)
                        continue
                    return False, f"HTTP {resp.status_code}"

                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    body = await resp.aread()
                    j = json.loads(body)
                    if "download_url" in j:
                        return await try_download(client, j["download_url"], dest_path)
                    return False, f"mirror returned JSON without download_url"

                if not any(t in ct for t in ("osz", "octet-stream", "zip", "x-osu-beatmap-archive")):
                    return False, f"unexpected content-type: {ct}"

                body = await resp.aread()
                tmp.unlink(missing_ok=True)
                await asyncio.to_thread(tmp.write_bytes, body)
                tmp.rename(dest_path)
                return True, ""

        except httpx.TimeoutException:
            reason = "timeout"
            tmp.unlink(missing_ok=True)
            await asyncio.sleep(2**attempt)
        except httpx.HTTPError as exc:
            reason = f"HTTP error: {exc}"
            tmp.unlink(missing_ok=True)
            await asyncio.sleep(2**attempt)

    tmp.unlink(missing_ok=True)
    return False, reason


async def download_one(client: httpx.AsyncClient, entry: MapEntry, dest_dir: Path) -> tuple[bool, str]:
    dest_file = dest_dir / f"{entry.set_id}.osz"
    if dest_file.exists() and dest_file.stat().st_size > 1000:
        return True, ""

    last_reason = "all mirrors exhausted"
    for template in MIRRORS:
        ok, reason = await try_download(client, template.format(entry.set_id), dest_file)
        if ok:
            return True, ""
        if reason and "502" not in reason:
            last_reason = reason
        elif reason and last_reason == "all mirrors exhausted":
            last_reason = reason

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
    print(f"Found {len(entries)} unique beatmapsets to download")

    existing_ids = {
        int(f.stem) for f in dest_dir.glob("*.osz") if f.stat().st_size > 1000
    }
    entries = [e for e in entries if e.set_id not in existing_ids]
    if not entries:
        print("All beatmaps already downloaded.")
        return
    print(f"({len(existing_ids)} already cached, {len(entries)} to download)\n")

    sem = asyncio.Semaphore(args.workers)
    failed: list[tuple[MapEntry, str]] = []

    async def attempt(entry: MapEntry) -> tuple[bool, str]:
        async with sem:
            ok, reason = await asyncio.wait_for(
                download_one(client, entry, dest_dir), timeout=120
            )
            return ok, reason

    async def do_pass(
        items: list[tuple[MapEntry, str | None]],
        staggered: bool,
    ) -> list[tuple[MapEntry, str]]:
        results: list[tuple[MapEntry, str]] = []

        async def worker(e: MapEntry, old_reason: str | None, idx: int) -> None:
            if staggered:
                await asyncio.sleep(random.random() * (idx % 5))
            label = f"(old: {old_reason}) " if old_reason else ""
            print(f"{label}({e.count:>5} plays) {e.artist} - {e.title}")
            try:
                ok, reason = await attempt(e)
            except asyncio.TimeoutError:
                ok, reason = False, "timed out (>120s)"
            if not ok:
                print(f"  FAILED: {reason}")
                results.append((e, reason))
            return

        tasks = [
            worker(e, old_reason, i)
            for i, (e, old_reason) in enumerate(items)
        ]
        await asyncio.gather(*tasks)
        return results

    limits = httpx.Limits(
        max_connections=args.workers + 5,
        max_keepalive_connections=args.workers,
    )
    async with httpx.AsyncClient(
        headers={"User-Agent": UA},
        timeout=httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0),
        limits=limits,
    ) as client:
        failed = await do_pass([(e, None) for e in entries], staggered=True)

        ok_count = len(entries) - len(failed)
        print(f"\nPass 1: {ok_count}/{len(entries)}")

        if failed:
            retryable = [(e, r) for e, r in failed if "502" not in r]
            skipped_502 = [(e, r) for e, r in failed if "502" in r]
            if retryable:
                print(f"\nRetrying {len(retryable)} (skipping {len(skipped_502)} with HTTP 502)...")
                retried = await do_pass(retryable, staggered=False)
                failed = retried + skipped_502

            ok_count = len(entries) - len(failed)
            print(f"\nPass 2: {ok_count}/{len(entries)}")

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