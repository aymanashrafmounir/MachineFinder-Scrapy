"""
Enhanced API fetcher with parallel request support for faster data retrieval
"""
import logging
import asyncio
import aiohttp
import json
import time

logger = logging.getLogger(__name__)

async def fetch_via_api_parallel(search_title, search_kind, bcat, max_price, csrf_token, cookies, max_concurrent=5):
    """
    Fetch machines using parallel requests for better performance.
    
    Args:
        search_title: Display title for the category
        search_kind: The search_kind value for API context
        bcat: The bcat value for API criteria
        max_price: Optional maximum price filter
        csrf_token: CSRF token for API authentication
        cookies: Session cookies
        max_concurrent: Number of parallel requests (default: 5)
    """
    base_url = "https://www.machinefinder.com/ww/en-US/mfinder/results?mw=t&lang_code=en-US"
    
    if not search_kind:
        logger.warning(f"API: No search_kind provided for {search_title}, skipping.")
        return []
    
    # Headers
    headers = {
        "authority": "www.machinefinder.com",
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json;charset=UTF-8",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "x-csrf-token": csrf_token,
        "x-requested-with": "XMLHttpRequest"
    }
    
    # Convert cookies dict to string
    cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    headers["cookie"] = cookie_str
    
    all_machines = []
    total_matches = 0
    
    # Initial request to get total count
    initial_payload = {
        "branding": "co",
        "context": {
            "kind": "mf",
            "region": "na",
            "property": "mf_na",
            "search_kind": search_kind
        },
        "criteria": {
            "bcat": [bcat]
        },
        "fw": "pr:hrs:shr:chr:fhr",
        "intro_header": f"Used {search_title} For Sale",
        "locked_criteria": {
            "bcat": [bcat]
        },
        "show_more_start": 0
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            # Get total count
            async with session.post(base_url, headers=headers, json=initial_payload, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"API Error: Status {response.status}")
                    return []
                
                data = await response.json()
                if 'results' not in data:
                    logger.error("API Error: 'results' key missing")
                    return []
                
                results = data['results']
                total_matches = results.get('matches', 0)
                logger.info(f"API: Found {total_matches} total matches for {search_title}")
                
                # Process first page
                if 'machines' in results:
                    new_machines = _process_machines(results['machines'], search_title)
                    all_machines.extend(new_machines)
            
            # Calculate remaining offsets
            offsets = list(range(25, total_matches, 25))
            
            if not offsets:
                return all_machines
            
            # Fetch remaining pages in parallel batches
            for i in range(0, len(offsets), max_concurrent):
                batch_offsets = offsets[i:i + max_concurrent]
                
                # Display progress on same line in console
                items_fetched = min(25 + (i + len(batch_offsets)) * 25, total_matches)
                progress = (items_fetched / total_matches) * 100
                print(f"\rAPI: Progress {int(progress)}% ({items_fetched}/{total_matches})", end='', flush=True)
                
                # Create tasks for parallel requests
                tasks = []
                for offset in batch_offsets:
                    payload = initial_payload.copy()
                    payload['show_more_start'] = offset
                    tasks.append(fetch_single_page(session, base_url, headers, payload))
                
                # Execute parallel requests
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Process results
                for result in batch_results:
                    if isinstance(result, Exception):
                        logger.error(f"API: Request failed: {result}")
                        continue
                    
                    if result and 'machines' in result:
                        new_machines = _process_machines(result['machines'], search_title)
                        all_machines.extend(new_machines)
                
                # Small delay between batches to be polite
                if i + max_concurrent < len(offsets):
                    await asyncio.sleep(0.1)
            
            # Print newline after progress is complete
            print()  # Move to next line after progress completes
    
    except Exception as e:
        logger.error(f"API: Critical error fetching {search_title}: {e}")
        return []
    
    # Client-side filtering for max_price
    if max_price:
        original_count = len(all_machines)
        all_machines = [m for m in all_machines if _parse_price(m['price']) <= max_price]
        logger.info(f"API: Filtered {original_count} -> {len(all_machines)} machines (Max Price: {max_price})")
    
    return all_machines

async def fetch_single_page(session, url, headers, payload, max_retries=3):
    """Fetch a single page of results with retry logic"""
    offset = payload.get('show_more_start', 0)
    
    for attempt in range(max_retries):
        try:
            async with session.post(url, headers=headers, json=payload, timeout=30) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('results', {})
                else:
                    logger.warning(f"HTTP {response.status} for offset {offset}, attempt {attempt + 1}/{max_retries}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)  # Wait before retry
        except asyncio.TimeoutError:
            logger.warning(f"Timeout for offset {offset}, attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Error for offset {offset}, attempt {attempt + 1}/{max_retries}: {type(e).__name__}: {str(e)}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
    
    # All retries failed
    logger.error(f"Failed to fetch page at offset {offset} after {max_retries} attempts")
    return None

def _process_machines(machines_list, search_title):
    """Convert API machine objects to our internal format"""
    processed = []
    for m in machines_list:
        try:
            machine_id = str(m.get('id'))
            
            # Get URL from API response
            relative_url = m.get('url', '')
            if relative_url:
                link = f"https://www.machinefinder.com{relative_url}"
            else:
                link = f"https://www.machinefinder.com/ww/en-US/machines/{machine_id}"
            
            # Extract fields using correct API field names
            title = m.get('label', f"Machine {machine_id}")
            price = m.get('retail', '')
            hours = m.get('hrs', '')
            location = m.get('situ', '').strip()
            image_url = m.get('gallery', '') or m.get('thumb', '')
            
            processed.append({
                'id': machine_id,
                'search_title': search_title,
                'title': title,
                'price': price,
                'location': location,
                'hours': hours,
                'image_url': image_url,
                'link': link
            })
        except Exception as e:
            logger.error(f"Error processing machine object: {e}")
            continue
    
    return processed

def _parse_price(price_str):
    """Parse price string to float for filtering"""
    if not price_str:
        return float('inf')
    try:
        clean_price = str(price_str).replace('$', '').replace(',', '').strip()
        return float(clean_price)
    except:
        return float('inf')
