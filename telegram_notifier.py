import asyncio
import requests
from io import BytesIO
from telegram import Bot
from telegram.error import TelegramError
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot = Bot(token=bot_token)
    
    async def send_new_items_notification(self, search_title: str, machines: List[Dict]):
        """Send notification for new machines found"""
        if not machines:
            return
        
        try:
            for machine in machines:
                await self._send_machine_notification(search_title, machine)
                # Small delay between messages to avoid rate limiting
                await asyncio.sleep(1)
        except TelegramError as e:
            logger.error(f"Error sending Telegram notification: {e}")
    
    async def _send_machine_notification(self, search_title: str, machine: Dict):
        """Send notification for a single machine with image"""
        # Format message
        message = self._format_message(search_title, machine)
        
        # Try to send with image
        image_url = machine.get('image_url', '')
        
        if image_url:
            try:
                # Download image
                image_data = self._download_image(image_url)
                
                if image_data:
                    # Send photo with caption
                    await self.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=image_data,
                        caption=message,
                        parse_mode='HTML'
                    )
                    logger.info(f"Sent notification with image for: {machine['title']}")
                    return
            except Exception as e:
                logger.warning(f"Failed to send image, sending text only: {e}")
        
        # Fallback: send text only
        await self.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode='HTML'
        )
        logger.info(f"Sent text notification for: {machine['title']}")
    
    def _format_message(self, search_title: str, machine: Dict) -> str:
        """Format the notification message"""
        message = f"🆕 <b>New item(s) found on {search_title}:</b>\n\n"
        message += f"<b>Title:</b> {machine['title']}\n"
        
        if machine.get('price'):
            message += f"<b>Price:</b> {machine['price']}\n"
        
        if machine.get('location'):
            message += f"<b>Location:</b> {machine['location']}\n"
        
        if machine.get('hours'):
            message += f"<b>Hours:</b> {machine['hours']}\n"
        
        message += f"<b>Link:</b> {machine['link']}"
        
        return message
    
    def _download_image(self, image_url: str) -> BytesIO:
        """Download image from URL"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            response = requests.get(image_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            return BytesIO(response.content)
        except Exception as e:
            logger.error(f"Error downloading image from {image_url}: {e}")
            return None
    
    async def send_alert(self, message: str):
        """Send warning/alert message to Telegram"""
        try:
            alert_message = f"⚠️ <b>ALERT</b>\n\n{message}"
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=alert_message,
                parse_mode='HTML'
            )
            logger.warning(f"Alert sent: {message}")
        except TelegramError as e:
            logger.error(f"Failed to send alert: {e}")
    
    async def test_connection(self):
        """Test Telegram bot connection"""
        try:
            me = await self.bot.get_me()
            logger.info(f"Telegram bot connected: @{me.username}")
            return True
        except TelegramError as e:
            logger.error(f"Failed to connect to Telegram: {e}")
            return False

