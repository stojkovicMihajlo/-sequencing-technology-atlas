// Local visual/interaction check. Uses the already-installed browser when set.
import {chromium, expect} from '@playwright/test';
import fs from 'node:fs/promises';
const browser = await chromium.launch(process.env.ATLAS_BROWSER ? {executablePath:process.env.ATLAS_BROWSER} : {});
const page = await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];
page.on('pageerror', e=>errors.push(e.message));
try {
  await page.goto(process.env.ATLAS_PREVIEW || 'http://127.0.0.1:5173');
  await expect(page.locator('.method-card')).toHaveCount(108);
  await page.getByRole('textbox',{name:'Search methods'}).fill('ATAC');
  await page.locator('.method-card').filter({has:page.getByRole('heading',{name:'ATAC-Seq',exact:true})}).click();
  await page.getByRole('button',{name:'Text view',exact:true}).click();
  await expect(page.locator('.operation-card')).toHaveCount(4);
  await page.screenshot({path:'test-results/atac-reader.png'});
  await page.getByRole('button',{name:'Method diagram',exact:true}).click();
  await expect(page.locator('.react-flow__node').first()).toBeVisible({timeout:30000});
  const count=await page.locator('.react-flow__node').count();
  await page.locator('.react-flow__node').first().click();
  await expect(page.locator('.inspector h2')).toBeVisible();
  await fs.mkdir('test-results',{recursive:true});
  await page.screenshot({path:'test-results/atac-workflow.png'});
  await page.getByRole('button',{name:'Illumina diagram',exact:true}).click();
  const image=page.locator('.diagram-wrap img').first();
  await expect(image).toBeVisible();
  await expect.poll(()=>image.evaluate(img=>img.naturalWidth)).toBeGreaterThan(0);
  await page.getByRole('button',{name:'Sources & review',exact:true}).click();
  await expect(page.locator('.source-link').first()).toBeVisible();
  await page.getByRole('button',{name:'← Method library'}).click();
  await expect(page.getByRole('textbox',{name:'Search methods'})).toHaveValue('ATAC');
  await page.getByRole('textbox',{name:'Search methods'}).fill('');
  await expect(page.locator('.method-card')).toHaveCount(108);
  await page.screenshot({path:'test-results/library.png'});
  await page.getByRole('combobox',{name:'Review status'}).selectOption('all');
  await expect(page.locator('.method-card')).toHaveCount(174);
  expect(errors).toEqual([]);
  console.log(JSON.stringify({acceptedCards:108,allCards:174,atacNodes:count,browserErrors:errors}));
} finally {await browser.close();}
