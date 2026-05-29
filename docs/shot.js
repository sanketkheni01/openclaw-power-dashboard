// Headless screenshot helper for the Ultimate UI Design audit loop.
// Usage: node docs/shot.js <url> <outPath> [width] [height] [fullPage] [clickSel] [waitMs]
const puppeteer = require('puppeteer');

(async () => {
  const [,, url, out, w='1440', h='900', full='false', clickSel='', waitMs='0'] = process.argv;
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--force-color-profile=srgb'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: +w, height: +h, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 60000 }).catch(()=>{});
  try { await page.evaluate(() => document.fonts.ready); } catch {}
  await new Promise(r => setTimeout(r, 1800));
  if (clickSel) {
    try {
      await page.evaluate((sel) => {
        const el = document.querySelector(sel);
        if (el) el.click();
      }, clickSel);
    } catch {}
  }
  if (+waitMs > 0) await new Promise(r => setTimeout(r, +waitMs));
  await page.screenshot({ path: out, fullPage: full === 'true' });
  await browser.close();
  console.log('shot:', out);
})().catch(e => { console.error(e); process.exit(1); });
