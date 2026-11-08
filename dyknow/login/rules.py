"""抖音登录页的识别规则：文案关键词和找二维码的脚本。

抖音改版导致登录卡住时，只改这一个文件：
  - 页面上出现了新的提示文案 → 加到对应的关键词列表里
  - 二维码找不到 → 改 FIND_QR_JS
其他登录代码不认识任何抖音文案。
"""

REFRESH = ["刷新", "点击刷新", "刷新二维码"]
REFRESH_EXPIRED = REFRESH + ["二维码已失效", "二维码已过期"]
SWITCH_TO_QR = ["扫码登录", "二维码登录"]
SWITCH_TO_SMS = ["验证码登录", "手机号登录", "短信登录", "手机验证码登录"]
AGREE = ["同意并继续", "同意并登录", "同意并发送"]

FIND_QR_JS = r"""() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 80 && r.height > 80 && s.visibility !== 'hidden' && s.display !== 'none'
      && Number(s.opacity) > 0.1 && r.bottom > 0 && r.right > 0; };
  let best = null, bestScore = -1;
  for (const el of document.querySelectorAll('img, canvas, svg')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect(), ratio = r.width / r.height;
    if (ratio < 0.85 || ratio > 1.18 || r.width > 520) continue;
    const src = el.tagName === 'IMG' ? (el.currentSrc || el.src || '') : '';
    const cls = String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || '')
      + ' ' + String((el.parentElement && el.parentElement.className) || '');
    let score = 0;
    if (/qr/i.test(src) || /qr/i.test(cls)) score += 3;
    if (src.startsWith('data:image') || el.tagName !== 'IMG') score += 1;
    let p = el;
    for (let i = 0; p && i < 7; i++, p = p.parentElement) {
      if ((p.innerText || '').includes('扫码')) { score += 2; break; }
    }
    if (score > bestScore) { best = el; bestScore = score; }
  }
  if (!best || bestScore < 3) return null;
  const r = best.getBoundingClientRect();
  let sig = '';
  if (best.tagName === 'IMG') sig = best.currentSrc || best.src || '';
  else if (best.tagName === 'CANVAS') { try { sig = best.toDataURL(); } catch (e) { sig = 'canvas'; } }
  else sig = best.innerHTML;
  return {x: r.x, y: r.y, w: r.width, h: r.height, sig};
}"""

TEXT_JS = "() => (document.body && document.body.innerText || '').slice(0, 6000)"

CAPTCHA = ("拖动滑块", "请完成下列验证", "验证码中间页", "按顺序点击", "旋转图片")
EXPIRED = ("二维码已失效", "二维码已过期", "二维码失效", "已失效", "已过期")
SCANNED = ("扫码成功", "已扫码", "请在手机上确认", "在手机上确认", "确认登录")
NEED_CODE = ("身份验证", "安全验证", "短信验证", "输入验证码", "请输入验证码", "接收短信")
SEND_CODE = ["获取验证码", "发送验证码", "获取短信验证码"]
SMS_OPTION = ["接收短信验证码", "短信验证码", "短信验证", "发送短信验证码", "手机短信验证"]
WRONG_CODE = ("验证码错误", "验证码不正确", "验证码有误", "验证码已过期", "验证码已失效")
SUBMIT = ["验证", "确定", "确认", "提交", "下一步", "登录"]

# 找手机号输入框并打上标记（data-dyknow-phone）
PHONE_INPUT_JS = r"""() => {
      const ok = el => { const r = el.getBoundingClientRect(); return r.width > 40 && r.height > 10
        && getComputedStyle(el).visibility !== 'hidden'; };
      const el = [...document.querySelectorAll('input')].filter(ok).find(el =>
        /手机号|手机号码|phone|mobile/i.test((el.placeholder || '') + (el.name || '') + (el.getAttribute('aria-label') || ''))
        || el.type === 'tel');
      if (!el) return false;
      el.setAttribute('data-dyknow-phone', '1');
      return true;
    }"""

# 勾上「同意用户协议」的选框
AGREE_CHECKBOX_JS = r"""() => {
      for (const el of document.querySelectorAll('input[type=checkbox]')) {
        const t = (el.closest('label, div') || el).innerText || '';
        if (/协议|同意/.test(t) && !el.checked) { el.click(); }
      }
    }"""

# 找验证码输入框并打上标记（data-dyknow-code）
CODE_INPUT_JS = r"""(code) => {
      const ok = el => { const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 10
        && getComputedStyle(el).visibility !== 'hidden'; };
      const inputs = [...document.querySelectorAll('input')].filter(ok).filter(el =>
        /验证码|code/i.test((el.placeholder || '') + (el.name || '') + (el.getAttribute('aria-label') || ''))
        || ['tel', 'number'].includes(el.type) && Number(el.maxLength) > 0 && Number(el.maxLength) <= 8);
      const el = inputs[inputs.length - 1];
      if (!el) return false;
      el.setAttribute('data-dyknow-code', '1');
      return true;
    }"""

# 点验证码输入框附近的提交按钮（页面顶部也有个「登录」，不能点错）；参数是按钮文案列表 SUBMIT
SUBMIT_NEAR_CODE_JS = r"""(names) => {
          let p = document.querySelector("[data-dyknow-code='1']");
          for (let i = 0; p && i < 8; i++, p = p.parentElement) {
            const btn = [...p.querySelectorAll('button, [role=button], div, span')].find(el =>
              names.includes((el.innerText || '').trim()) && el.getBoundingClientRect().width > 20);
            if (btn) { btn.click(); return true; }
          }
          return false;
        }"""
