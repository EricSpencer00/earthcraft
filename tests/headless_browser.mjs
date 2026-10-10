// Static evidence page only: no game launch, remote basemap or private inputs.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,sep,extname} from 'node:path';
import {chromium,expect} from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const root=resolve(process.argv[2]||'site/headless');
const report=JSON.parse(await readFile(resolve(root,'report.json'),'utf8'));
const mime={'.html':'text/html','.css':'text/css','.png':'image/png','.json':'application/json'};
const server=createServer(async(req,res)=>{
  try{
    const path=new URL(req.url,'http://localhost').pathname;
    const file=resolve(root,path==='/'?'index.html':path.slice(1));
    assert(file.startsWith(root+sep));
    const data=await readFile(file);res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream'});res.end(data);
  }catch{res.writeHead(404);res.end('Not found');}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const browser=await chromium.launch();
try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const [width,height] of [[1440,1000],[390,844],[320,720]]){
    await page.setViewportSize({width,height});await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await expect(page.getByRole('heading',{level:1})).toHaveText('Built from stored blocks.');
    await expect(page.locator('.metrics')).toContainText(report.source.actual_chunks.toLocaleString('en-US'));
    await expect(page.locator('body')).toContainText('Absolute vertical datum');
    await expect(page.locator('body')).toContainText('illustrative');
    await page.waitForFunction(()=>[...document.images].every(image=>image.complete&&image.naturalWidth>0));
    const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,
      images:[...document.images].map(image=>[image.naturalWidth,image.naturalHeight])}));
    assert(layout.scroll<=layout.width,'Page overflows the viewport');
    assert.deepEqual(layout.images,[[1280,960],[512,512]]);
    const accessibility=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
    assert.deepEqual(accessibility.violations,[],`Evidence page accessibility: ${width}`);
    const receipt=await page.request.get(`http://127.0.0.1:${server.address().port}/report.json`);
    assert.equal(receipt.status(),200);assert.deepEqual(await receipt.json(),report);
    console.log(`Headless evidence page: ${width}px layout, images, receipt and accessibility passed`);
  }
  assert.deepEqual(errors,[]);
}finally{await browser.close();await new Promise(done=>server.close(done));}
