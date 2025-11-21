import json
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import sys
import re
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from database import MachinefinderDB
from telegram_notifier import TelegramNotifier

# Configure logging with rotation (max 2KB per file)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

# Rotating file handler (max 2KB, keep 1 backup)
file_handler = RotatingFileHandler(
    'scraper_log.txt',
    maxBytes=20480,  # 20KB max
    backupCount=1,   # Keep only 1 backup file
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# ⏱️ TIMING LOGGER - Dedicated logger for performance tracking (10MB limit)
timing_logger = logging.getLogger('timing')
timing_logger.setLevel(logging.INFO)
timing_logger.propagate = False  # Don't send to parent logger

timing_handler = RotatingFileHandler(
    'timing_log.txt',
    maxBytes=10 * 1024 * 1024,  # 10MB max
    backupCount=3,               # Keep 3 backup files
    encoding='utf-8'
)
timing_handler.setLevel(logging.INFO)
timing_formatter = logging.Formatter('%(asctime)s - %(message)s')
timing_handler.setFormatter(timing_formatter)
timing_logger.addHandler(timing_handler)


class MachinefinderMonitor:
    def __init__(self, config_path='config.json', machine_id=None):
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Set machine ID (used to select URLs from machine_groups)
        self.machine_id = machine_id
        
        # Initialize components
        self.db = MachinefinderDB(self.config['database']['path'])
        self.notifier = TelegramNotifier(
            self.config['telegram']['bot_token'],
            self.config['telegram']['chat_id']
        )
        
        # ⏱️ Cycle counter for timing logs
        self.cycle_count = 0
    
    async def run_once(self):
        """Run a single scraping cycle for all configured URLs"""
        # ⏱️ TIMING DEBUGGER: Start tracking cycle time
        import time
        cycle_start_time = time.time()
        
        # Increment cycle counter
        self.cycle_count += 1
        
        logger.info("="*60)
        logger.info(f"🚀 Starting scraping cycle #{self.cycle_count}...")
        logger.info("="*60)
        
        # ⏱️ Log cycle start in timing log
        timing_logger.info("="*80)
        timing_logger.info(f"CYCLE #{self.cycle_count} STARTED")
        timing_logger.info("="*80)
        
        # Test Telegram connection
        telegram_ok = await self.notifier.test_connection()
        if not telegram_ok:
            logger.warning("Telegram connection failed, but continuing with scraping...")
        
        # Get delay between URLs from config (default 5 seconds - OPTIMIZED)
        delay_between_urls = self.config.get('delay_between_urls_seconds', 5)
        
        # Get URLs based on machine ID (if specified)
        if self.machine_id == 'ALL' and 'machine_groups' in self.config:
            # Special case: combine ALL URLs from all machine groups
            search_urls = []
            for machine_group in self.config['machine_groups'].values():
                search_urls.extend(machine_group)
            logger.info(f"🖥️ Running as Machine ALL with {len(search_urls)} URLs (from all groups)")
        elif self.machine_id and 'machine_groups' in self.config:
            search_urls = self.config['machine_groups'].get(str(self.machine_id), [])
            logger.info(f"🖥️ Running as Machine #{self.machine_id} with {len(search_urls)} URLs")
        else:
            # Fallback to old config format
            search_urls = self.config.get('search_urls', [])
        
        for index, search_config in enumerate(search_urls):
            search_title = search_config['title']
            search_url = search_config['url']
            max_price = search_config.get('max_price')  # Get max_price (or None)
            
            # ⏱️ Start timing this URL
            url_start_time = time.time()
            
            logger.info(f"Scraping: {search_title} - {search_url}")
            
            # CHECK: Is this the first run for this category?
            existing_count = len(self.db.get_existing_ids(search_title))
            is_first_run = (existing_count == 0)
            
            if is_first_run:
                logger.info(f"🔵 FIRST RUN detected for '{search_title}' - will populate database WITHOUT notifications")
            
            # Run scraper for this URL
            machines = await self._scrape_url(search_url, search_title, max_price)
            
            # Process scraped machines using OPTIMIZED batch processing
            # OLD: N queries (slow with 1000+ items)
            # NEW: 3 queries total (10-100x faster!)
            new_machines = self.db.batch_process_machines(machines, search_title)
            
            # Send notifications ONLY if NOT first run
            if new_machines and not is_first_run:
                logger.info(f"Found {len(new_machines)} new machine(s) for {search_title}")
                
                # Log each new machine (only when NOT first run)
                for machine in new_machines:
                    logger.info(f"  → New: {machine['title']}")
                
                # TEST MODE: Only send 1 notification for testing
                TEST_MODE = False
                if TEST_MODE:
                    logger.info("TEST MODE: Sending only 1 notification")
                    await self.notifier.send_new_items_notification(search_title, new_machines[:1])
                else:
                    await self.notifier.send_new_items_notification(search_title, new_machines)
            elif new_machines and is_first_run:
                logger.info(f"✅ Database populated with {len(new_machines)} existing item(s) for {search_title} (no notifications sent)")
            else:
                logger.info(f"No new machines found for {search_title}")
            
            # ⏱️ Display timing for this URL
            url_duration = time.time() - url_start_time
            url_minutes = int(url_duration // 60)
            url_seconds = int(url_duration % 60)
            logger.info(f"⏱️  URL completed in: {url_minutes}m {url_seconds}s ({url_duration:.1f}s)")
            
            # ⏱️ Log to timing log
            timing_logger.info(f"  [{search_title}] → {url_minutes}m {url_seconds}s ({url_duration:.1f}s) | {len(machines)} items | {len(new_machines)} new")
            
            # CLEANUP: Remove old machines not in current scrape
            cleanup_enabled = self.config.get('cleanup_enabled', True)
            if cleanup_enabled:
                if len(machines) == 0:
                    # Safety: Send alert if 0 results
                    alert_msg = f"Zero results returned for '{search_title}'. Skipping cleanup to prevent data loss."
                    await self.notifier.send_alert(alert_msg)
                    logger.warning(f"⚠️ Zero results for {search_title}, cleanup skipped!")
                else:
                    # Normal cleanup: remove machines not in current scrape
                    current_ids = {m['id'] for m in machines}
                    deleted_count = self.db.cleanup_missing_machines(search_title, current_ids)
                    if deleted_count > 0:
                        logger.info(f"Cleaned up {deleted_count} old machine(s) for {search_title}")
            
            # Add delay between URLs (except after the last one)
            if index < len(search_urls) - 1:
                logger.info(f"Waiting {delay_between_urls} seconds before next URL...")
                await asyncio.sleep(delay_between_urls)
        
        # ⏱️ TIMING DEBUGGER: Calculate and display cycle duration
        cycle_end_time = time.time()
        cycle_duration_seconds = cycle_end_time - cycle_start_time
        cycle_minutes = int(cycle_duration_seconds // 60)
        cycle_seconds = int(cycle_duration_seconds % 60)
        
        logger.info("="*60)
        logger.info(f"✅ Scraping cycle completed!")
        logger.info(f"⏱️  CYCLE DURATION: {cycle_minutes} minutes, {cycle_seconds} seconds ({cycle_duration_seconds:.2f}s total)")
        logger.info("="*60)
        
        # ⏱️ Log cycle summary to timing log
        timing_logger.info("")
        timing_logger.info(f"CYCLE #{self.cycle_count} COMPLETED → {cycle_minutes}m {cycle_seconds}s ({cycle_duration_seconds:.1f}s total)")
        timing_logger.info("="*80)
        timing_logger.info("")  # Empty line for readability
    
    async def _scrape_url(self, search_url, search_title, max_price=None):
        """Scrape machines from a single URL using Playwright"""
        machines = []
        
        logger.info("🔧 Launching memory-optimized browser...")
        
        async with async_playwright() as p:
            # Launch browser with OPTIMIZED memory-efficient settings
            # Optimized for low-resource servers (1-2GB RAM)
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',  # Use /tmp instead of /dev/shm (critical for low RAM)
                    '--disable-gpu',  # Disable GPU hardware acceleration
                    '--disable-gpu-compositing',  # Additional GPU optimization
                    '--disable-software-rasterizer',
                    '--disable-extensions',
                    '--disable-plugins',
                    '--no-sandbox',  # Required for some Linux environments
                    '--disable-setuid-sandbox',
                    '--disable-background-networking',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--disable-web-security',  # May help with CORS issues
                    '--disable-features=IsolateOrigins,site-per-process,VizDisplayCompositor',  # Reduce memory
                    '--js-flags=--max-old-space-size=128',  # Limit JavaScript heap to 128MB (reduced from 256MB)
                    '--disable-logging',
                    '--disable-permissions-api',
                    '--disable-notifications',
                    '--disable-offer-store-unmasked-wallet-cards',
                    '--disable-speech-api',
                    '--hide-scrollbars',
                    '--mute-audio',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--metrics-recording-only',
                    '--disable-hang-monitor',
                    '--disable-prompt-on-repost',
                    '--disable-sync',
                    '--disable-translate',
                    '--safebrowsing-disable-auto-update',
                    '--disable-client-side-phishing-detection',
                ]
            )
            
            # Create context with reduced viewport (optimized for memory)
            context = await browser.new_context(
                viewport={'width': 1366, 'height': 768},  # Reduced from 1920x1080 to save memory
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            page = await context.new_page()
            
            try:
                # Navigate to the page (use domcontentloaded to save memory)
                logger.info(f"Loading page: {search_url}")
                response = await page.goto(search_url, wait_until='domcontentloaded', timeout=45000)
                logger.debug(f"Page loaded with status: {response.status}")
                
                # Wait for Angular to bootstrap and initial content to load (OPTIMIZED)
                logger.debug("Waiting for page to become interactive...")
                await page.wait_for_timeout(3000)  # OPTIMIZED: 3s is enough for Angular to start
                
                # APPLY PRICE FILTER IF CONFIGURED
                if max_price:
                    logger.debug(f"Applying max price filter: ${max_price:,}")
                    try:
                        # Step 1: Click on "Hours & Price" filter panel
                        logger.debug("Looking for Hours & Price filter panel...")
                        hours_price_panel = await page.query_selector('div.inner:has-text("Hours")')
                        
                        if hours_price_panel:
                            logger.debug("Clicking Hours & Price panel...")
                            await hours_price_panel.click()
                            await page.wait_for_timeout(800)  # OPTIMIZED: 800ms is enough
                            
                            # Step 2: Enter max price in the input field
                            logger.debug(f"Entering max price: {max_price}")
                            price_input = await page.query_selector('input[ng-model="filters.price_max"]')
                            
                            if price_input:
                                await price_input.click()
                                await price_input.fill(str(max_price))
                                await page.wait_for_timeout(400)  # OPTIMIZED: 400ms is enough
                                
                                # Step 3: Click the VIEW button using JavaScript (most reliable method)
                                logger.debug("Clicking VIEW button via JavaScript...")
                                try:
                                    clicked = await page.evaluate("""
                                        () => {
                                            const button = document.querySelector('span.save-button.finish-button');
                                            if (button) {
                                                button.click();
                                                return true;
                                            }
                                            return false;
                                        }
                                    """)
                                    
                                    if clicked:
                                        logger.debug("✓ Clicked VIEW button!")
                                        
                                        # Wait for filtered results to load (OPTIMIZED)
                                        logger.debug("Waiting for filtered results to load...")
                                        await page.wait_for_timeout(2000)  # OPTIMIZED: 2s instead of 5s
                                        
                                        try:
                                            await page.wait_for_selector('div.tile', timeout=10000)
                                            logger.debug("✓ Price filter applied successfully!")
                                        except:
                                            logger.warning("Tiles not found after filter, but continuing...")
                                    else:
                                        logger.warning("VIEW button not found, scraping without filter...")
                                        
                                except Exception as e:
                                    logger.error(f"Error clicking VIEW button: {e}")
                                    logger.warning("Scraping without price filter...")
                            else:
                                logger.warning("Could not find price input field, scraping without filter...")
                        else:
                            logger.warning("Could not find Hours & Price panel, scraping without filter...")
                    
                    except Exception as e:
                        logger.error(f"Error applying price filter: {e}")
                        logger.warning("Continuing with scraping without price filter...")
                
                # Try multiple selector strategies
                logger.debug("Looking for machine listings...")
                
                # Strategy 1: Look for the ng-repeat links
                tiles_found = False
                try:
                    await page.wait_for_selector('a[ng-repeat*="results_machines"]', timeout=10000)
                    logger.debug("✓ Found machine links via ng-repeat selector!")
                    tiles_found = True
                except:
                    logger.debug("✗ ng-repeat selector didn't work")
                
                # Strategy 2: Look for any machine tiles
                if not tiles_found:
                    try:
                        await page.wait_for_selector('div.tile', timeout=10000)
                        logger.debug("✓ Found tiles via div.tile selector!")
                        tiles_found = True
                    except:
                        logger.debug("✗ div.tile selector didn't work")
                
                # Strategy 3: Look for machine labels (the title divs)
                if not tiles_found:
                    try:
                        await page.wait_for_selector('div.label', timeout=10000)
                        logger.debug("✓ Found elements via div.label selector!")
                        tiles_found = True
                    except:
                        logger.debug("✗ div.label selector didn't work")
                
                if not tiles_found:
                    logger.error("Failed to find machine listings with any selector!")
                    # Save screenshot for debugging
                    await page.screenshot(path="error_screenshot.png", full_page=True)
                    logger.info("Saved error screenshot to error_screenshot.png")
                    # Try to get page content for debugging
                    content = await page.content()
                    logger.info(f"Page HTML length: {len(content)} characters")
                    if len(content) < 1000:
                        logger.error("Page content is suspiciously small, might be a redirect or error page")
                
                # STEP 1: Click "SHOW MORE" until no more buttons available
                click_count = 0
                logger.debug("Starting to load all items by clicking SHOW MORE...")
                
                while True:
                    try:
                        # Look for the show more button (excluding hidden ones)
                        show_more_button = await page.query_selector('div.show-more-tile:not(.ng-hide)')
                        
                        if show_more_button:
                            is_visible = await show_more_button.is_visible()
                            if is_visible:
                                click_count += 1
                                logger.debug(f"Clicking SHOW MORE (click #{click_count})...")
                                
                                # Scroll to button and click
                                await show_more_button.scroll_into_view_if_needed()
                                await show_more_button.click()
                                
                                # Wait for new content to load (OPTIMIZED: 300ms is enough)
                                await page.wait_for_timeout(300)
                            else:
                                logger.debug(f"SHOW MORE button not visible. Finished loading after {click_count} clicks.")
                                break
                        else:
                            logger.debug(f"SHOW MORE button not found. Finished loading after {click_count} clicks.")
                            break
                    except Exception as e:
                        logger.debug(f"No more SHOW MORE button available after {click_count} clicks. Error: {e}")
                        break
                
                # STEP 2: Now extract ALL machines from the fully loaded page
                logger.debug("All items loaded! Now extracting all machines...")
                
                # Get the HTML content (this avoids Playwright memory issues with stale elements)
                html_content = await page.content()
                logger.debug(f"Got page HTML, length: {len(html_content)} characters")
                
                # Parse with BeautifulSoup for reliability
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Extract machines from the HTML
                machines = self._extract_machines_from_html(soup, search_title, search_url)
                logger.info(f"Found {len(machines)} total machines")
                
            except Exception as e:
                logger.error(f"Error scraping {search_url}: {e}", exc_info=True)
            finally:
                await browser.close()
        
        logger.debug(f"Scraping complete. Total machines collected: {len(machines)}")
        return machines
    
    async def _extract_machines(self, page, search_title, base_url):
        """Extract machine data from the current page state"""
        machines = []
        
        # Get all machine tiles
        tiles = await page.query_selector_all('a[ng-repeat*="results_machines"]')
        
        for tile in tiles:
            try:
                # Extract href
                href = await tile.get_attribute('href')
                machine_id = self._extract_id_from_url(href) if href else None
                
                if not machine_id:
                    continue
                
                # Extract title
                title_elem = await tile.query_selector('div.label')
                title = await title_elem.inner_text() if title_elem else 'Unknown'
                title = title.strip()
                
                # Extract price
                price_elem = await tile.query_selector('div.first-line span')
                price = await price_elem.inner_text() if price_elem else ''
                price = price.strip()
                
                # Extract hours
                hours_elems = await tile.query_selector_all('div.first-line span')
                hours = ''
                if len(hours_elems) > 1:
                    hours = await hours_elems[1].inner_text()
                    hours = hours.strip()
                
                # Extract location
                location_elem = await tile.query_selector('div.second-line')
                location = await location_elem.inner_text() if location_elem else ''
                location = location.strip()
                
                # Extract image URL
                image_elem = await tile.query_selector('div.primary-photo.optim')
                image_url = ''
                if image_elem:
                    style = await image_elem.get_attribute('style')
                    if style:
                        image_url = self._extract_image_url(style)
                
                # Build full URL
                full_url = urljoin(base_url, href) if href else ''
                
                machine = {
                    'id': machine_id,
                    'search_title': search_title,
                    'title': title,
                    'price': price,
                    'location': location,
                    'hours': hours,
                    'image_url': image_url,
                    'link': full_url,
                }
                
                machines.append(machine)
                
            except Exception as e:
                logger.error(f"Error extracting machine data: {e}")
                continue
        
        return machines
    
    def _extract_machines_from_html(self, soup, search_title, base_url):
        """Extract machine data from BeautifulSoup HTML (avoids Playwright memory issues)"""
        machines = []
        
        # Find all machine tiles using BeautifulSoup
        tiles = soup.select('a[ng-repeat*="results_machines"]')
        logger.info(f"Found {len(tiles)} machine tiles in HTML")
        
        for tile in tiles:
            try:
                # Extract href
                href = tile.get('href', '')
                machine_id = self._extract_id_from_url(href) if href else None
                
                if not machine_id:
                    continue
                
                # Extract title
                title_elem = tile.select_one('div.label')
                title = title_elem.get_text(strip=True) if title_elem else 'Unknown'
                
                # Extract price
                price_elem = tile.select_one('div.first-line span')
                price = price_elem.get_text(strip=True) if price_elem else ''
                
                # Extract hours
                hours_elems = tile.select('div.first-line span')
                hours = ''
                if len(hours_elems) > 1:
                    hours = hours_elems[1].get_text(strip=True)
                
                # Extract location
                location_elem = tile.select_one('div.second-line')
                location = location_elem.get_text(strip=True) if location_elem else ''
                
                # Extract image URL
                image_elem = tile.select_one('div.primary-photo.optim')
                image_url =''
                if image_elem and image_elem.get('style'):
                    image_url = self._extract_image_url(image_elem.get('style'))
                
                # Build full URL
                full_url = urljoin(base_url, href) if href else ''
                
                machine = {
                    'id': machine_id,
                    'search_title': search_title,
                    'title': title,
                    'price': price,
                    'location': location,
                    'hours': hours,
                    'image_url': image_url,
                    'link': full_url,
                }
                
                machines.append(machine)
                
            except Exception as e:
                logger.error(f"Error extracting machine data: {e}")
                continue
        
        return machines
    
    def _extract_id_from_url(self, url):
        """Extract machine ID from URL"""
        match = re.search(r'-(\d+)$', url)
        if match:
            return match.group(1)
        return None
    
    def _extract_image_url(self, style_attr):
        """Extract image URL from CSS background style"""
        match = re.search(r'url\((https?://[^)]+)\)', style_attr)
        if match:
            return match.group(1)
        return ''
    
    async def run_continuous(self):
        """Run scraping continuously at configured intervals"""
        interval_minutes = self.config.get('scrape_interval_minutes', 30)
        
        logger.info(f"Starting continuous monitoring (interval: {interval_minutes} minutes)")
        
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Error during scraping cycle: {e}", exc_info=True)
            
            logger.info(f"Waiting {interval_minutes} minutes until next cycle...")
            await asyncio.sleep(interval_minutes * 60)


async def main():
    import sys
    
    # Check if machine ID is provided via command line (e.g., --machine 1)
    machine_id = None
    run_mode = 'continuous'  # default mode
    
    # Parse command line arguments
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == '--machine' and i + 1 < len(sys.argv):
            machine_id = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--once':
            run_mode = 'once'
            i += 1
        else:
            i += 1
    
    # If no machine ID provided via command line, ask user
    if machine_id is None:
        print("\n" + "="*50)
        print("🖥️  MACHINEFINDER SCRAPER - MACHINE SELECTION")
        print("="*50)
        print("\nSelect which machine this is:")
        print("  [1] Machine 1 - 11 URLs (Tandem Rollers → Articulated Dump Trucks)")
        print("  [2] Machine 2 - 2 URLs (Compact Excavators + Compact Track Loaders)")
        print("  [ALL] ALL Machines - 13 URLs (Machine 1 + Machine 2 combined)")
        print("\nEnter machine number (1, 2, or ALL): ", end="")
        
        while True:
            try:
                choice = input().strip().upper()
                if choice in ['1', '2', 'ALL']:
                    machine_id = choice
                    break
                else:
                    print("Invalid choice! Please enter 1, 2, or ALL: ", end="")
            except (EOFError, KeyboardInterrupt):
                print("\n\n❌ Cancelled by user.")
                return
    
    print(f"\n✅ Selected Machine #{machine_id}")
    print("="*50 + "\n")
    
    # Initialize monitor with machine ID
    monitor = MachinefinderMonitor(machine_id=machine_id)
    
    if run_mode == 'once':
        # Run once and exit
        await monitor.run_once()
    else:
        # Run continuously
        await monitor.run_continuous()


if __name__ == '__main__':
    asyncio.run(main())
