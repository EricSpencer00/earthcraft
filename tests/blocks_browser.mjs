// Browser checks for the 3D block viewer against a local Range server and the committed
// 9-chunk Water Tower fixture. No network access is needed: three.js is vendored.
//   node tests/blocks_browser.mjs [site-root]
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {chromium,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {createRangeServer} from './range_server.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
const site=process.argv[2]?resolve(process.argv[2]):resolve(root,'dashboard');
const fixture=resolve(root,'tests/fixtures/anvil/water-tower-r.0.0.region');
const server=createRangeServer({mounts:[{path:'/fixtures/region/0/r.0.0.mca',file:fixture},{prefix:'/',dir:site}]});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const origin=`http://127.0.0.1:${server.address().port}`;
await mkdir(resolve(root,'test-results'),{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||undefined,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const problems=[];
const watch=page=>{page.on('pageerror',error=>problems.push(error.message));page.on('console',message=>{if(message.type()==='error')problems.push(message.text());});};
const done=page=>page.waitForFunction(()=>window.__earthcraftRender&&window.__earthcraftRender.status!=='loading',null,{timeout:60000}).then(()=>page.evaluate(()=>window.__earthcraftRender));
async function variety(page,locator){
  const png=await locator.screenshot();
  return page.evaluate(async base64=>{
    const image=new Image();image.src='data:image/png;base64,'+base64;await image.decode();
    const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
    const data=ctx.getImageData(0,0,canvas.width,canvas.height).data,colors=new Set();let sky=0,total=0;
    for(let i=0;i<data.length;i+=4*7){colors.add(data[i]>>3<<10|data[i+1]>>3<<5|data[i+2]>>3);total++;if(Math.abs(data[i]-201)<4&&Math.abs(data[i+1]-217)<4&&Math.abs(data[i+2]-227)<4)sky++;}
    return {colors:colors.size,sky:sky/total};
  },png.toString('base64'));
}

try{
  // Deterministic QA captures: only the canvas, fixed size, status reported on window.
  const qa=await browser.newPage({viewport:{width:1000,height:800}});watch(qa);
  for(const view of ['iso','top','street']){
    await qa.goto(`${origin}/blocks.html?qa=1&base=/fixtures/region/&region=0,0&chunk=2,2&radius=1&view=${view}&yaw=${{iso:45,top:0,street:270}[view]}&w=640&h=480`);
    const result=await done(qa);
    assert.equal(result.status,'done',`${view}: ${result.error}`);
    assert.equal(result.chunks_requested,9);assert.equal(result.chunks_loaded,9);assert.equal(result.missing,0);assert.ok(result.blocks>5000,`${view}: blocks drawn`);
    assert.deepEqual(result.unknown_blocks,[]);
    const canvas=qa.locator('canvas').first();assert.deepEqual(await canvas.boundingBox(),{x:0,y:0,width:640,height:480});
    const hidden=await qa.evaluate(()=>[...document.querySelectorAll('.site-header,.viewer-tools,.viewer-readout,.details-panel')].every(e=>getComputedStyle(e).display==='none'));
    assert.ok(hidden,`${view}: QA capture hides page chrome`);
    const look=await variety(qa,canvas);
    assert.ok(look.colors>=(view==='street'?8:24),`${view}: canvas shows varied blocks (${look.colors} colors)`);assert.ok(look.sky<.95,`${view}: canvas is not just sky`);
    await canvas.screenshot({path:resolve(root,`test-results/blocks-${view}.png`)});
  }
  // Chunks absent from the fixture and a missing region are reported, not errors.
  await qa.goto(`${origin}/blocks.html?qa=1&base=/fixtures/region/&chunk=40,40&radius=1&view=iso&w=320&h=240`);
  let result=await done(qa);
  assert.equal(result.status,'done');assert.equal(result.chunks_loaded,0);assert.equal(result.missing,9);assert.equal(result.blocks,0);
  await qa.goto(`${origin}/blocks.html?qa=1&base=/fixtures/region/&chunk=2,2&radius=2&view=top&w=320&h=240`);
  result=await done(qa);assert.equal(result.chunks_loaded,9);assert.equal(result.missing,16);
  await qa.goto(`${origin}/blocks.html?qa=1&chunk=bad`);result=await done(qa);assert.equal(result.status,'error');assert.match(result.error,/chunk must be two integers/);
  console.log('QA captures (iso, top, street), coverage gaps and parameter errors passed');

  // Interactive page: layout, accessibility, picking and camera switching.
  for(const [name,width,height] of [['desktop',1440,900],['phone',390,844]]){
    const context=await browser.newContext({viewport:{width,height},hasTouch:name==='phone'});const page=await context.newPage();watch(page);
    await page.goto(`${origin}/blocks.html?base=/fixtures/region/&chunk=2,2&radius=1`);
    result=await done(page);assert.equal(result.status,'done');
    await page.evaluate(()=>document.fonts.ready);
    await expect(page.locator('#viewer-status')).toContainText('Showing 9 of 9 chunks');
    await expect(page.locator('#viewer-wrap')).toHaveAttribute('aria-busy','false');
    const layout=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,small:[...document.querySelectorAll('button,select,input,summary,.site-header a')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&e.getBoundingClientRect().height<44).map(e=>e.id||e.textContent.trim())}));
    assert.ok(layout.scroll<=layout.width,`${name}: horizontal overflow`);assert.deepEqual(layout.small,[],`${name}: small touch targets`);
    if(name==='desktop'){
      await expect(page.locator('#viewer-panel')).toBeVisible();
      await expect(page.locator('#coverage-summary')).toContainText('9 chunks requested: 9 loaded');
      const box=await page.locator('#viewer').boundingBox();
      await page.mouse.move(box.x+box.width*.62,box.y+box.height*.55);
      await expect(page.locator('#readout-block')).toContainText(/minecraft:[a-z_]+ · X -?\d+, Y -?\d+, Z -?\d+/);
      await page.locator('#viewer').focus();await page.keyboard.press('3');
      await expect(page.locator('.view-switch button[data-view="top"]')).toHaveAttribute('aria-pressed','true');
      await expect(page).toHaveURL(/view=top/);
      await page.getByRole('button',{name:'Street'}).click();
      await expect(page.locator('#readout-camera')).toContainText('Eye X');
      await page.keyboard.press('w');
      await page.locator('#goto-radius').selectOption('0');await page.getByRole('button',{name:'Load'}).click();
      await expect(page.locator('#viewer-status')).toContainText('Showing 1 of 1 chunks');
    }else{
      await expect(page.locator('#viewer-panel')).toBeHidden();
      await page.locator('#panel-toggle').click();await expect(page.locator('#viewer-panel')).toBeVisible();
    }
    const accessibility=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
    assert.deepEqual(accessibility.violations.map(v=>v.id+': '+v.nodes.map(n=>n.target.join(' ')).join(', ')),[],`${name}: accessibility violations`);
    await page.screenshot({path:resolve(root,`test-results/blocks-page-${name}.png`)});
    await context.close();
    console.log(`${name}: block viewer layout, accessibility and controls passed`);
  }

  const noWebGL=await browser.newPage();
  await noWebGL.addInitScript(()=>{const original=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return String(type).startsWith('webgl')?null:original.call(this,type,...args);};});
  await noWebGL.goto(`${origin}/blocks.html?base=/fixtures/region/&chunk=2,2&radius=1`);
  result=await done(noWebGL);assert.equal(result.status,'error');
  await expect(noWebGL.locator('#viewer-status')).toContainText('WebGL is unavailable');
  await noWebGL.close();
  console.log('WebGL fallback message passed');
  assert.deepEqual(problems.filter(message=>!/WebGL|404 \(Not Found\)/.test(message)),[],'Unexpected browser errors');
}finally{
  await browser.close();
  await new Promise(done=>server.close(done));
}
