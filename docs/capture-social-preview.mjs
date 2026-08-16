// Renders docs/social-preview.html to the 1280x640 PNG GitHub expects.
// Run with the repo's dev dependency of Playwright available, e.g.
//   node docs/capture-social-preview.mjs
import { chromium } from '@playwright/test'
import { pathToFileURL } from 'node:url'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 640 }, deviceScaleFactor: 1 })
await page.goto(pathToFileURL(new URL('./social-preview.html', import.meta.url).pathname).href)
await page.screenshot({ path: new URL('./assets/social-preview.png', import.meta.url).pathname })
await browser.close()
