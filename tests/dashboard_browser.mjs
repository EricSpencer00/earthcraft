// Isolated browser checks: no world files, local run records, or provider calls.
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {resolve, extname, sep} from 'node:path';
import {chromium, expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const root=fileURLToPath(new URL('../',import.meta.url));
const assets=resolve(root,'dashboard');
const snapshot=JSON.parse(await readFile(resolve(root,'progress/earth.json'),'utf8'));
const mime={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2','.json':'application/json'};
const server=createServer(async(req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  try{
    let body,filename;
    if(pathname==='/progress/earth.json'){
      body=JSON.stringify(snapshot);filename='earth.json';
    }else{
      filename=pathname==='/assets/water-tower.png'
        ?resolve(root,'docs/photos/watertower-sep-10-26.png')
        :resolve(assets,pathname==='/'?'index.html':pathname.slice(1));
      if(pathname!=='/assets/water-tower.png'&&!filename.startsWith(assets+sep))throw Error('Outside dashboard');
      body=await readFile(filename);
    }
    res.writeHead(200,{'Content-Type':mime[extname(filename)]||'application/octet-stream',
      'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"});
    res.end(body);
  }catch{res.writeHead(404);res.end('Not found');}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const url=`http://127.0.0.1:${server.address().port}/?view=published`;
const browser=await chromium.launch();
await mkdir(resolve(root,'test-results'),{recursive:true});
const problems=[];
const context=await browser.newContext({viewport:{width:1440,height:1000},hasTouch:true});
const page=await context.newPage();
page.on('pageerror',error=>problems.push(error.message));
page.on('console',message=>{if(message.type()==='error')problems.push(message.text());});

async function ready(){
  await page.goto(url);
  await expect(page.locator('#connection')).toHaveText('Published snapshot');
  await expect(page.locator('#mapwrap')).toHaveAttribute('aria-busy','false');
  await page.getByLabel('Auto-refresh').uncheck();
  await page.evaluate(()=>document.fonts.ready);
}
async function capture(name){
  // Return to the page heading so audit focus and scroll do not distort proof.
  await page.locator('#page-title').click();
  await page.screenshot({path:resolve(root,`test-results/${name}.png`),fullPage:true});
}

try{
  for(const [name,width,height] of [['desktop',1440,1000],['tablet',768,1024],['phone',390,844],['small-phone',320,720]]){
    await page.setViewportSize({width,height});
    await ready();
    await page.locator('.world-photo img').scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>document.querySelector('.world-photo img').naturalWidth>0);
    await page.evaluate(()=>window.scrollTo(0,0));
    const layout=await page.evaluate(()=>({
      width:innerWidth,scroll:document.documentElement.scrollWidth,
      headingOverflow:document.querySelector('h1').scrollWidth>document.querySelector('h1').clientWidth,
      fonts:[...document.fonts].map(font=>({family:font.family,status:font.status})),
      buttons:[...document.querySelectorAll('.map-controls button,#refresh')].map(button=>button.getBoundingClientRect().height),
      canvasWidth:document.querySelector('#map').width,
      canvasHeight:document.querySelector('#map').height
    }));
    assert(layout.scroll<=layout.width,`${name}: horizontal overflow`);
    assert(!layout.headingOverflow,`${name}: heading overflow`);
    assert(layout.fonts.filter(font=>font.status==='loaded').length===2,`${name}: font files did not load`);
    assert(layout.buttons.every(height=>height>=44),`${name}: small touch targets`);
    assert(layout.canvasWidth>0&&layout.canvasHeight>0,`${name}: empty canvas size`);
    const accessibility=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
    assert.deepEqual(accessibility.violations,[],`${name}: accessibility violations`);
    if(name==='phone'){
      await page.locator('#map').tap();
      await expect(page.locator('#selection-title')).toHaveText(/^Cell /);
      await expect(page.locator('#selection-details')).toBeVisible();
      await page.getByRole('button',{name:'Clear selection'}).click();
      console.log('Touch inspection passed');
    }
    await capture(name);
    console.log(`${name}: layout, assets, and accessibility passed`);
  }

  await page.setViewportSize({width:1440,height:1000});
  await ready();
  await page.locator('#map').press('ArrowRight');
  await expect(page.locator('#selection-title')).toHaveText(/^Cell /);
  await expect(page.locator('#selection-details')).toContainText('Surface detail');
  await expect(page.locator('#selection-details')).toContainText('Minecraft check');
  const selected=await page.locator('#selection-title').textContent();
  const fitScale=await page.locator('#map-scale').textContent();
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  assert.notEqual(await page.locator('#map-scale').textContent(),fitScale,'Zoom did not change the map scale');
  await page.getByRole('button',{name:/Refresh/}).click();
  await expect(page.locator('#selection-title')).toHaveText(selected);
  await expect(page.getByRole('button',{name:'Zoom out',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Fit the map'}).click();
  await expect(page.locator('#map-scale')).toHaveText(fitScale);
  await page.locator('#map').press('Escape');
  await expect(page.locator('#selection-title')).toHaveText('Choose a square');
  console.log('Keyboard inspection, zoom, fit, and refresh persistence passed');

  await page.route('**/progress/earth.json',route=>route.fulfill({status:503,body:'Unavailable'}));
  await page.getByRole('button',{name:/Refresh/}).click();
  await expect(page.locator('#warning')).toContainText('The last loaded map is still shown');
  await capture('disconnected');
  await page.reload();
  await expect(page.locator('#warning')).toContainText('Check your connection');
  await expect(page.locator('#phase')).toHaveText('Record unavailable');
  await expect(page.getByRole('button',{name:'Zoom in',exact:true})).toBeDisabled();
  await expect(page.locator('#mapwrap')).toHaveAttribute('aria-busy','false');
  await expect(page.getByRole('button',{name:/Refresh/})).toBeEnabled();
  await capture('unavailable');
  await page.unroute('**/progress/earth.json');
  await page.getByRole('button',{name:/Refresh/}).click();
  await expect(page.locator('#connection')).toHaveText('Published snapshot');
  await expect(page.locator('#warning')).toBeHidden();
  console.log('Disconnected map, initial failure, and retry passed');

  await page.route('**/progress/earth.json',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({...snapshot,cells:[]})}));
  await page.getByRole('button',{name:/Refresh/}).click();
  await expect(page.locator('#map-title')).toHaveText('No cells recorded');
  await expect(page.locator('#selection-title')).toHaveText('Choose a square');
  await expect(page.locator('#map-scale')).toBeEmpty();
  await capture('empty');
  console.log('Empty atlas passed');

  // Expected HTTP failures above appear in Chromium's console; other errors do not.
  assert.deepEqual(problems.filter(message=>!message.includes('503 (Service Unavailable)')),[],'Unexpected browser errors');
}catch(error){
  await capture('failure');
  throw error;
}finally{
  await browser.close();
  await new Promise(done=>server.close(done));
}
