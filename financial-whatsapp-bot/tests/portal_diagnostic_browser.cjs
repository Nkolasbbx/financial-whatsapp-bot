/* Run with NODE_PATH pointing to installed playwright and Python as first argument. */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const assert = require('assert');
const root = path.resolve(__dirname, '..');
const fixture = JSON.parse(execFileSync(process.argv[2], [path.join(__dirname, 'portal_diagnostic_preview.py')], {encoding: 'utf8', cwd: root}));
(async () => {
    const browser = await chromium.launch({headless: true, ...(process.env.TEST_BROWSER_CHANNEL ? {channel: process.env.TEST_BROWSER_CHANNEL} : {})});
    try {
        const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, deviceScaleFactor: 1});
        const errors = [], writes = [];
        page.on('pageerror', error => errors.push(error.message));
        let conflict = false, state = structuredClone(fixture.data);
        await page.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.pathname === '/portal/api/diagnostic') {
                if (route.request().method() === 'PUT') {
                    const body = route.request().postDataJSON(); writes.push(body);
                    assert.equal(route.request().headers()['x-csrf-token'], 'test-csrf');
                    if (conflict) return route.fulfill({status: 409, json: {detail: 'El diagnóstico cambió. Recarga antes de editar.'}});
                    state.lock_version++;
                    state.summary.initial_remaining_max = 150000;
                }
                return route.fulfill({json: state});
            }
            if (url.pathname.startsWith('/static/')) {
                const file = path.join(root, decodeURIComponent(url.pathname));
                if (fs.existsSync(file)) return route.fulfill({body: fs.readFileSync(file), contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'image/svg+xml'});
            }
            if (url.pathname === '/portal') return route.fulfill({body: fixture.html, contentType: 'text/html'});
            return route.fulfill({status: 204, body: ''});
        });
        await page.goto('https://financial.test/portal?tab=diagnostico');
        await page.locator('#diag-content').waitFor({state: 'visible'});
        const saveBox = await page.locator('#diag-save').boundingBox();
        assert(saveBox.y >= 0 && saveBox.y + saveBox.height <= 844, 'mobile save action must remain visible');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'mobile horizontal overflow');
        const c12 = state.items.find(i => i.key === 'estimate:C12');
        const card = page.locator('.diag-cost').filter({has: page.locator('summary strong', {hasText: c12.title})});
        await card.locator('summary').click();
        await card.getByLabel('¿Cómo quieres considerar este costo?').selectOption('manual');
        await card.getByLabel('Monto total en pesos (antes de descontar abonos)').fill('200000');
        await card.getByLabel('¿Cuánto ya pagaste? (0 si nada)').fill('50000');
        await card.getByLabel('Fecha en que pagarás el saldo').fill('2026-11-15');
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'expanded card horizontal overflow');
        const out = path.resolve(root, '..', 'local_testing'); fs.mkdirSync(out, {recursive: true});
        await page.screenshot({path: path.join(out, 'diagnostico-mobile.png'), fullPage: true});
        await card.locator('summary').scrollIntoViewIfNeeded();
        await page.screenshot({path: path.join(out, 'diagnostico-mobile-viewport.png')});
        await page.getByRole('button', {name: 'Guardar y recalcular'}).click();
        await page.waitForFunction(() => document.getElementById('diag-status').textContent.includes('Cambios guardados'));
        assert.equal(writes.length, 1); assert.equal(writes[0].changes.length, 1);
        assert.equal(writes[0].changes[0].amount, 200000);
        // Conflicting changes must remain on screen, never silently overwrite.
        conflict = true;
        const current = page.locator('.diag-cost').filter({has: page.locator('summary strong', {hasText: c12.title})});
        await current.locator('summary').click();
        await current.getByLabel('¿Cómo quieres considerar este costo?').selectOption('manual');
        await current.getByLabel('Monto total en pesos (antes de descontar abonos)').fill('300000');
        await page.getByRole('button', {name: 'Guardar y recalcular'}).click();
        await page.waitForFunction(() => document.getElementById('diag-status').textContent.includes('cambió'));
        assert.equal(await current.getByLabel('Monto total en pesos (antes de descontar abonos)').inputValue(), '300000');
        assert(await page.locator('#diag-save').isDisabled());
        await page.setViewportSize({width: 1280, height: 900});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'desktop overflow');
        await page.screenshot({path: path.join(out, 'diagnostico-desktop.png'), fullPage: true});
        assert.deepEqual(errors, []);
        console.log('PASS: mobile/desktop layout, save batch, CSRF header, conflict preservation, no browser errors');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
