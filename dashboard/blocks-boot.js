// Runs before the 3D modules load so automated captures can poll from the start.
window.__earthcraftRender={status:'loading'};
if(new URLSearchParams(location.search).get('qa')==='1')document.documentElement.dataset.qa='1';
