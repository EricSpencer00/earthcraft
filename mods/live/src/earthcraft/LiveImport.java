package earthcraft;

import com.google.gson.*;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.*;
import net.minecraft.server.MinecraftServer;
import java.io.*;
import java.nio.file.*;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.zip.GZIPInputStream;

/** Version-pinned to 1.21.10 intermediary mappings. No network or commands.
 * All block mutation is on the server tick, through World.setBlockState.
 * Interrupted chunks are quarantined, never blindly replayed over player edits.
 */
public final class LiveImport implements ModInitializer {
    static final Gson JSON = new GsonBuilder().setPrettyPrinting().create();
    static final String BASE = "stone,dirt,grass_block,sand,water,snow_block,gray_concrete,stone_bricks,bricks,sandstone,clay,bedrock,iron_block";
    static final Set<String> COLORS = Set.of("white","orange","magenta","light_blue","yellow","lime","pink","gray","light_gray","cyan","purple","blue","brown","green","red","black");
    static final Set<String> STAINED_GLASS = Set.of("white_stained_glass","orange_stained_glass","magenta_stained_glass","light_blue_stained_glass","yellow_stained_glass","lime_stained_glass","pink_stained_glass","gray_stained_glass","light_gray_stained_glass","cyan_stained_glass","purple_stained_glass","blue_stained_glass","brown_stained_glass","green_stained_glass","red_stained_glass","black_stained_glass");
    Path root, inbox, receipts;
    String frame;
    Set<String> protectedChunks = new HashSet<>();
    Map<String,Long> deferred = new ConcurrentHashMap<>();
    ExecutorService io;
    Future<Job> loading;
    Job active;
    int runIndex, runOffset, written, skipped, ticks;
    long maxBatchNanos;
    boolean enabled;
    static final class Job {
        Path path; JsonObject data; String id, key, mode;
        int cx, cz; int[][] runs; class_2680[] states; class_2680 pavement;
    }
    @Override public void onInitialize() {
        ServerLifecycleEvents.SERVER_STARTED.register(this::start);
        ServerLifecycleEvents.SERVER_STOPPING.register(s -> {
            // Finish a bounded active patch before Minecraft saves on normal quit.
            long deadline=System.nanoTime()+5_000_000_000L;
            while(enabled&&active!=null&&System.nanoTime()<deadline)tick(s);
            stop();
        });
        ServerTickEvents.END_SERVER_TICK.register(this::tick);
    }
    static void atomic(Path path, JsonObject value) throws IOException {
        Path temp = path.resolveSibling(path.getFileName()+".partial");
        Files.writeString(temp, JSON.toJson(value));
        try(FileChannel c=FileChannel.open(temp, StandardOpenOption.WRITE)){c.force(true);}
        Files.move(temp,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
    }
    void start(MinecraftServer server) {
        stop(); protectedChunks.clear(); deferred.clear(); ticks=0;
        Path config = FabricLoader.getInstance().getConfigDir().resolve("earthcraft-live.json");
        if(!Files.isRegularFile(config))return;
        try {
            JsonObject c=JsonParser.parseString(Files.readString(config)).getAsJsonObject();
            // MinecraftServer.getSavePath(WorldSavePath.ROOT): exact physical save binding.
            Path save=server.method_27050(class_5218.field_24188).toRealPath();
            if(!save.equals(Path.of(c.get("world").getAsString()).toRealPath()))return;
            frame=c.get("frame").getAsString();
            root=Path.of(c.get("exchange").getAsString()).toRealPath();
            inbox=root.resolve("inbox");receipts=root.resolve("receipts");
            Files.createDirectories(receipts);
            for(JsonElement p:c.getAsJsonArray("protected_chunks"))protectedChunks.add(p.getAsString());
            io=Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"earthcraft-live-io");t.setDaemon(true);return t;});
            enabled=true;
            System.out.println("[Earthcraft live] attached to "+save);
            status("ready",null);
        }catch(Exception e){fail(e);}
    }
    void stop(){enabled=false;active=null;loading=null;if(io!=null)io.shutdownNow();io=null;}
    void fail(Exception e){
        System.err.println("[Earthcraft live] stopped safely: "+e);
        try{status("error",e.toString());}catch(Exception ignored){}
        stop();
    }
    void status(String state,String error)throws IOException{
        if(root==null)return;
        JsonObject s=new JsonObject();s.addProperty("state",state);s.addProperty("time",java.time.Instant.now().toString());
        if(error!=null)s.addProperty("error",error);
        if(active!=null){s.addProperty("chunk",active.key);s.addProperty("written",written);}
        s.addProperty("budget_ms",3);s.addProperty("max_batch_ms",maxBatchNanos/1e6);
        atomic(root.resolve("status.json"),s);
    }
    static String sha(byte[] b)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(b));}
    Job next()throws Exception{
        List<Path> files;
        try(var stream=Files.list(inbox)){files=stream.filter(p->p.getFileName().toString().matches("[0-9a-f]{64}\\.json\\.gz")).sorted().toList();}
        if(files.size()>256)throw new IOException("Queue exceeds 256-file bound");
        // Oldest first: lexical content hashes would starve some older patches
        // when fresh chunks continuously enter the bounded queue.
        files=new ArrayList<>(files);
        files.sort(Comparator.comparingLong(p->p.toFile().lastModified()));
        for(Path p:files){
            String id=p.getFileName().toString().substring(0,64);
            if(Files.exists(receipts.resolve(id+".json")))continue;
            if(deferred.getOrDefault(id,0L)>System.currentTimeMillis())continue;
            if(Files.isSymbolicLink(p)||Files.size(p)>2_000_000)throw new IOException("Invalid patch file");
            byte[] raw=Files.readAllBytes(p);
            if(!sha(raw).equals(id))throw new IOException("Patch checksum mismatch");
            byte[] decoded;
            try(var gz=new GZIPInputStream(new ByteArrayInputStream(raw))){decoded=gz.readNBytes(8_000_001);}
            if(decoded.length>8_000_000)throw new IOException("Patch expansion exceeds bound");
            JsonObject d=JsonParser.parseString(new String(decoded,StandardCharsets.UTF_8)).getAsJsonObject();
            if(d.get("version").getAsInt()!=1||!d.get("frame").getAsString().equals(frame))throw new IOException("Wrong patch frame/version");
            Job j=new Job();j.path=p;j.id=id;j.data=d;j.cx=d.get("cx").getAsInt();j.cz=d.get("cz").getAsInt();
            if(Math.abs((long)j.cx)>10000||Math.abs((long)j.cz)>10000)throw new IOException("Outside Chicago importer envelope");
            j.key=j.cx+","+j.cz;j.mode=d.get("mode").getAsString();
            if(!Set.of("new_chunk","pavement").contains(j.mode))throw new IOException("Invalid mode");
            JsonArray palette=d.getAsJsonArray("palette"), runs=d.getAsJsonArray("runs");
            if(palette.size()>64||runs.size()>262144)throw new IOException("Patch count limit");
            // Registry access remains on the server thread, not this I/O thread.
            for(JsonElement name:palette){String n=name.getAsString();
                String local=n.replaceFirst("^minecraft:","");
                boolean concrete=local.endsWith("_concrete")&&COLORS.contains(local.substring(0,local.length()-9));
                if(!n.startsWith("minecraft:")||!(concrete||STAINED_GLASS.contains(local)||Arrays.asList(BASE.split(",")).contains(local)))throw new IOException("Unsupported block "+n);
                if(j.mode.equals("pavement")&&!concrete)throw new IOException("Pavement accepts only concrete");
            }
            j.runs=new int[runs.size()][3];int end=0,total=0;
            for(int k=0;k<runs.size();k++){
                JsonArray r=runs.get(k).getAsJsonArray();if(r.size()!=3)throw new IOException("Run shape");
                int a=r.get(0).getAsInt(),n=r.get(1).getAsInt(),v=r.get(2).getAsInt();
                if(a<end||n<1||a>262144-n||v<0||v>=palette.size())throw new IOException("Invalid/overlapping run");
                j.runs[k]=new int[]{a,n,v};end=a+n;total+=n;
            }
            if(total!=d.get("cells").getAsInt())throw new IOException("Cell count mismatch");
            return j;
        }
        return null;
    }
    boolean nearPlayer(class_3218 world,Job j){
        for(var player:world.method_18456()){
            if(Math.abs(player.method_23317()-(j.cx*16+8))<88&&Math.abs(player.method_23321()-(j.cz*16+8))<88)return true;
        }
        return false;
    }
    void receipt(Job j,String result)throws IOException{
        JsonObject r=new JsonObject();r.addProperty("patch",j.id);r.addProperty("chunk",j.key);r.addProperty("mode",j.mode);
        r.addProperty("result",result);r.addProperty("written",written);r.addProperty("skipped",skipped);
        r.addProperty("max_batch_ms",maxBatchNanos/1e6);r.addProperty("time",java.time.Instant.now().toString());
        r.addProperty("saved_and_reloaded_verified",false);
        atomic(receipts.resolve(j.id+".json"),r);
        System.out.println("[Earthcraft live] "+result+" "+j.key+" written="+written+" skipped="+skipped);
    }
    void begin(class_3218 world,Job j)throws Exception{
        written=skipped=runIndex=runOffset=0;maxBatchNanos=0;
        if(Files.exists(root.resolve("started-"+j.id+".json"))){receipt(j,"interrupted_requires_review");return;}
        if(j.mode.equals("new_chunk")&&protectedChunks.contains(j.key)){receipt(j,"protected_existing_chunk");return;}
        Path claim=root.resolve("claimed-"+j.key+".json");
        if(j.mode.equals("new_chunk")&&Files.exists(claim)){receipt(j,"previously_claimed_chunk_preserved");return;}
        var chunk=world.method_8497(j.cx,j.cz); // World.getChunk: load on server thread.
        if(j.mode.equals("new_chunk")){
            for(var section:chunk.method_12006())if(!section.method_38292()){receipt(j,"nonempty_chunk_preserved");return;}
            if(!chunk.method_12021().isEmpty()){receipt(j,"block_entities_preserved");return;}
        }
        var palette=j.data.getAsJsonArray("palette");j.states=new class_2680[palette.size()];
        for(int i=0;i<palette.size();i++)j.states[i]=class_7923.field_41175.method_17966(class_2960.method_60654(palette.get(i).getAsString())).orElseThrow().method_9564();
        j.pavement=class_7923.field_41175.method_63535(class_2960.method_60654("minecraft:gray_concrete")).method_9564();
        JsonObject intent=new JsonObject();intent.addProperty("chunk",j.key);intent.addProperty("mode",j.mode);
        intent.addProperty("recovery","Do not replay interrupted mutations over possible player edits");
        atomic(root.resolve("started-"+j.id+".json"),intent);
        if(j.mode.equals("new_chunk"))atomic(claim,intent);
        active=j;status("applying",null);
    }
    void tick(MinecraftServer server){
        if(!enabled)return;
        try{
            class_3218 world=server.method_30002();
            if(active==null){
                if(Files.exists(root.resolve("pause"))){if(++ticks%20==0)status("paused_by_operator",null);return;}
                if(loading!=null&&loading.isDone()){
                    Job j=loading.get();loading=null;
                    if(j!=null){
                        // Delay (not reject) while someone is close enough to edit new terrain.
                        if(j.mode.equals("new_chunk")&&nearPlayer(world,j)){deferred.put(j.id,System.currentTimeMillis()+5000);return;}
                        begin(world,j);
                    }
                }
                if(active==null&&loading==null&&++ticks%2==0)loading=io.submit(this::next);
                return;
            }
            Job j=active;
            if(j.mode.equals("new_chunk")&&nearPlayer(world,j)){receipt(j,"player_approached_partial_preserved");active=null;return;}
            long start=System.nanoTime();int count=0;
            while(runIndex<j.runs.length&&count<16384&&System.nanoTime()-start<3_000_000){
                int[] r=j.runs[runIndex];int index=r[0]+runOffset;
                class_2338 pos=new class_2338(j.cx*16+(index&15),(index>>8)-64,j.cz*16+((index>>4)&15));
                var old=world.method_8320(pos);var target=j.states[r[2]];
                boolean allowed=j.mode.equals("new_chunk")?old.method_26215():old==j.pavement;
                if(allowed&&old!=target){
                    // Notify clients; force state, skip drops, no recursive neighbour physics.
                    if(!world.method_8652(pos,target,50)||world.method_8320(pos)!=target)throw new IOException("Block mutation/readback failed");
                    written++;
                }else skipped++;
                count++;runOffset++;if(runOffset==r[1]){runIndex++;runOffset=0;}
            }
            maxBatchNanos=Math.max(maxBatchNanos,System.nanoTime()-start);
            if(runIndex==j.runs.length){receipt(j,"applied_in_memory");active=null;status("ready",null);}
        }catch(Exception e){fail(e);}
    }
}
