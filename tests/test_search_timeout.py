import os
import subprocess
import sys
import unittest


class TestSearchRequestTimeout(unittest.TestCase):
    def test_request_finishes_even_if_xhr_never_emits_timeout(self):
        code = r'''
from playwright.sync_api import sync_playwright
from dyknow.browser import _XHR_JS
with sync_playwright() as p:
    b=p.chromium.launch(headless=True)
    page=b.new_page()
    page.goto('about:blank')
    page.evaluate("""() => { window.XMLHttpRequest = class {
      open() {} setRequestHeader() {} send() {} abort() {} }; }""")
    script=_XHR_JS.replace('20000', '100')
    result=page.evaluate(script, {'path':'/test','params':{},'method':'GET','body':None})
    assert result['http'] == -1, result
    b.close()
'''
        try:
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=12, env=os.environ.copy())
        except subprocess.TimeoutExpired:
            self.fail('请求没有在设定时间内结束')
        self.assertEqual(result.returncode, 0, result.stderr)
