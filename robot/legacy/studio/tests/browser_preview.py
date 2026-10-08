"""Optional rendered UI checks against the isolated preview; never connects to MAX.

Requires Selenium plus Firefox/geckodriver. Set FIREFOX_BINARY and GECKODRIVER
when they are not on the usual paths. Not run by unittest discovery.
"""
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import os
from pathlib import Path
import tempfile
import threading
import time
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from build_preview import build

work=Path(tempfile.mkdtemp(prefix='max-studio-qa-'))
build(work/'index.html')
server=ThreadingHTTPServer(('127.0.0.1',8091),partial(SimpleHTTPRequestHandler,directory=str(work)))
threading.Thread(target=server.serve_forever,daemon=True).start()
options=Options();options.add_argument('-headless')
if os.environ.get('FIREFOX_BINARY'):options.binary_location=os.environ['FIREFOX_BINARY']
service=Service(os.environ['GECKODRIVER']) if os.environ.get('GECKODRIVER') else Service()
driver=webdriver.Firefox(service=service,options=options)
wait=WebDriverWait(driver,10)
def click(css):driver.find_element(By.CSS_SELECTOR,css).click()
def view(name):click(f'[data-view="{name}"]')
def count():return int(driver.find_element(By.ID,'qaDriveCount').get_attribute('textContent'))
def logs():return driver.find_element(By.ID,'qaLog').get_attribute('textContent')
try:
 driver.set_window_size(1440,1100);driver.get('http://127.0.0.1:8091/')
 wait.until(lambda d:'Listo' in d.find_element(By.ID,'status').text)
 wait.until(lambda d:len(d.find_elements(By.CSS_SELECTOR,'.joint'))==6)
 # Every transformed arrow must stay in its own button, in both viewport sizes.
 def assert_arrow_bounds():
  outside=driver.execute_script("""
   return [...document.querySelectorAll('.dpad path,.dpad circle')].filter(shape=>{
     const b=shape.getBoundingClientRect(),s=shape.closest('svg').getBoundingClientRect();
     return b.left<s.left-1||b.right>s.right+1||b.top<s.top-1||b.bottom>s.bottom+1;
   }).map(shape=>shape.closest('button').getAttribute('aria-label'));
  """)
  assert not outside, 'Arrows outside their buttons: '+str(outside)
 assert_arrow_bounds()
 ActionChains(driver).key_down('w').key_down('a').pause(.25).key_up('a').key_up('w').perform();time.sleep(.1)
 assert '"linear":0.05,"angular":0.25' in logs()
 assert '"type":"stop"' in logs()
 ActionChains(driver).key_down('w').pause(.15).perform();view('brazos');before=count()
 ActionChains(driver).key_up('w').key_down('w').pause(.2).key_up('w').perform();assert count()==before
 click('.joint-controls button');wait.until(lambda d:'"action":"joint"' in logs())
 view('biblioteca');click('.favorite');click('[data-filter="favorites"]')
 assert len(driver.find_elements(By.CSS_SELECTOR,'.action-item'))==1
 click('.favorite');click('[data-filter="quick"]')
 driver.find_element(By.ID,'actionSearch').send_keys('militar')
 assert len(driver.find_elements(By.CSS_SELECTOR,'.action-item'))==2
 driver.find_element(By.ID,'actionSearch').clear()
 view('jetson');assert 'IA: apagado' in driver.find_element(By.ID,'jetsonVoiceNodes').text
 assert 'detectada' in driver.find_element(By.ID,'jetsonCamera').text
 view('cabina');driver.save_screenshot(str(work/'desktop.png'))
 driver.set_window_size(390,844)
 for name in ['cabina','brazos','biblioteca','entorno','jetson']:
  view(name)
  assert not driver.execute_script('return document.documentElement.scrollWidth>innerWidth'),name+' overflow'
 view('cabina');assert_arrow_bounds();driver.save_screenshot(str(work/'mobile.png'))
 print('PASS rendered desktop/mobile preview, navigation, keyboard, favorites, joints, search, Jetson.')
 print('Screenshots:',work)
finally:
 driver.quit();server.shutdown()
