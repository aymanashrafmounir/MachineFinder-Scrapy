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
    maxBytes=2048,  # 2KB max
    backupCount=1,   # Keep only 1 backup file
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)


class MachinefinderMonitor:
    def __init__(self, config_path='config.json'):
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Initialize components
        self.db = MachinefinderDB(self.config['database']['path'])
        self.notifier = TelegramNotifier(
            self.config['telegram']['bot_token'],
            self.config['telegram']['chat_id']
        )
    
    async def run_once(self):
        """Run a single scraping cycle for all configured URLs"""
        logger.info("Starting scraping cycle...")
        
        # Test Telegram connection
        telegram_ok = await self.notifier.test_connection()
        if not telegram_ok:
            logger.warning("Telegram connection failed, but continuing with scraping...")
        
        # Get delay between URLs from config (default 30 seconds)
        delay_between_urls = self.config.get('delay_between_urls_seconds', 30)
        
        for index, search_config in enumerate(self.config['search_urls']):
            search_title = search_config['title']
            search_url = search_config['url']
            max_price = search_config.get('max_price')  # Get max_price (or None)
            
            logger.info(f"Scraping: {search_title} - {search_url}")
            
            # Run scraper for this URL
            machines = await self._scrape_url(search_url, search_title, max_price)
            
            # Process scraped machines
            new_machines = []
            
            for machine in machines:
                is_new = self.db.add_machine(machine)
                if is_new:
                    new_machines.append(machine)
                    logger.info(f"New machine found: {machine['title']}")
            
            # Send notifications for new machines
            if new_machines:
                logger.info(f"Found {len(new_machines)} new machine(s) for {search_title}")
                
                # TEST MODE: Only send 1 notification for testing
                TEST_MODE = False
                if TEST_MODE:
                    logger.info("TEST MODE: Sending only 1 notification")
                    await self.notifier.send_new_items_notification(search_title, new_machines[:1])
                else:
                    await self.notifier.send_new_items_notification(search_title, new_machines)
            else:
                logger.info(f"No new machines found for {search_title}")
            
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
            if index < len(self.config['search_urls']) - 1:
                logger.info(f"Waiting {delay_between_urls} seconds before next URL...")
                await asyncio.sleep(delay_between_urls)
        
        logger.info("Scraping cycle completed!")
    
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
                # Navigate to the page
                logger.info(f"Loading page: {search_url}")
                response = await page.goto(search_url, wait_until='networkidle', timeout=60000)
                logger.debug(f"Page loaded with status: {response.status}")
                
                # Wait for Angular to bootstrap and initial content to load
                logger.debug("Waiting for page JavaScript to execute...")
                await page.wait_for_timeout(10000)  # Give Angular 10 seconds to bootstrap
                
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
                            await page.wait_for_timeout(2000)  # Wait for panel to expand
                            
                            # Step 2: Enter max price in the input field
                            logger.debug(f"Entering max price: {max_price}")
                            price_input = await page.query_selector('input[ng-model="filters.price_max"]')
                            
                            if price_input:
                                await price_input.click()
                                await price_input.fill(str(max_price))
                                await page.wait_for_timeout(1000)
                                
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
                                        
                                        # Wait for filtered results to load
                                        logger.debug("Waiting for filtered results to load...")
                                        await page.wait_for_timeout(5000)
                                        
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
                                
                                # Wait for new content to load
                                await page.wait_for_timeout(2000)
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
    
    monitor = MachinefinderMonitor()
    
    if len(sys.argv) > 1 and sys.argv[1] == '--once':
        # Run once and exit
        await monitor.run_once()
    else:
        # Run continuously
        await monitor.run_continuous()


if __name__ == '__main__':
    asyncio.run(main())
