() => {
  const verifyKeys = ["身份验证", "接收短信验证码", "发送短信验证", "请输入验证码", "我已发送", "为保障账号安全"];
  const loginKeys = ["扫码登录", "打开抖音App", "打开「抖音APP」", "登录后免费畅享"];
  const bodyText = ((document.body && document.body.innerText) || "").replace(/\s+/g, " ");
  const inVerify = verifyKeys.some((k) => bodyText.includes(k));
  const keys = inVerify ? verifyKeys : loginKeys.concat(verifyKeys);
  const scoreOf = (el) => {
    const r = el.getBoundingClientRect();
    const t = (el.innerText || "").replace(/\s+/g, " ");
    const hits = keys.filter((k) => t.includes(k)).length;
    if (!hits) return -1;
    if (r.width < 280 || r.height < 200 || r.width > 640 || r.height > 880) return -1;
    if (r.bottom < 8 || r.right < 8) return -1;
    if (inVerify && /扫码登录/.test(t) && !verifyKeys.some((k) => t.includes(k))) return -1;
    let score = hits * 80 - Math.abs(r.width - 400) - Math.abs(r.height - 520) * 0.35;
    if (/请输入验证码|我已发送|重新发送/.test(t)) score += 50;
    if (inVerify && t.includes("身份验证")) score += 40;
    if (/接收短信验证码|发送短信验证/.test(t)) score += 30;
    return score;
  };
  let best = null;
  let bestScore = -1;
  for (const el of document.querySelectorAll("div, section, article, [role=dialog], form")) {
    const score = scoreOf(el);
    if (score > bestScore) {
      bestScore = score;
      best = el;
    }
  }
  document.querySelectorAll("[data-sparkflow-live]").forEach((n) => n.removeAttribute("data-sparkflow-live"));
  if (!best) return { ok: false, verify: inVerify, score: -1 };
  best.setAttribute("data-sparkflow-live", "1");
  const r = best.getBoundingClientRect();
  return {
    ok: true,
    verify: inVerify,
    w: Math.round(r.width),
    h: Math.round(r.height),
    score: bestScore,
    text: (best.innerText || "").replace(/\s+/g, " ").slice(0, 80)
  };
}
