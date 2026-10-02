// Isolated checks use the committed progress record and a public geographic basemap.
// No private world files or local build records are read or uploaded.
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
const mime={'.html':'text/html','.js':'text/javascript','.mjs':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2','.json':'application/json'};
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
      'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://tiles.openfreemap.org; img-src 'self' data: blob: https://tiles.openfreemap.org; worker-src 'self' blob:; frame-ancestors 'none'"});
    res.end(body);
  }catch{res.writeHead(404);res.end('Not found');}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const url=`http://127.0.0.1:${server.address().port}/?view=published`;
const browser=await chromium.launch({args:['--enable-unsafe-swiftshader']});
await mkdir(resolve(root,'test-results'),{recursive:true});
const problems=[];
const context=await browser.newContext({viewport:{width:1440,height:1000},hasTouch:true});
const page=await context.newPage();
page.on('pageerror',error=>problems.push(error.message));
page.on('console',message=>{if(message.type()==='error')problems.push(message.text());});

async function ready(view='published'){
  await page.goto(view==='world'?url.replace('?view=published',''):url);
  await expect(page.locator('#connection')).toHaveText('Published snapshot');
  await expect(page.locator('#mapwrap')).toHaveAttribute('aria-busy','false');
  if(await page.locator('#panel-toggle').getAttribute('aria-expanded')==='false')await page.locator('#panel-toggle').click();
  await page.getByLabel('Auto-refresh').uncheck();
  if(page.viewportSize().width<1024)await page.locator('#panel-close').click();
  await page.evaluate(()=>document.fonts.ready);
  await expect(page.locator('body')).toHaveAttribute('data-map','geographic');
  await expect(page.locator('body')).toHaveAttribute('data-basemap','ready',{timeout:45000});
}
async function capture(name){
  await page.locator('#page-title').click();
  await page.screenshot({path:resolve(root,`test-results/${name}.png`),fullPage:true});
}

try{
  // The current save is independent of the older generation journal.
  if(snapshot.generated_world){
    for(const [name,width,height] of [['world-desktop',1440,1000],['world-phone',390,844]]){
      await page.setViewportSize({width,height});
      await ready('world');
      await expect(page.locator('#map-title')).toHaveText('Generated world');
      await expect(page.locator('#grid-size')).toHaveText(snapshot.generated_world.unique_cells.toLocaleString()+' cells');
      await page.locator('#place-elmhurst').click();
      await expect(page.locator('#selection-title')).toHaveText('Cell -103, -2');
      await expect(page.locator('#selection-details')).toContainText('Street-photo colorPending');
      await expect(page.locator('#selection-details')).toContainText('-26,368 / -512');
      await expect(page.locator('#mapnote')).toContainText('Elmhurst · cell');
      await expect(page.locator('body')).toHaveAttribute('data-camera','idle',{timeout:15000});
      const accessibility=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
      assert.deepEqual(accessibility.violations,[],`${name}: accessibility violations`);
      await capture(name+'-elmhurst');
      await page.getByRole('button',{name:'Clear selection'}).click();
      await page.getByRole('button',{name:'Fit the map'}).click();
      await capture(name);
    }
    console.log('Complete saved footprint, Elmhurst coordinates, and photo-detail honesty passed');
  }
  for(const [name,width,height] of [['desktop',1440,1000],['tablet',768,1024],['phone',390,844],['small-phone',320,720]]){
    await page.setViewportSize({width,height});
    await ready();
    const layout=await page.evaluate(()=>({
      width:innerWidth,scroll:document.documentElement.scrollWidth,
      headingOverflow:document.querySelector('h1').scrollWidth>document.querySelector('h1').clientWidth,
      fonts:[...document.fonts].map(font=>({family:font.family,status:font.status})),
      buttons:[...document.querySelectorAll('.map-controls button,#refresh,.place-switch button,.layer-switch button')].map(button=>button.getBoundingClientRect().height),
      canvasWidth:document.querySelector('#map').width,
      canvasHeight:document.querySelector('#map').height,
      attributionVisible:!document.querySelector('#map-attribution').hidden
    }));
    assert(layout.scroll<=layout.width,`${name}: horizontal overflow`);
    assert(!layout.headingOverflow,`${name}: heading overflow`);
    assert(layout.fonts.filter(font=>font.status==='loaded').length===2,`${name}: font files did not load`);
    assert(layout.buttons.every(height=>height>=44),`${name}: small touch targets`);
    assert(layout.canvasWidth>0&&layout.canvasHeight>0,`${name}: empty canvas size`);
    assert(layout.attributionVisible,`${name}: missing basemap credits`);
    const accessibility=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
    assert.deepEqual(accessibility.violations,[],`${name}: accessibility violations`);
    await capture(name);
    if(name==='phone'){
      await page.locator('#place-chicago').tap();
      await expect(page.locator('#selection-title')).toHaveText(/^Cell /);
      await expect(page.locator('#selection-details')).toBeVisible();
      await expect(page.locator('body')).toHaveAttribute('data-camera','idle',{timeout:15000});
      const headingVisible=await page.locator('#selection-title').evaluate(heading=>{
        const title=heading.getBoundingClientRect(),scroller=heading.closest('.panel-content').getBoundingClientRect();
        return title.top>=scroller.top&&title.bottom<=scroller.bottom;
      });
      assert(headingVisible,'Selected-cell heading opened outside the bottom sheet viewport');
      await capture('phone-selected');
      await page.getByRole('button',{name:'Clear selection'}).click();
      await expect(page.locator('#details-panel')).toBeHidden();
      console.log('Touch inspection and bottom sheet passed');
    }
    console.log(`${name}: geographic map, layout, assets, and accessibility passed`);
  }

  await page.setViewportSize({width:1440,height:1000});
  await ready();
  const fitScale=await page.locator('#map-scale').textContent();
  await page.locator('#map').press('ArrowRight');
  await expect(page.locator('#selection-title')).toHaveText(/^Cell /);
  await expect(page.locator('#selection-details')).toContainText('Surface detail');
  await expect(page.locator('#selection-details')).toContainText('Minecraft check');
  const selected=await page.locator('#selection-title').textContent();
  await page.getByRole('button',{name:'Zoom in',exact:true}).click();
  assert.notEqual(await page.locator('#map-scale').textContent(),fitScale,'Zoom did not change the map scale');
  await page.getByRole('button',{name:'Refresh snapshot'}).click();
  await expect(page.locator('#selection-title')).toHaveText(selected);
  await expect(page.getByRole('button',{name:'Zoom out',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Fit the map'}).click();
  await expect(page.locator('#map-scale')).toHaveText(fitScale);
  await page.locator('#map').press('Escape');
  await expect(page.locator('#selection-title')).toHaveText('Choose a cell');
  console.log('Keyboard inspection, zoom, fit, and refresh persistence passed');

  await page.locator('#place-elmhurst').click();
  await expect(page.locator('#mapnote')).toContainText('Elmhurst has no cell in this published snapshot');
  await expect(page.locator('#clear-selection')).toBeHidden();
  await page.locator('#place-chicago').click();
  await expect(page.locator('#selection-details')).toContainText('41.');
  await expect(page.locator('body')).toHaveAttribute('data-camera','idle',{timeout:15000});
  await expect(page.locator('#mapnote')).not.toContainText('Elmhurst');
  const centerCell=await page.locator('#selection-title').textContent();
  await page.getByRole('button',{name:'Clear selection'}).click();
  const mapBox=await page.locator('#basemap').boundingBox();
  await page.locator('#basemap').click({position:{x:mapBox.width/2,y:mapBox.height/2}});
  await expect(page.locator('#selection-title')).toHaveText(centerCell);
  await capture('chicago-selected');
  await page.getByRole('button',{name:'Clear selection'}).click();
  await page.getByRole('button',{name:'Fit the map'}).click();
  await page.locator('#layer-appearance').click();
  await expect(page.locator('#mapnote')).toContainText('12 cells with surface detail');
  await expect(page.locator('#legend-source-label')).toHaveText('Awaiting detail');
  await capture('surface-detail');
  await page.locator('#layer-build').click();
  await page.locator('#about-open').click();
  await page.waitForFunction(()=>document.querySelector('.world-photo img').naturalWidth>0);
  await capture('about');
  await page.locator('#tab-world').click();
  console.log('Geographic shortcuts, honest appearance coverage, and project photo passed');

  await page.route('**/progress/earth.json',route=>route.fulfill({status:503,body:'Unavailable'}));
  await page.getByRole('button',{name:'Refresh snapshot'}).click();
  await expect(page.locator('#warning')).toContainText('The last loaded map is still shown');
  await capture('disconnected');
  await page.reload();
  await expect(page.locator('#warning')).toContainText('Check your connection');
  await expect(page.locator('#phase')).toHaveText('Record unavailable');
  await expect(page.getByRole('button',{name:'Zoom in',exact:true})).toBeDisabled();
  await expect(page.locator('#mapwrap')).toHaveAttribute('aria-busy','false');
  await expect(page.getByRole('button',{name:'Refresh snapshot'})).toBeEnabled();
  await capture('unavailable');
  await page.unroute('**/progress/earth.json');
  await page.getByRole('button',{name:'Refresh snapshot'}).click();
  await expect(page.locator('#connection')).toHaveText('Published snapshot');
  await expect(page.locator('#warning')).toBeHidden();
  console.log('Disconnected map, initial failure, and retry passed');

  await page.route('**/progress/earth.json',route=>route.fulfill({contentType:'application/json',body:JSON.stringify({...snapshot,cells:[]})}));
  await page.getByRole('button',{name:'Refresh snapshot'}).click();
  await expect(page.locator('#map-title')).toHaveText('No cells recorded');
  await expect(page.locator('#selection-title')).toHaveText('Choose a cell');
  await expect(page.locator('#map-scale')).toBeEmpty();
  await capture('empty');
  console.log('Empty record passed');

  const offline=await context.newPage();
  const offlineErrors=[];offline.on('pageerror',error=>offlineErrors.push(error.message));
  await offline.route('https://tiles.openfreemap.org/**',route=>route.abort());
  await offline.goto(url);
  await expect(offline.locator('#connection')).toHaveText('Published snapshot');
  await expect(offline.locator('#basemap-state')).toContainText('Street map unavailable');
  await offline.locator('#map').press('ArrowRight');
  await expect(offline.locator('#selection-details')).toBeVisible();
  await expect(offline.getByRole('button',{name:'Zoom in',exact:true})).toBeEnabled();
  await offline.screenshot({path:resolve(root,'test-results/basemap-offline.png')});
  assert.deepEqual(offlineErrors,[],'Basemap failure broke cell inspection');
  await offline.close();
  console.log('Basemap outage preserves cell inspection');

  const noWebGL=await context.newPage();
  await noWebGL.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...args){return String(type).startsWith('webgl')?null:original.call(this,type,...args);};
  });
  await noWebGL.goto(url);
  await expect(noWebGL.locator('#connection')).toHaveText('Published snapshot');
  await expect(noWebGL.locator('body')).toHaveAttribute('data-map','grid');
  await expect(noWebGL.locator('#basemap-state')).toContainText('Showing the generation grid');
  await noWebGL.locator('#layer-appearance').click();
  await expect(noWebGL.locator('#legend-finished-label')).toHaveText('Surface detail added');
  await noWebGL.locator('#map').press('ArrowRight');
  await expect(noWebGL.locator('#selection-details')).toBeVisible();
  await noWebGL.close();
  console.log('WebGL fallback preserves the generation and appearance views');

  // Expected snapshot failures appear in Chromium's console; other errors do not.
  assert.deepEqual(problems.filter(message=>!message.includes('503 (Service Unavailable)')),[],'Unexpected browser errors');
}catch(error){
  await capture('failure');
  throw error;
}finally{
  await browser.close();
  await new Promise(done=>server.close(done));
}
