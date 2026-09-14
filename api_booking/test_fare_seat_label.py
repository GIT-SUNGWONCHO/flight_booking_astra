import unittest
from playwright.sync_api import sync_playwright
import site_drive

class SeatLabelTests(unittest.TestCase):
    def test_visible_positive_seats_only(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                for label,expected in [('항공편명 KE901 일반석 35,000 마일',True),
                        ('항공편명 KE901 일반석 35,000 마일 8 석',True),
                        ('항공편명 KE901 일반석 35,000 마일 0 석',False),
                        ('항공편명 KE901 프레스티지 62,500 마일 1 석',False),
                        ('항공편명 KE901 일반석 매진',False)]:
                    with self.subTest(label=label):
                        page.set_content('<button>'+label+'</button>')
                        self.assertEqual(bool(site_drive.select_fare(page,wait=0)),expected)
            finally:browser.close()

if __name__=='__main__':unittest.main()
