"""
Final script to fetch ALL categories using parallel requests (5 concurrent)
Shows timing for each category
"""
import asyncio
import json
import logging
import re
import requests
import os
import time
from datetime import datetime
from api_mode_fetch_parallel import fetch_via_api_parallel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_csrf_and_cookies():
    url = "https://www.machinefinder.com/"
    response = requests.get(url)
    response.raise_for_status()
    
    # Extract CSRF token from category page
    cat_url = "https://www.machinefinder.com/ww/en-US/categories/used-excavators"
    cat_resp = requests.get(cat_url, cookies=response.cookies)
    
    match = re.search(r'<meta name="csrf-token" (?:enhanced="true" )?content="([^"]+)"', cat_resp.text)
    if match:
        token = match.group(1)
        return token, cat_resp.cookies
    else:
        raise ValueError("Could not find CSRF token on category page")

async def main():
    # Load config
    config_path = 'api_config.json'
    with open(config_path, 'r') as f:
        config = json.load(f)
        
    try:
        logger.info("="*80)
        logger.info("FETCHING ALL CATEGORIES WITH PARALLEL REQUESTS (5 concurrent)")
        logger.info("="*80)
        csrf_token, cookies = get_csrf_and_cookies()
        logger.info(f"✓ CSRF token acquired\n")
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        return

    # Create output directory for JSON files
    output_dir = "machine_data"
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Output directory: {output_dir}\n")

    # Track statistics
    total_start = time.time()
    total_categories = 0
    total_machines = 0
    category_stats = []

    # Iterate over all groups and all items
    machine_groups = config.get('machine_groups', {})
    for group_id, items in machine_groups.items():
        logger.info(f"{'─'*80}")
        logger.info(f"📁 GROUP {group_id}")
        logger.info(f"{'─'*80}")
        
        for item in items:
            title = item.get('title')
            search_kind = item.get('search_kind')
            bcat = item.get('bcat', search_kind)
            total_categories += 1
            
            logger.info(f"\n🔍 Fetching: {title}")
            
            # Time this category
            cat_start = time.time()
            
            try:
                # Call the parallel fetcher with 5 concurrent requests
                machines = await fetch_via_api_parallel(
                    search_title=title,
                    search_kind=search_kind,
                    bcat=bcat,
                    max_price=None, 
                    csrf_token=csrf_token,
                    cookies=cookies,
                    max_concurrent=5
                )
                
                cat_elapsed = time.time() - cat_start
                
                # Save to JSON file
                filename = f"{search_kind}.json"
                filepath = os.path.join(output_dir, filename)
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(machines, f, indent=2, ensure_ascii=False)
                
                # Update statistics
                machine_count = len(machines)
                total_machines += machine_count
                
                # Calculate speed
                speed = machine_count / cat_elapsed if cat_elapsed > 0 else 0
                
                category_stats.append({
                    'title': title,
                    'search_kind': search_kind,
                    'count': machine_count,
                    'time_seconds': round(cat_elapsed, 2),
                    'speed_items_per_sec': round(speed, 1),
                    'file': filename,
                    'status': 'SUCCESS'
                })
                
                logger.info(f"✓ Saved {machine_count} machines to {filename}")
                logger.info(f"  ⏱️  Time: {cat_elapsed:.2f}s | Speed: {speed:.1f} items/s")
                
            except Exception as e:
                cat_elapsed = time.time() - cat_start
                logger.error(f"✗ Failed to fetch {title}: {e}")
                category_stats.append({
                    'title': title,
                    'search_kind': search_kind,
                    'count': 0,
                    'time_seconds': round(cat_elapsed, 2),
                    'status': 'FAILED',
                    'error': str(e)
                })

    total_elapsed = time.time() - total_start
    
    # Print final summary
    logger.info(f"\n\n{'═'*80}")
    logger.info("📊 FINAL SUMMARY")
    logger.info(f"{'═'*80}")
    
    for stat in category_stats:
        if stat['status'] == 'SUCCESS':
            logger.info(f"✓ {stat['title']:30} {stat['count']:5} machines | {stat['time_seconds']:6.2f}s | {stat['speed_items_per_sec']:6.1f} items/s")
        else:
            logger.info(f"✗ {stat['title']:30} FAILED")
    
    logger.info(f"\n{'─'*80}")
    logger.info(f"Total Categories:    {total_categories}")
    logger.info(f"Total Machines:      {total_machines:,}")
    logger.info(f"Total Time:          {total_elapsed:.2f}s ({total_elapsed/60:.1f} minutes)")
    logger.info(f"Overall Speed:       {total_machines/total_elapsed:.1f} items/s")
    logger.info(f"{'═'*80}\n")
    
    # Save summary
    summary = {
        'timestamp': datetime.now().isoformat(),
        'total_categories': total_categories,
        'total_machines': total_machines,
        'total_time_seconds': round(total_elapsed, 2),
        'overall_speed': round(total_machines/total_elapsed, 2),
        'categories': category_stats
    }
    
    with open(os.path.join(output_dir, '_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    logger.info(f"✓ Summary saved to: {output_dir}/_summary.json")

if __name__ == "__main__":
    asyncio.run(main())
