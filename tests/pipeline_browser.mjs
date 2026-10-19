// Isolated browser checks: deterministic telemetry and real chunk bytes, no live HF dependence.
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {createRangeServer} from './range_server.mjs';

const root=fileURLToPath(new URL('../',import.meta.url)),site=process.argv[2]?resolve(process.argv[2]):resolve(root,'dashboard');
const server=createRangeServer({mounts:[{prefix:'/',dir:site}],headers:{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://huggingface.co; img-src 'self' data: blob:; worker-src 'self' blob:"}});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const origin=`http://127.0.0.1:${server.address().port}`,revision='a'.repeat(40);
const header=Buffer.alloc(8192); // An absent excerpt is a valid outcome; it must not fabricate geometry.
const now=new Date().toISOString();
const batch={id:revision,date:now,title:'Generate tiles/v2: 2 tile(s) -136_68, -130_68: batch 1 (16 files) [hf_store]'};
const fixture={schema:'earthcraft.pipeline-status.v1',time_utc:now,service:'active',workers:4,tiles_last_hour:19,tiles_last_24h:1121,disk:{free_bytes:33*2**30},journal:{0:{complete:25258,running:4,pending:70126},1:{complete:25257},2:{complete:789},3:{pending:95399}}};
const manifest={schema:'earthcraft.generated-tile.v2',tile:'-136_68',region:{x:-136,z:68},quality:'scan-points-and-roof',generator:{seconds:697},files:[{role:'region',path:'tiles/v2/-136_68/region/r.-136.68.mca',bytes:4202496,sha256:'f'.repeat(64)}]};
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||undefined,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const problems=[];let commitsFail=false,old=false,regionRequests=0;
await mkdir(resolve(root,'test-results'),{recursive:true});
try{
  const context=await browser.newContext();
  await context.route('https://huggingface.co/**',async route=>{
    const url=route.request().url();let value;
    if(url.includes('/commits/main')){if(commitsFail)return route.fulfill({status:503,body:'Unavailable'});value=[batch];}
    else if(url.endsWith('/status/pipeline.json'))value={...fixture,time_utc:old?new Date(Date.now()-4*3600000).toISOString():now};
    else if(url.endsWith('/tile.json'))value=manifest;
    else if(url.endsWith('/r.-136.68.mca')){regionRequests++;return route.fulfill({status:206,body:header,headers:{'Content-Range':'bytes 0-8191/4202496','Content-Type':'application/octet-stream','Access-Control-Allow-Origin':'*'}});}
    else throw Error('Unexpected network request: '+url);
    return route.fulfill({status:200,body:JSON.stringify(value),contentType:'application/json',headers:{'Access-Control-Allow-Origin':'*'}});
  });
  const page=await context.newPage();page.on('pageerror',e=>problems.push(e.message));
  for(const [name,width,height] of [['desktop',1440,1000],['phone',390,844]]){
    await page.setViewportSize({width,height});await page.goto(origin+'/pipeline.html');
    await expect(page.locator('#tile-id')).toHaveText('-136_68');await expect(page.locator('#rate-day')).toHaveText('1,121');
    await expect(page.locator('#worker-state')).toHaveText('Reported active');
    await expect(page.locator('#preview-status')).toContainText('no stored chunks',{timeout:30000});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No horizontal overflow');
    const href=await page.locator('#tile-open').getAttribute('href');assert.ok(href.includes(revision)&&href.includes('layout=flat'));
    const requestsBefore=regionRequests;await page.locator('#pipeline-refresh').click();await expect(page.locator('#pipeline-refresh')).toBeEnabled();assert.equal(regionRequests,requestsBefore,'Unchanged tile is not downloaded again');
    const axe=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();assert.deepEqual(axe.violations.map(x=>({id:x.id,nodes:x.nodes.length})),[]);
    await page.screenshot({path:resolve(root,`test-results/pipeline-${name}.png`),fullPage:true});
  }
  old=true;await page.locator('#pipeline-refresh').click();await expect(page.locator('#worker-state')).toHaveText('Older report');
  commitsFail=true;await page.locator('#pipeline-refresh').click();await expect(page.locator('#pipeline-error')).toContainText('could not be loaded');await expect(page.locator('#tile-id')).toHaveText('-136_68');
  assert.deepEqual(problems,[]);console.log('Pipeline browser: desktop/mobile, accessibility, stale reports, feed failure, pinned preview and unchanged-tile caching passed');
}finally{await browser.close();await new Promise(done=>server.close(done));}
