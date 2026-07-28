#!/usr/bin/env python3
"""
Telegram Bot لطلاب جامعة النجاح - الإصدار المطور
يسجل المشتركين ويراقب أخبار الجامعة كل 5 دقائق من الموقع وفيسبوك
ويرسل إشعارات عند وجود عطلة أو تعليق دوام

الميزات الجديدة:
- مراقبة موقع الجامعة الرسمي (www.najah.edu)
- مراقبة صفحة الجامعة على فيسبوك (www.facebook.com/ANajahUni)
- اشتراك اختياري في إشعارات فيسبوك
- أزرار تفاعلية للتحكم في الإعدادات
- تحليل البيانات من مصادر متعددة
"""

import sqlite3
import asyncio
import logging
from datetime import datetime
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue,
    CallbackQueryHandler
)
import aiohttp

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ثوابت
DB_PATH = 'najah_bot.db'
CHECK_INTERVAL = 300  # 5 دقائق بالثواني
NAJAH_WEBSITE_URL = 'https://www.najah.edu'
NAJAH_NEWS_URL = 'https://www.najah.edu/ar/news'
NAJAH_FACEBOOK_URL = 'https://www.facebook.com/ANajahUni'

# كلمات مفتاحية للدلالة على عطلة أو تعليق دوام
HOLIDAY_KEYWORDS = [
    'عطلة', 'تعطيل', 'تعليق', 'دوام', 'إغلاق', 
    'holiday', 'vacation', 'suspended', 'closed'
]


class Database:
    """إدارة قاعدة البيانات SQLite"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """إنشاء الجداول المطلوبة"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # جدول المشتركين
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                facebook_subscription BOOLEAN DEFAULT 0
            )
        ''')
        
        # جدول الإعلانات المرسلة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT,
                url TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("تم تهيئة قاعدة البيانات بنجاح")
    
    def add_subscriber(self, chat_id: int, username: str, first_name: str, last_name: str = None) -> bool:
        """إضافة مشترك جديد"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO subscribers 
                (chat_id, username, first_name, last_name, subscribed_at, is_active, facebook_subscription)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 1, 0)
            ''', (chat_id, username, first_name, last_name))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"خطأ في إضافة المشترك: {e}")
            return False
    
    def toggle_facebook_subscription(self, chat_id: int) -> bool:
        """تبديل اشتراك فيسبوك"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE subscribers 
                SET facebook_subscription = CASE 
                    WHEN facebook_subscription = 0 THEN 1 
                    ELSE 0 
                END
                WHERE chat_id = ?
            ''', (chat_id,))
            
            conn.commit()
            
            # جلب الحالة الجديدة
            cursor.execute('SELECT facebook_subscription FROM subscribers WHERE chat_id = ?', (chat_id,))
            result = cursor.fetchone()
            new_status = result[0] if result else 0
            
            conn.close()
            return new_status == 1
        except Exception as e:
            logger.error(f"خطأ في تبديل اشتراك فيسبوك: {e}")
            return False
    
    def get_facebook_subscribers(self) -> list:
        """الحصول على المشتركين في خدمة فيسبوك"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT chat_id FROM subscribers WHERE is_active = 1 AND facebook_subscription = 1')
            subscribers = [row[0] for row in cursor.fetchall()]
            
            conn.close()
            return subscribers
        except Exception as e:
            logger.error(f"خطأ في جلب مشتركي فيسبوك: {e}")
            return []
    
    def get_user_facebook_status(self, chat_id: int) -> bool:
        """الحصول على حالة اشتراك فيسبوك للمستخدم"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT facebook_subscription FROM subscribers WHERE chat_id = ?', (chat_id,))
            result = cursor.fetchone()
            
            conn.close()
            return result[0] == 1 if result else False
        except Exception as e:
            logger.error(f"خطأ في جلب حالة فيسبوك: {e}")
            return False
    
    def remove_subscriber(self, chat_id: int) -> bool:
        """إزالة مشترك"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('UPDATE subscribers SET is_active = 0 WHERE chat_id = ?', (chat_id,))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"خطأ في إزالة المشترك: {e}")
            return False
    
    def get_active_subscribers(self) -> list:
        """الحصول على قائمة المشتركين النشطين"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT chat_id FROM subscribers WHERE is_active = 1')
            subscribers = [row[0] for row in cursor.fetchall()]
            
            conn.close()
            return subscribers
        except Exception as e:
            logger.error(f"خطأ في جلب المشتركين: {e}")
            return []
    
    def is_announcement_sent(self, title: str) -> bool:
        """التحقق مما إذا كان الإعلان قد أُرسل من قبل"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT COUNT(*) FROM sent_announcements WHERE title = ?', (title,))
            count = cursor.fetchone()[0]
            
            conn.close()
            return count > 0
        except Exception as e:
            logger.error(f"خطأ في التحقق من الإعلان: {e}")
            return False
    
    def mark_announcement_sent(self, title: str, content: str, url: str):
        """تسجيل إعلان كمرسل"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO sent_announcements (title, content, url)
                VALUES (?, ?, ?)
            ''', (title, content, url))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"خطأ في تسجيل الإعلان: {e}")


class NajahNewsMonitor:
    """مراقبة أخبار جامعة النجاح من الموقع وفيسبوك"""
    
    def __init__(self):
        self.session = None
    
    async def start_session(self):
        """بدء جلسة HTTP"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """إغلاق جلسة HTTP"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def fetch_news_from_website(self) -> list:
        """جلب الأخبار من موقع الجامعة"""
        news_items = []
        
        try:
            async with self.session.get(NAJAH_NEWS_URL, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    news_items = self.parse_website_news(html)
        except Exception as e:
            logger.error(f"خطأ في جلب الأخبار من الموقع: {e}")
        
        return news_items
    
    async def fetch_news_from_facebook(self) -> list:
        """جلب الأخبار من صفحة فيسبوك"""
        news_items = []
        
        try:
            # ملاحظة: فيسبوك يتطلب authentication للوصول للبيانات
            # هنا نستخدم طريقة مبسطة - في الإنتاج استخدم Facebook Graph API
            async with self.session.get(NAJAH_FACEBOOK_URL, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    news_items = self.parse_facebook_posts(html)
        except Exception as e:
            logger.error(f"خطأ في جلب الأخبار من فيسبوك: {e}")
        
        return news_items
    
    def parse_website_news(self, html: str) -> list:
        """تحليل HTML لموقع الجامعة"""
        news_items = []
        
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            
            # البحث عن عناصر الأخبار (تعديل حسب هيكل موقع النجاح الفعلي)
            news_elements = soup.find_all('article', limit=10)
            
            for element in news_elements:
                title_elem = element.find('h2') or element.find('h3')
                link_elem = element.find('a')
                
                if title_elem and link_elem:
                    title = title_elem.get_text(strip=True)
                    link = link_elem.get('href', '')
                    if not link.startswith('http'):
                        link = f'https://www.najah.edu{link}'
                    
                    news_items.append({
                        'title': title,
                        'content': element.get_text(strip=True)[:200],
                        'url': link,
                        'source': 'website'
                    })
        except ImportError:
            logger.warning("BeautifulSoup غير مثبت")
        except Exception as e:
            logger.error(f"خطأ في تحليل أخبار الموقع: {e}")
        
        return news_items
    
    def parse_facebook_posts(self, html: str) -> list:
        """تحليل منشورات فيسبوك"""
        news_items = []
        
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            
            # ملاحظة: فيسبوك يستخدم تحميل ديناميكي وقد يحتاج Selenium
            # هذا مثال مبسط - في الإنتاج استخدم Facebook Graph API
            posts = soup.find_all('div', {'role': 'article'}, limit=5)
            
            for post in posts:
                content_elem = post.find('div', class_='x1lliihq')
                if content_elem:
                    content = content_elem.get_text(strip=True)[:300]
                    news_items.append({
                        'title': 'منشور من صفحة الجامعة',
                        'content': content,
                        'url': NAJAH_FACEBOOK_URL,
                        'source': 'facebook'
                    })
        except ImportError:
            logger.warning("BeautifulSoup غير مثبت")
        except Exception as e:
            logger.error(f"خطأ في تحليل منشورات فيسبوك: {e}")
        
        return news_items
    
    async def fetch_all_news(self) -> list:
        """جلب الأخبار من جميع المصادر"""
        website_news = await self.fetch_news_from_website()
        facebook_news = await self.fetch_news_from_facebook()
        
        all_news = website_news + facebook_news
        logger.info(f"تم جلب {len(all_news)} خبر من جميع المصادر")
        
        return all_news
    
    def is_holiday_announcement(self, title: str, content: str) -> bool:
        """التحقق مما إذا كان الإعلان عن عطلة أو تعليق دوام"""
        text = f"{title} {content}".lower()
        return any(keyword.lower() in text for keyword in HOLIDAY_KEYWORDS)


class NajahBot:
    """بوت تليجرام الرئيسي"""
    
    def __init__(self, token: str):
        self.token = token
        self.db = Database(DB_PATH)
        self.monitor = NajahNewsMonitor()
        self.application = None
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start command"""
        user = update.effective_user
        chat_id = update.effective_chat.id
        
        self.db.add_subscriber(
            chat_id=chat_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        # إنشاء أزرار التفاعل
        keyboard = [
            [InlineKeyboardButton("🔔 تفعيل إشعارات فيسبوك", callback_data='toggle_facebook')],
            [InlineKeyboardButton("📊 عرض الحالة", callback_data='show_status')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_message = f"""
🎓 مرحبًا {user.first_name}!

أهلاً بك في بوت جامعة النجاح للإشعارات.

سأقوم بإعلامك فورًا عند وجود:
• عطلة رسمية
• تعليق دوام
• أي إعلان مهم من الجامعة

📢 ميزة جديدة: يمكنك الآن الاشتراك في إشعارات صفحة الجامعة على فيسبوك!

استخدم الأوامر التالية:
/help - لعرض المساعدة
/unsubscribe - لإلغاء الاشتراك
/status - لعرض حالة اشتراكك

أو اضغط على الأزرار أدناه للتفاعل مع البوت.

تم تسجيلك بنجاح! ✅
        """
        
        await update.message.reply_text(welcome_message, reply_markup=reply_markup)
        logger.info(f"مشترك جديد: {user.first_name} ({chat_id})")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /help command"""
        keyboard = [
            [InlineKeyboardButton("⚙️ إعدادات فيسبوك", callback_data='toggle_facebook')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        help_message = """
📚 مساعدة بوت جامعة النجاح

الأوامر المتاحة:
/start - البدء واستخدام البوت
/help - عرض هذه الرسالة
/unsubscribe - إلغاء الاشتراك
/status - عرض حالة الاشتراك

🆕 ميزة فيسبوك:
يمكنك الآن الاشتراك في إشعارات صفحة الجامعة على فيسبوك!
اضغط على زر "إعدادات فيسبوك" لتفعيل/تعطيل هذه الميزة.

البوت يراقب أخبار جامعة النجاح كل 5 دقائق من:
• موقع الجامعة الرسمي (www.najah.edu)
• صفحة الجامعة على فيسبوك

ويرسلك إشعارات فورية عند وجود عطلة أو تعليق دوام.

للدعم والتواصل: @najah_support
        """
        await update.message.reply_text(help_message, reply_markup=reply_markup)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة ضغطات الأزرار"""
        query = update.callback_query
        await query.answer()
        
        chat_id = query.message.chat_id
        data = query.data
        
        if data == 'toggle_facebook':
            is_enabled = self.db.toggle_facebook_subscription(chat_id)
            
            if is_enabled:
                message = """
✅ تم تفعيل إشعارات فيسبوك!

ستصلك الآن إشعارات من:
• موقع جامعة النجاح الرسمي
• صفحة الجامعة على فيسبوك

📱 صفحة الجامعة: https://www.facebook.com/ANajahUni

ملاحظة: لإلغاء التفعيل، اضغط على الزر مرة أخرى.
                """
            else:
                message = """
❌ تم تعطيل إشعارات فيسبوك.

لن تصلك إلا إشعارات من موقع الجامعة الرسمي فقط.

لتفعيل الإشعارات مرة أخرى، اضغط على الزر مرة أخرى.
                """
            
            keyboard = [[InlineKeyboardButton("🔄 تغيير الإعداد", callback_data='toggle_facebook')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup)
        
        elif data == 'show_status':
            subscribers = self.db.get_active_subscribers()
            is_subscribed = chat_id in subscribers
            
            fb_status = self.db.get_user_facebook_status(chat_id)
            fb_text = "✅ مفعل" if fb_status else "❌ معطل"
            
            total_fb = len(self.db.get_facebook_subscribers())
            
            status_message = f"""
📊 حالة اشتراكك:

الاشتراك الرئيسي: {'✅ نشط' if is_subscribed else '❌ غير نشط'}
إشعارات فيسبوك: {fb_text}

📈 إحصائيات عامة:
• إجمالي المشتركين: {len(subscribers)}
• مشتركو فيسبوك: {total_fb}
            """
            
            keyboard = [[InlineKeyboardButton("🔄 تحديث", callback_data='show_status')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(status_message, reply_markup=reply_markup)
    
    async def unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /unsubscribe command"""
        chat_id = update.effective_chat.id
        self.db.remove_subscriber(chat_id)
        
        await update.message.reply_text(
            "❌ تم إلغاء اشتراكك بنجاح.\n"
            "يمكنك العودة للاشتراك في أي وقت باستخدام /start"
        )
        logger.info(f"تم إلغاء اشتراك: {chat_id}")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /status command"""
        chat_id = update.effective_chat.id
        
        # تحقق من حالة المشترك
        subscribers = self.db.get_active_subscribers()
        is_subscribed = chat_id in subscribers
        
        if is_subscribed:
            status_message = "✅ أنت مشترك حاليًا في خدمة الإشعارات."
        else:
            status_message = "❌ لست مشتركًا حاليًا.\nاستخدم /start للاشتراك."
        
        total_subscribers = len(subscribers)
        status_message += f"\n\n📊 إجمالي المشتركين: {total_subscribers}"
        
        await update.message.reply_text(status_message)
    
    async def check_news_periodically(self, context: ContextTypes.DEFAULT_TYPE):
        """فحص الأخبار بشكل دوري"""
        logger.info("جاري فحص أخبار جامعة النجاح...")
        
        try:
            # جلب الأخبار من جميع المصادر
            news_items = await self.monitor.fetch_all_news()
            
            for news in news_items:
                if self.monitor.is_holiday_announcement(news['title'], news.get('content', '')):
                    if not self.db.is_announcement_sent(news['title']):
                        await self.send_announcement(news)
                        self.db.mark_announcement_sent(
                            news['title'],
                            news.get('content', ''),
                            news['url']
                        )
                        logger.info(f"تم إرسال إعلان: {news['title']}")
        except Exception as e:
            logger.error(f"خطأ في فحص الأخبار: {e}")
    
    async def send_announcement(self, news: dict):
        """إرسال إعلان لجميع المشتركين"""
        subscribers = self.db.get_active_subscribers()
        facebook_subscribers = self.db.get_facebook_subscribers()
        
        # تحديد المصدر
        source_text = "🌐 الموقع الرسمي" if news.get('source') == 'website' else "📘 فيسبوك"
        
        message = f"""
🚨 إعلان عاجل من جامعة النجاح
{source_text}

📌 العنوان: {news['title']}

{news.get('content', '')}

🔗 للمزيد: {news['url']}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}
        """
        
        bot = Bot(token=self.token)
        
        # إرسال للمشتركين العاديين (دائمًا)
        for chat_id in subscribers:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode='HTML'
                )
                await asyncio.sleep(0.1)  # تجنب rate limiting
            except Exception as e:
                logger.error(f"خطأ في إرسال الإعلان إلى {chat_id}: {e}")
        
        # إذا كان الإعلان من فيسبوك، نرسل فقط لمشتركي فيسبوك (إضافة خاصة)
        if news.get('source') == 'facebook':
            fb_message = f"""
📘 منشور جديد من صفحة الجامعة على فيسبوك

{news.get('content', '')}

🔗 الرابط: {news['url']}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}
            """
            
            for chat_id in facebook_subscribers:
                # تجنب الإرسال المكرر لمن وصلته الرسالة العادية
                if chat_id not in subscribers:
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=fb_message,
                            parse_mode='HTML'
                        )
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        logger.error(f"خطأ في إرسال إعلان فيسبوك إلى {chat_id}: {e}")
    
    async def post_init(self, application):
        """تهيئة بعد بدء التطبيق"""
        await self.monitor.start_session()
        logger.info("تم بدء مراقبة الأخبار")
    
    async def post_shutdown(self, application):
        """تنظيف عند الإيقاف"""
        await self.monitor.close_session()
        logger.info("تم إيقاف مراقبة الأخبار")
    
    def run(self):
        """تشغيل البوت"""
        # إنشاء التطبيق
        self.application = Application.builder().token(self.token).build()
        
        # إضافة معالجات الأوامر
        self.application.add_handler(CommandHandler('start', self.start_command))
        self.application.add_handler(CommandHandler('help', self.help_command))
        self.application.add_handler(CommandHandler('unsubscribe', self.unsubscribe_command))
        self.application.add_handler(CommandHandler('status', self.status_command))
        
        # إضافة معالج ضغطات الأزرار
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        
        # إضافة وظيفة الفحص الدوري
        self.application.job_queue.run_repeating(
            self.check_news_periodically,
            interval=CHECK_INTERVAL,
            first=10  # ابدأ بعد 10 ثواني
        )
        
        # إعداد دوال التهيئة والتنظيف
        self.application.post_init = self.post_init
        self.application.post_shutdown = self.post_shutdown
        
        logger.info("جاري تشغيل البوت...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    """الدالة الرئيسية"""
    import os
    
    # توكن البوت المقدم
    token = os.getenv('TELEGRAM_BOT_TOKEN', '8937034906:AAGez184aYpJkuK1VaU_CGO7u6PcPbaCG4A')
    
    if not token:
        print("❌ خطأ: يرجى设置 متغير البيئة TELEGRAM_BOT_TOKEN")
        print("مثال: export TELEGRAM_BOT_TOKEN='your_bot_token_here'")
        return
    
    bot = NajahBot(token)
    bot.run()


if __name__ == '__main__':
    main()
