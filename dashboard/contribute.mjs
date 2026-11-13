// The contribute page: the install command with the chosen amount, and how many tiles are left.
const STATUS='https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/main/status/pipeline.json';
const BASE='curl -fsSL https://ericspencer.us/earthcraft/contribute.sh | sh';

export const command=tiles=>tiles?`${BASE} -s -- --tiles ${tiles}`:BASE;

// Plan size is every tile the journal knows (complete + pending + running in any one stage).
export function tilesLeft(status){
  const stage=status?.journal?.['0'],detailed=status?.detailed_tiles?.count;
  if(!stage||!Number.isFinite(detailed))return null;
  const plan=['complete','pending','running','failed'].reduce((sum,key)=>sum+(Number(stage[key])||0),0);
  return plan>detailed?{plan,left:plan-detailed}:null;
}

if(typeof document!=='undefined'){
  const code=document.getElementById('command'),copy=document.getElementById('copy');
  for(const input of document.querySelectorAll('input[name=amount]'))
    input.addEventListener('change',()=>{code.textContent=command(input.value);});
  copy.addEventListener('click',async()=>{
    try{await navigator.clipboard.writeText(code.textContent);copy.textContent='Copied';}
    catch{const range=document.createRange();range.selectNodeContents(code);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);copy.textContent='Selected';}
    setTimeout(()=>{copy.textContent='Copy';},2000);
  });
  const state=document.getElementById('tiles-left');
  fetch(STATUS,{cache:'no-store'}).then(r=>r.ok?r.json():null).then(status=>{
    const left=tilesLeft(status);
    state.textContent=left?`${left.left.toLocaleString()} of ${left.plan.toLocaleString()} tiles still need full detail.`:'';
  }).catch(()=>{state.textContent='';});
}
