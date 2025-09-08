import requests
import time
from bs4 import BeautifulSoup
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging
from urllib.parse import quote

class BookSyncAutomation:
    def __init__(self, goodreads_user_id, storygraph_email, storygraph_password):
        self.goodreads_user_id = goodreads_user_id
        self.storygraph_email = storygraph_email
        self.storygraph_password = storygraph_password
        self.driver = None

    def get_recently_read_goodreads(self):
        """Fetch recently read books from Goodreads RSS feed"""
        logging.info("Fetching Goodreads RSS feed...")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
        rss_url = f"https://www.goodreads.com/user/updates_rss/{self.goodreads_user_id}"
        response = requests.get(rss_url, headers=headers, timeout=30)
        if response.status_code != 200:
            raise Exception(f"Failed to access RSS feed, status code: {response.status_code}")

        soup = BeautifulSoup(response.text, 'lxml-xml')
        items = soup.find_all('item')
        logging.info(f"Found {len(items)} total items in RSS feed.")
        
        recent_books = []
        for item in items:
            try:
                desc_text = item.find('description').get_text(strip=True)
                if "read" in desc_text or "finished reading" in desc_text:
                    item_title = item.find('title').text.strip()
                    book_title = item_title.split(', ')[0]
                    if " (" in book_title:
                        book_title = book_title.split(" (")[0].strip()

                    pub_date = datetime.strptime(item.find('pubDate').text, '%a, %d %b %Y %H:%M:%S %z')
                    book = {'title': book_title, 'date_read': pub_date}
                    if book not in recent_books:
                        recent_books.append(book)
                        logging.info(f"Found book to sync: {book_title} (Read on: {pub_date.strftime('%Y-%m-%d')})")
            except Exception as e:
                logging.warning(f"Could not process RSS item: {e}")
        return recent_books

    def initialize_browser(self):
        """Initialize a local headless Chrome browser inside the container"""
        if not self.driver:
            logging.info("Initializing local headless Chrome browser...")
            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            
            # The executable_path is no longer needed if chromedriver is in the PATH
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.implicitly_wait(15)
            logging.info("Headless browser initialized.")

    def login_to_storygraph(self):
        """Login to StoryGraph with improved waits and verification"""
        self.initialize_browser()
        try:
            logging.info("Navigating to StoryGraph login page...")
            self.driver.get("https://app.thestorygraph.com/users/sign_in")
            
            if "/users/sign_in" not in self.driver.current_url:
                logging.info("Already logged in.")
                return

            email_field = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']")))
            email_field.send_keys(self.storygraph_email)
            self.driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(self.storygraph_password)
            self.driver.find_element(By.XPATH, "//button[contains(text(), 'Sign in')]").click()

            WebDriverWait(self.driver, 30).until(EC.url_contains("app.thestorygraph.com/"))
            if "/sign_in" in self.driver.current_url:
                 raise Exception("Login failed - still on sign-in page.")
            logging.info("Successfully logged into StoryGraph.")
        except Exception as e:
            self.driver.save_screenshot("logs/login_error.png")
            raise

    def check_book_exists(self, book):
        """Check if book already exists in StoryGraph reading journal"""
        self.driver.get("https://app.thestorygraph.com/journal")
        time.sleep(3)
        if book['title'].lower() in self.driver.page_source.lower():
            logging.info(f"Book '{book['title']}' already exists.")
            return True
        return False
    
    def js_click(self, element):
        self.driver.execute_script("arguments[0].click();", element)

    def update_book_status(self, book):
        if self.check_book_exists(book): return
        logging.info(f"Adding '{book['title']}' to StoryGraph...")
        self.driver.get(f"https://app.thestorygraph.com/browse?search_term={quote(book['title'])}")
        try:
            container = WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.book-pane-content-container")))
            self.js_click(container.find_element(By.CSS_SELECTOR, "button.expand-dropdown-button"))
            time.sleep(1)
            self.js_click(WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "div.read-status-dropdown-content form[action*='status=read'] button"))))
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.NAME, "read_instance[year]")))
            
            date = book['date_read']
            self.driver.execute_script(f"document.getElementById('read_instance_day').value = '{date.day}';")
            self.driver.execute_script(f"document.getElementsByName('read_instance[month]')[0].value = '{date.month}';")
            self.driver.execute_script(f"document.getElementsByName('read_instance[year]')[0].value = '{date.year}';")
            time.sleep(1)
            self.js_click(self.driver.find_element(By.CSS_SELECTOR, "input[type='submit'][value='Update']"))
            time.sleep(4)
            logging.info(f"✅ Successfully added '{book['title']}'")
        except Exception as e:
            self.driver.save_screenshot(f"logs/book_error_{book['title'].replace(' ', '_')}.png")
            raise Exception(f"Failed to process '{book['title']}': {e}")

    def sync_books(self):
        processed_titles = []
        try:
            recent_books = self.get_recently_read_goodreads()
            if not recent_books:
                logging.info("No new books to sync.")
                return []
            self.login_to_storygraph()
            for book in recent_books:
                try:
                    logging.info(f"\n--- Processing: '{book['title']}' ---")
                    self.update_book_status(book)
                    processed_titles.append(book['title'])
                    time.sleep(5)
                except Exception as e:
                    logging.error(f"Failed to process '{book['title']}': {e}. Continuing.")
            return processed_titles
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None
