import asyncio
import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import List, Dict, Set

from db_helper import init_db, upsert_item, get_all_slugs, delete_missing, get_total_count, is_first_run, mark_first_run_complete
from config_loader import load_config
from api_mode_fetch_parallel import fetch_via_api_parallel
from telegram_notifier import TelegramNotifier

# ---------------------------------------------------------------------------
# Logging setup (two rotating loggers: normal and timing)
# ---------------------------------------------------------------------------
def _setup_loggers(log_dir: str, max_mb: int) -> None:
    os.makedirs(log_dir, exist_ok=True)
    normal_path = os.path.join(log_dir, "normal.log")
    timing_path = os.path.join(log_dir, "timing.log")

    normal_handler = RotatingFileHandler(normal_path, maxBytes=max_mb * 1024 * 1024, backupCount=1)
    timing_handler = RotatingFileHandler(timing_path, maxBytes=max_mb * 1024 * 1024, backupCount=1)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    normal_handler.setFormatter(formatter)
    timing_handler.setFormatter(formatter)

    normal_logger = logging.getLogger("normal")
    timing_logger = logging.getLogger("timing")
    normal_logger.setLevel(logging.INFO)
    timing_logger.setLevel(logging.INFO)
    normal_logger.addHandler(normal_handler)
    timing_logger.addHandler(timing_handler)

# ---------------------------------------------------------------------------
# Helper to extract the slug (unique part) from a full URL
# ---------------------------------------------------------------------------
def _extract_slug(url: str) -> str:
    # URL format: https://www.machinefinder.com/ww/en-US/machines/<slug>
    return url.rstrip("/").split("/")[-1]

# ---------------------------------------------------------------------------
# Core cycle implementation
# ---------------------------------------------------------------------------
# Global cycle counter
_cycle_number = 0

async def run_cycle(config: Dict) -> None:
    global _cycle_number
    _cycle_number += 1
    
    normal_logger = logging.getLogger("normal")
    timing_logger = logging.getLogger("timing")

    # Record cycle start time
    cycle_start_time = time.time()
    
    normal_logger.info("=== Cycle start ===")
    timing_logger.info(f"{'='*60}")
    timing_logger.info(f"Cycle {_cycle_number} - Start")

    # Initialise DB if needed
    init_db()
    
    # Check if this is the first run
    first_run = is_first_run()
    if first_run:
        normal_logger.info("FIRST RUN DETECTED - Will save all items without notifications")
    
    stored_slugs = get_all_slugs()
    fetched_slugs: Set[str] = set()
    new_items: List[Dict] = []
    new_items_slugs: Set[str] = set()  # Track new items to avoid duplicates

    # Prepare Telegram notifier
    notifier = TelegramNotifier(
        bot_token=config["telegram_bot_token"],
        chat_id=config["telegram_chat_id"],
    )

    # CSRF & cookies – reuse the same logic as fetch_all_parallel
    from fetch_all_parallel import get_csrf_and_cookies
    csrf_token, cookies = get_csrf_and_cookies()

    machine_groups = config.get("machine_groups", {})
    for group_id, items in machine_groups.items():
        for item_cfg in items:
            # --- per-search timing & stats ---
            search_start = time.time()
            title = item_cfg.get("title")
            search_kind = item_cfg.get("search_kind")
            bcat = item_cfg.get("bcat", search_kind)
            normal_logger.info(f"Fetching {title} (group {group_id})")
            
            # Get existing items for this search BEFORE fetching
            from db_helper import get_slugs_by_search, delete_missing_by_search
            existing_slugs_for_search = get_slugs_by_search(title)
            items_before = len(existing_slugs_for_search)
            
            try:
                machines = await fetch_via_api_parallel(
                    search_title=title,
                    search_kind=search_kind,
                    bcat=bcat,
                    max_price=None,
                    csrf_token=csrf_token,
                    cookies=cookies,
                    max_concurrent=5,
                )
            except Exception as e:
                normal_logger.error(f"Failed to fetch {title}: {e}")
                continue

            # Process fetched machines
            current_slugs_for_search = set()
            new_items_count = 0
            
            for m in machines:
                link = m.get("link", "")
                if not link:
                    continue
                slug = _extract_slug(link)
                if not slug:
                    continue
                
                current_slugs_for_search.add(slug)
                fetched_slugs.add(slug)
                
                record = {
                    "slug": slug,
                    "title": m.get("title"),
                    "price": m.get("price"),
                    "location": m.get("location"),
                    "hours": m.get("hours"),
                    "link": link,
                    "search_name": title,
                    "image_url": m.get("image_url"),
                }
                upsert_item(slug, title)
                
                # Only add to new_items if not seen before (in DB or this cycle)
                if slug not in stored_slugs and slug not in new_items_slugs:
                    new_items.append(record)
                    new_items_slugs.add(slug)
                    stored_slugs.add(slug)  # Update to avoid duplicates in next search
                    new_items_count += 1
            
            # Delete stale items for this search
            deleted_count = delete_missing_by_search(title, current_slugs_for_search)
            
            # Get count after cleanup
            items_after = len(current_slugs_for_search)
            
            # End of processing this search
            search_elapsed = time.time() - search_start
            fetched_count = len(machines)
            
            normal_logger.info(
                f"Search '{title}' completed: time_taken={search_elapsed:.2f}s, "
                f"fetched={fetched_count}, items_before={items_before}, "
                f"new={new_items_count}, deleted={deleted_count}, items_after={items_after}"
            )

    # Send notifications for newly discovered items (SKIP ON FIRST RUN)
    if first_run:
        normal_logger.info(f"First run complete: Saved {len(new_items)} items to database (no notifications sent)")
        # Mark first run as complete
        mark_first_run_complete()
    elif new_items:
        # Group by search name for nicer messages
        grouped: Dict[str, List[Dict]] = {}
        for itm in new_items:
            grouped.setdefault(itm["search_name"], []).append(itm)
        for search_name, items in grouped.items():
            await notifier.send_new_items_notification(search_name, items)
        normal_logger.info(f"Sent notifications for {len(new_items)} new items")
    else:
        normal_logger.info("No new items found this cycle")

    # Calculate and log cycle duration
    cycle_duration = time.time() - cycle_start_time
    timing_logger.info(f"Cycle {_cycle_number} - Completed in {cycle_duration:.2f}s")
    timing_logger.info(f"{'='*60}\n")
    normal_logger.info("=== Cycle end ===")

# ---------------------------------------------------------------------------
# Entry point – runs indefinitely respecting the configured delay
# ---------------------------------------------------------------------------
async def main() -> None:
    cfg = load_config()
    _setup_loggers(cfg.get("log_dir", "logs"), cfg.get("max_log_size_mb", 50))
    delay = cfg.get("cycle_delay_seconds", 3600)
    while True:
        await run_cycle(cfg)
        await asyncio.sleep(delay)

if __name__ == "__main__":
    asyncio.run(main())
