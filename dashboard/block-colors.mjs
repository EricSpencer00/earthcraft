// Approximate vanilla block appearance as flat RGB, authored for this viewer.
// No Mojang textures or texture-derived files are used or redistributed.
// kind: cube (opaque), glass (transparent, culls same block), water, leaves,
// cross (plants), flat (carpet-height), slab, post (fence/wall), skip (air, torches…).
const DYES={white:[[207,213,214],[210,178,161],[234,236,237],[226,227,228],[246,248,248]],orange:[[224,97,1],[162,84,38],[241,118,20],[227,132,32],[216,127,51]],magenta:[[169,48,159],[150,88,109],[190,69,180],[193,84,185],[178,76,216]],light_blue:[[35,137,199],[113,109,138],[58,175,217],[74,181,213],[102,153,216]],yellow:[[241,175,21],[186,133,35],[249,198,40],[233,199,55],[229,229,51]],lime:[[94,169,24],[104,118,53],[112,185,26],[125,189,42],[127,204,25]],pink:[[214,101,143],[162,78,79],[238,141,172],[229,153,181],[242,127,165]],gray:[[55,58,62],[58,42,36],[63,68,72],[77,81,85],[96,99,104]],light_gray:[[128,128,121],[135,107,98],[142,142,135],[155,155,148],[168,170,172]],cyan:[[21,119,136],[87,91,91],[21,138,145],[37,148,157],[76,127,153]],purple:[[100,32,156],[118,70,86],[122,42,173],[132,56,178],[127,63,178]],blue:[[45,47,143],[74,60,91],[53,57,157],[70,73,167],[51,76,178]],brown:[[96,60,32],[77,51,36],[114,72,41],[126,85,54],[102,76,51]],green:[[73,91,36],[76,83,42],[85,110,28],[97,119,45],[102,127,51]],red:[[142,33,33],[143,61,47],[161,39,35],[168,54,51],[153,51,51]],black:[[8,10,15],[37,23,16],[21,21,26],[25,27,32],[32,34,40]]};
// [rgb, kind?, alpha?, top rgb?, bottom rgb?]
const B={
  stone:[[125,125,125]],cobblestone:[[122,121,122]],mossy_cobblestone:[[110,118,94]],stone_bricks:[[122,121,122]],mossy_stone_bricks:[[115,121,105]],cracked_stone_bricks:[[118,117,118]],chiseled_stone_bricks:[[119,118,119]],smooth_stone:[[159,159,159]],
  granite:[[149,103,85]],polished_granite:[[154,106,89]],diorite:[[188,188,188]],polished_diorite:[[192,193,194]],andesite:[[136,136,137]],polished_andesite:[[132,135,133]],
  deepslate:[[80,80,82]],cobbled_deepslate:[[77,77,80]],polished_deepslate:[[72,72,73]],deepslate_bricks:[[70,70,71]],deepslate_tiles:[[54,54,55]],tuff:[[108,109,102]],calcite:[[223,224,220]],dripstone_block:[[134,107,92]],
  bedrock:[[85,85,85]],gravel:[[131,127,126]],sand:[[219,207,163]],red_sand:[[190,102,33]],sandstone:[[216,203,155]],smooth_sandstone:[[223,214,170]],cut_sandstone:[[217,206,159]],red_sandstone:[[186,99,29]],clay:[[160,166,179]],
  dirt:[[134,96,67]],coarse_dirt:[[119,85,59]],rooted_dirt:[[144,103,76]],mud:[[60,57,60]],packed_mud:[[142,106,79]],mud_bricks:[[137,103,79]],farmland:[[143,102,70],'cube',1,[82,44,15]],
  grass_block:[[134,96,67],'cube',1,[98,150,66],[134,96,67]],podzol:[[122,87,58],'cube',1,[91,63,24]],mycelium:[[134,96,67],'cube',1,[111,99,105]],dirt_path:[[148,122,65],'slab15'],moss_block:[[89,109,45]],moss_carpet:[[89,109,45],'flat'],
  snow_block:[[249,254,254]],snow:[[249,254,254],'snow'],powder_snow:[[248,253,253]],ice:[[145,183,253],'glass',.75],packed_ice:[[141,180,250]],blue_ice:[[116,167,253]],
  water:[[52,96,186],'water',.72],bubble_column:[[52,96,186],'water',.72],lava:[[207,92,15],'water',1],
  glass:[[196,218,228],'glass',.42],tinted_glass:[[44,38,46],'glass',.85],glass_pane:[[196,218,228],'glass',.42],iron_bars:[[136,139,135],'glass',.5],
  bricks:[[150,97,83]],nether_bricks:[[44,21,26]],red_nether_bricks:[[69,7,9]],quartz_block:[[236,230,223]],smooth_quartz:[[236,230,223]],quartz_bricks:[[234,229,221]],quartz_pillar:[[235,230,224]],chiseled_quartz_block:[[231,226,218]],
  prismarine:[[99,156,151]],prismarine_bricks:[[99,171,158]],dark_prismarine:[[51,91,75]],sea_lantern:[[172,199,190]],glowstone:[[171,131,84]],shroomlight:[[240,146,70]],
  iron_block:[[220,220,220]],gold_block:[[246,208,61]],diamond_block:[[98,237,228]],emerald_block:[[42,203,87]],lapis_block:[[31,67,140]],redstone_block:[[175,24,5]],coal_block:[[16,15,15]],netherite_block:[[66,61,63]],
  copper_block:[[192,107,79]],cut_copper:[[191,106,80]],exposed_copper:[[161,125,103]],weathered_copper:[[108,153,110]],oxidized_copper:[[82,162,132]],
  obsidian:[[15,10,24]],crying_obsidian:[[32,10,60]],netherrack:[[97,38,38]],blackstone:[[42,36,41]],polished_blackstone:[[53,48,56]],polished_blackstone_bricks:[[48,42,49]],basalt:[[80,81,86]],smooth_basalt:[[72,72,78]],
  end_stone:[[219,222,158]],end_stone_bricks:[[218,224,162]],purpur_block:[[169,125,169]],purpur_pillar:[[171,129,171]],
  terracotta:[[152,94,68]],hay_block:[[166,139,12]],bookshelf:[[117,94,59]],sponge:[[195,192,74]],melon:[[111,145,30]],pumpkin:[[198,118,24]],bone_block:[[229,225,207]],
  oak_planks:[[162,130,78]],spruce_planks:[[115,85,49]],birch_planks:[[192,175,121]],jungle_planks:[[160,115,81]],acacia_planks:[[168,90,50]],dark_oak_planks:[[67,43,20]],mangrove_planks:[[117,54,48]],cherry_planks:[[226,178,172]],bamboo_planks:[[194,173,80]],crimson_planks:[[101,48,70]],warped_planks:[[43,104,99]],pale_oak_planks:[[228,217,213]],
  oak_log:[[109,85,50],'cube',1,[151,122,73]],spruce_log:[[58,37,16],'cube',1,[108,80,46]],birch_log:[[216,215,210],'cube',1,[193,179,135]],jungle_log:[[85,67,25],'cube',1,[149,109,70]],acacia_log:[[103,96,86],'cube',1,[150,88,55]],dark_oak_log:[[60,46,26],'cube',1,[64,42,21]],cherry_log:[[54,33,44],'cube',1,[185,141,137]],mangrove_log:[[84,66,41],'cube',1,[102,48,42]],
  short_grass:[[94,148,58],'cross'],tall_grass:[[94,148,58],'cross'],fern:[[88,140,56],'cross'],large_fern:[[88,140,56],'cross'],dead_bush:[[107,79,41],'cross'],seagrass:[[40,110,60],'skip'],tall_seagrass:[[40,110,60],'skip'],kelp:[[60,120,40],'skip'],kelp_plant:[[60,120,40],'skip'],
  lily_pad:[[32,128,48],'flat'],sugar_cane:[[148,192,101],'cross'],bamboo:[[93,144,19],'post'],vine:[[60,110,30],'skip'],
  torch:[[0,0,0],'skip'],wall_torch:[[0,0,0],'skip'],lantern:[[0,0,0],'skip'],light:[[0,0,0],'skip'],barrier:[[0,0,0],'skip'],structure_void:[[0,0,0],'skip'],cobweb:[[0,0,0],'skip'],tripwire:[[0,0,0],'skip'],ladder:[[0,0,0],'skip'],
  rail:[[125,110,90],'flat'],powered_rail:[[125,110,90],'flat'],detector_rail:[[125,110,90],'flat'],activator_rail:[[125,110,90],'flat'],
};
const SKIP_PATTERNS=/(^|_)(air|button|sign|banner|torch|pressure_plate|head|skull|candle|lever|redstone_wire|tripwire_hook|frame|pot$|flower_pot|coral(_fan)?$|vines?$|lichen|roots$|sprouts$|fungus$|door$)/;
const PLANTS=/(flower|tulip|poppy|dandelion|orchid|allium|bluet|daisy|cornflower|lily_of_the_valley|rose|peony|lilac|sunflower|sapling|bush|propagule|azalea$|wheat|carrots|potatoes|beetroots|sweet_berry|grass$|fern$|mushroom$|petals$|wildflowers|leaf_litter)/;
const STRIP=[[/_stairs$|_slab$|_wall$|_fence$|_fence_gate$/,''],[/^(.*)_brick$/,'$1_bricks'],[/^(.*)_tile$/,'$1_tiles']];
const cache=new Map();
function lookupBase(name){
  if(B[name])return B[name];
  for(const [pattern,replacement] of STRIP){const n=name.replace(pattern,replacement);if(n!==name){if(B[n])return B[n];if(B[n+'s'])return B[n+'s'];if(B[n+'_planks'])return B[n+'_planks'];if(B[n+'_block'])return B[n+'_block'];name=n;}}
  for(const suffix of ['_planks','_block','s'])if(B[name+suffix])return B[name+suffix];
  return null;
}
// Returns {rgb, top, bottom, alpha, kind, height, known}. Properties refine slabs, snow and fluids.
export function blockStyle(name,properties=null){
  const key=properties?name+JSON.stringify(properties):name;
  let style=cache.get(key);if(style)return style;
  style=resolve(String(name).replace(/^minecraft:/,''),properties||{});
  cache.set(key,style);return style;
}
function make(rgb,kind='cube',alpha=1,top=null,bottom=null,known=true){return {rgb,top:top||rgb,bottom:bottom||rgb,alpha,kind,known};}
function resolve(name,props){
  const dye=/^(white|orange|magenta|light_blue|yellow|lime|pink|gray|light_gray|cyan|purple|blue|brown|green|red|black)_(concrete_powder|concrete|glazed_terracotta|terracotta|wool|carpet|stained_glass_pane|stained_glass|bed|shulker_box|candle|banner)$/.exec(name);
  let style;
  if(dye){
    const [c,t,w,p,g]=DYES[dye[1]],type=dye[2];
    style=type==='concrete'?make(c):type==='concrete_powder'?make(p):type==='terracotta'||type==='glazed_terracotta'?make(t):type==='wool'||type==='shulker_box'?make(w):type==='carpet'?make(w,'flat'):type.startsWith('stained_glass')?make(g,'glass',dye[1]==='black'?.9:dye[1]==='gray'?.84:dye[1]==='light_gray'?.78:.7):type==='bed'?make(w,'slab9'):make(w,'skip');
  }else if(/_leaves$/.test(name))style=make(name.startsWith('spruce')?[54,88,54]:name.startsWith('birch')?[96,132,62]:name.startsWith('cherry')?[228,172,196]:name.startsWith('azalea')||name.startsWith('flowering')?[90,118,44]:[64,112,34],'leaves',1);
  else if(/^(infested_)?(.*_)?ore$/.test(name))style=make(name.includes('deepslate')?[80,80,82]:name.includes('nether')?[97,38,38]:[125,125,125]);
  else if(SKIP_PATTERNS.test(name)&&!/^(.*_)?(grass_block|mushroom_block)$/.test(name))style=make([0,0,0],'skip');
  else if(B[name])style=make(...B[name]);
  else if(PLANTS.test(name))style=make(/sunflower|dandelion|yellow/.test(name)?[220,190,40]:/poppy|rose|red/.test(name)?[190,40,35]:/orchid|cornflower|blue/.test(name)?[70,110,200]:/allium|lilac|peony|pink/.test(name)?[190,120,190]:/bluet|daisy|lily|white/.test(name)?[220,220,210]:[90,140,55],'cross');
  else{
    const base=lookupBase(name);
    if(base)style=make(base[0],base[1]==='glass'?'glass':'cube',base[2]??1,base[3],base[4]);
    else if(/glass/.test(name))style=make([200,222,230],'glass',.3);
    else if(/_log$|_wood$|_stem$|_hyphae$/.test(name))style=make([96,74,44]);
    else if(/planks|wood/.test(name))style=make([150,118,70]);
    else if(/copper/.test(name))style=make([170,120,90]);
    else if(/stone|brick|tile|deepslate|tuff/.test(name))style=make([125,125,125]);
    else style=make([168,152,136],'cube',1,null,null,false);
  }
  // Shape refinements from block-state properties.
  if(/_slab$/.test(name)&&style.kind!=='glass')style={...style,kind:props.type==='double'?'cube':props.type==='top'?'slabtop':'slab'};
  else if(/_stairs$/.test(name))style={...style,kind:'stairs',half:props.half==='top'?'top':'bottom',facing:['north','south','east','west'].includes(props.facing)?props.facing:'north'};
  else if(/_(fence|wall|fence_gate)$/.test(name))style={...style,kind:'post'};
  else if(/_pane$/.test(name)||name==='iron_bars')style={...style,kind:'glass'};
  if(name==='snow')style={...style,layers:Math.max(1,Math.min(8,Number(props.layers)||1))};
  if(style.kind==='water'&&props.level&&Number(props.level)>0&&Number(props.level)<8)style={...style,level:Number(props.level)};
  if(props.waterlogged==='true'&&style.kind==='skip')style={...make(...B.water),waterlogged:true};
  return style;
}
export const KNOWN_BLOCKS=Object.keys(B).map(n=>'minecraft:'+n);
