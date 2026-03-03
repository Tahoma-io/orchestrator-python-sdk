from playwright.async_api import async_playwright
from tahoma import Tahoma

import asyncio

async def main():
    tahoma = Tahoma("http://127.0.0.1:9222")
    await tahoma.start()
    print(tahoma.context.info())
    
    page = await tahoma.new_page()
    await page.goto("https://www.google.com")
    await page.wait()
    await page.screenshot()
    print(tahoma.context.info())
    
    page2 = await tahoma.new_page()
    await page2.goto("https://www.youtube.com")
    await page2.wait()
    await page2.screenshot()
    print(tahoma.context.info())
    
    print(f"Title: {await page.title()}")
    print(f"Title: {await page2.title()}")
    await tahoma.close()

asyncio.run(main())
