const { chromium } = require('playwright');
const dir = process.argv[2]; const B = process.env.BASE_URL || 'http://localhost:8080';
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1300, height: 800 } });
  const a = await ctx.newPage();
  await a.goto(B + '/sign-in'); await a.fill('input[name=email]', 'demo@astro.example'); await a.fill('input[name=password]', 'demo-password-123');
  await a.click('button[type=submit]'); await a.waitForURL('**/app'); await a.goto(B + '/app/bookings'); await a.waitForTimeout(1500);
  console.log('tab A before:', (await a.innerText('header')).replace(/\n/g, ' | '));
  console.log('tab A bookings:', (await a.$$eval('main li p.font-semibold', e => e.map(x => x.innerText))).join('; '));
  // Tab B: the same browser, another person signs in.
  const bt = await ctx.newPage();
  await bt.goto(B + '/sign-in'); // proxy redirects signed-in users to /app; sign out first like a real switch would
  console.log('tab B landed on', new URL(bt.url()).pathname);
  await bt.evaluate(() => document.cookie.split(';').forEach(c => { document.cookie = c.split('=')[0].trim() + '=; Max-Age=0; path=/'; }));
  await bt.goto(B + '/sign-in'); await bt.fill('input[name=email]', 'second@astro.example'); await bt.fill('input[name=password]', 'second-password-123');
  await bt.click('button[type=submit]'); await bt.waitForURL('**/app'); await bt.waitForTimeout(2500);
  // Tab A got the BroadcastChannel message and must have reloaded as the second person.
  await a.waitForTimeout(2500);
  console.log('tab A after:', (await a.innerText('header')).replace(/\n/g, ' | '), '@', new URL(a.url()).pathname);
  const after = await a.$$eval('main li p.font-semibold', e => e.map(x => x.innerText)).catch(() => []);
  console.log('tab A items after:', after.join('; ') || '(none)');
  await a.screenshot({ path: dir + '/iso-tabA-after.png' });
  await b.close();
})();
