// 豆包会话只读读取工具 v1（2026-10-02 固化自 tmp_doubao_v2.mjs，经 12 会话实战验证）
// 用法：
//   node doubao_read.mjs --list              只列侧边栏会话（标题+id），不读正文
//   node doubao_read.mjs                     按 targets.json 全量读取
//   node doubao_read.mjs --only A1,A3b       只读指定 label（逗号分隔，模糊匹配）
// 前置：本地 Edge 已开 CDP(9222)、豆包已登录、至少开着一个 doubao.com 标签页
// 铁律：本工具只读。不发送任何输入、不点发送按钮；唯一 UI 交互是点「参考 N 篇资料」展开面板。
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';

const HERE = path.dirname(fileURLToPath(import.meta.url));
// playwright-core 解析顺序：工具目录本地 node_modules → D:\geo\tmpcdp\node_modules（有实测依赖）
function loadPlaywright() {
  const candidates = [HERE, 'D:\\geo\\tmpcdp'];
  for (const dir of candidates) {
    const pkg = path.join(dir, 'node_modules', 'playwright-core', 'package.json');
    if (fs.existsSync(pkg)) return createRequire(path.join(dir, 'package.json'))('playwright-core');
  }
  console.error('找不到 playwright-core：请在 HERE 或 D:\\geo\\tmpcdp 下 npm i playwright-core');
  process.exit(1);
}
const { chromium } = loadPlaywright();
const cfg = JSON.parse(fs.readFileSync(path.join(HERE, 'targets.json'), 'utf-8'));
const argOnly = (process.argv.find(a => a.startsWith('--only')) || '').replace('--only', '').replace(/^=/, '');
const LIST_MODE = process.argv.includes('--list');
const onlySet = argOnly ? new Set(argOnly.split(',').map(s => s.trim())) : null;

const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
const ctx = browser.contexts()[0];
const page = ctx.pages().find(p => p.url().includes('doubao.com'));
if (!page) { console.error('未找到 doubao.com 标签页，请先在 Edge 打开豆包'); process.exit(1); }

// ---------- --list：只列侧边栏会话 ----------
if (LIST_MODE) {
  await page.goto('https://www.doubao.com/chat/', { waitUntil: 'domcontentloaded', timeout: 30000 });
  // 侧边栏水合较慢（实测 2.5s 时为 0 个锚点、4s 时 56 个），轮询等到出现数字 id 会话
  let items = [];
  for (let i = 0; i < 10; i++) {
    await page.waitForTimeout(1500);
    items = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll('a[href*="/chat/"]').forEach(a => {
        const m = (a.href || '').match(/\/chat\/(\d{10,})/);
        const t = (a.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 50);
        if (m && t) out.push({ id: m[1], title: t });
      });
      return out;
    });
    if (items.length > 0) break;
  }
  const seen = new Set();
  items.forEach(it => {
    if (seen.has(it.id)) return; seen.add(it.id);
    console.log(`${it.id}  ${it.title}`);
  });
  console.log(`\n共 ${seen.size} 个会话。要读某个会话：把 id 加进 targets.json 或用 --only。`);
  await browser.close();
  process.exit(0);
}

// ---------- 读取模式 ----------
const sessions = cfg.sessions.filter(s => !onlySet || [...onlySet].some(k => s.label.includes(k)));
if (sessions.length === 0) { console.error('--only 没匹配到任何 label'); process.exit(1); }
const OUT = cfg.outDir.endsWith('\\') ? cfg.outDir : cfg.outDir + '\\';
fs.mkdirSync(OUT, { recursive: true });

// 真实 URL 解码（豆包用 link.wtturl.cn 跳转包裹）
const decodeHref = `(href) => {
  try {
    const m = href.match(/target=([^&]+)/);
    if (m) return decodeURIComponent(m[1]);
    const m2 = href.match(/url=([^&]+)/);
    if (m2) return decodeURIComponent(m[1] === undefined ? href : m2[1]);
  } catch (e) {}
  return href;
}`;

const res = [];
for (const { label, id } of sessions) {
  await page.goto(`https://www.doubao.com/chat/${id}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  let prev = -1, text = '';
  for (let i = 0; i < 10; i++) {           // 等渲染稳定（长度连续两次不变且 >800）
    await page.waitForTimeout(1500);
    text = await page.evaluate(() => document.body.innerText || '');
    if (text.length === prev && text.length > 800) break;
    prev = text.length;
  }

  // 剔除侧边栏：取「下载电脑版」之后、底部推荐/会话列表之前
  // ★ 教训：侧边栏标题含「逐曜AI」「结构可引用度」等，不剔除会把标记判成假阳性
  let body = text;
  const cut1 = body.indexOf('下载电脑版');
  if (cut1 >= 0) body = body.slice(cut1 + '下载电脑版'.length);
  for (const end of ['\n对话\nPPT 生成', '\n对话\n', '今天 ']) {
    const p = body.indexOf(end);
    if (p > 100) { body = body.slice(0, p); break; }
  }
  body = body.trim();
  if (body.length < 300) console.warn(`⚠ ${label}: 剔除侧边栏后正文仅 ${body.length} 字符，人工确认是否切错`);

  // 展开「参考资料」面板（只点展开，不发消息）
  let srcText = '', srcLinks = [];
  try {
    const btn = page.locator('text=/参考\\s*\\d+\\s*篇资料/').first();
    if (await btn.count() > 0 && await btn.isVisible().catch(() => false)) {
      await btn.click({ timeout: 5000 });
      await page.waitForTimeout(2500);
      srcText = await page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('div'));
        const hit = els.find(e => /参考资料|参考来源/.test((e.innerText || '').slice(0, 40)) && (e.innerText || '').length > 100);
        return hit ? hit.innerText.slice(0, 2000) : '';
      });
      srcLinks = await page.evaluate(`(() => {
        const d = ${decodeHref};
        const out = [];
        document.querySelectorAll('a[href]').forEach(a => {
          const real = d(a.href || '');
          if (/^https?:\\/\\//.test(real) && !/doubao\\.com|bytedance|byteimg|wtturl/.test(real)) {
            out.push({ real: real.slice(0, 130), txt: (a.innerText || '').trim().slice(0, 70) });
          }
        });
        return out;
      })()`);
    }
  } catch (e) { /* 展开失败不影响正文 */ }

  fs.writeFileSync(OUT + label + '.正文.txt', body, 'utf-8');
  if (srcText) fs.writeFileSync(OUT + label + '.参考资料.txt', srcText + '\n\n' + srcLinks.map(l => l.txt + ' -> ' + l.real).join('\n'), 'utf-8');

  const uniq = [...new Map(srcLinks.map(l => [l.real, l])).values()];
  const flags = {};
  for (const mk of cfg.markers) {
    const re = mk.includes('.') || mk.includes('/') ? new RegExp(mk.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) : null;
    flags[mk] = re ? re.test(body + srcText) : (body + srcText).includes(mk);
  }
  console.log(`\n===== ${label} =====`);
  console.log('正文长度:', body.length, '| 来源链接:', uniq.length);
  console.log('标记:', Object.entries(flags).filter(([, v]) => v).map(([k]) => k).join(' / ') || '(无)');
  uniq.slice(0, 10).forEach(l => console.log('  源:', (l.txt || '(无标题)').slice(0, 50), '->', l.real.slice(0, 100)));
  res.push({ label, id, len: body.length, flags, srcLinks: uniq });
}

fs.writeFileSync(OUT + '_分析.json', JSON.stringify(res, null, 2), 'utf-8');
console.log(`\n完成 ${res.length} 个会话 → ${OUT}`);
await browser.close();
