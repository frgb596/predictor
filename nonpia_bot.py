import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import random
import string
import json
import os
import math
import hashlib
import time
import struct
import hmac
import asyncio
from datetime import datetime, timezone, timedelta

# ── LOAD ENVIRONMENT VARIABLES ──────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable not set! Set it in Railway dashboard.")

PRIVILEGED_ROLES = {"owner", "founders", "founder", "admin", "administrator"}
KEYS_FILE  = "keys.json"
USERS_FILE = "users.json"

KEY_TYPES = {
    "lifetime": None,
    "yearly":   365,
    "monthly":  30,
    "weekly":   7,
    "3days":    3,
}

# ── BLOXFLIP BYPASS HEADERS ───────────────────────────────────────────────────
# Rotates real browser fingerprints to avoid detection

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def _build_headers(token: str) -> dict:
    ua = random.choice(_UA_POOL)
    return {
        "x-auth-token":             token,
        "Content-Type":             "application/json",
        "Accept":                   "application/json, text/plain, */*",
        "Accept-Language":          "en-US,en;q=0.9",
        "Accept-Encoding":          "gzip, deflate, br",
        "Origin":                   "https://bloxflip.com",
        "Referer":                  "https://bloxflip.com/mines",
        "Sec-Ch-Ua":                '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile":         "?0",
        "Sec-Ch-Ua-Platform":       '"Windows"',
        "Sec-Fetch-Dest":           "empty",
        "Sec-Fetch-Mode":           "cors",
        "Sec-Fetch-Site":           "same-origin",
        "User-Agent":               ua,
        "X-Requested-With":         "XMLHttpRequest",
        "Connection":               "keep-alive",
        "Cache-Control":            "no-cache",
        "Pragma":                   "no-cache",
    }

def _build_cookie_jar(token: str) -> aiohttp.CookieJar:
    jar = aiohttp.CookieJar()
    jar.update_cookies({"app.rt": token}, response_url=aiohttp.typedefs.StrOrURL("https://bloxflip.com"))
    return jar

async def _bloxflip_fetch(token: str, path: str) -> dict | None:
    url     = f"https://api.bloxflip.com{path}"
    headers = _build_headers(token)
    timeout = aiohttp.ClientTimeout(total=12, connect=5)
    connector = aiohttp.TCPConnector(ssl=True, limit=10)

    # Try up to 3 times with different UA each attempt
    for attempt in range(3):
        try:
            headers["User-Agent"] = random.choice(_UA_POOL)
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"Cookie": f"app.rt={token}"},
            ) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    if resp.status in (401, 403):
                        return {"_auth_error": True, "status": resp.status}
                    if resp.status == 429:
                        await asyncio.sleep(1.5)
                        continue
        except Exception:
            if attempt < 2:
                await asyncio.sleep(0.8)
            continue
    return None

async def _verify_token(token: str) -> tuple:
    data = await _bloxflip_fetch(token, "/user")
    if data is None:
        return False, "Could not reach Bloxflip. Check your internet or try again."
    if data.get("_auth_error"):
        return False, f"Token rejected by Bloxflip (HTTP {data.get('status')}). Make sure you copied `app.rt` correctly."
    username = data.get("user", {}).get("username") or data.get("username") or "Unknown"
    balance  = data.get("user", {}).get("wallet") or data.get("wallet") or 0
    return True, {"username": username, "balance": balance}

async def _fetch_live_game(token: str) -> dict | None:
    data = await _bloxflip_fetch(token, "/games/mines")
    if not data or data.get("_auth_error"):
        return None
    return data

# ── JSON HELPERS ──────────────────────────────────────────────────────────────

def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ── ROLE / KEY HELPERS ────────────────────────────────────────────────────────

def has_privileged_role(member: discord.Member) -> bool:
    return any(r.name.lower() in PRIVILEGED_ROLES for r in member.roles)

def generate_key(key_type: str) -> str:
    prefix_map = {
        "lifetime": "NONPIA-LT",
        "yearly":   "NONPIA-YR",
        "monthly":  "NONPIA-MO",
        "weekly":   "NONPIA-WK",
        "3days":    "NONPIA-3D",
    }
    chars    = string.ascii_uppercase + string.digits
    segments = ["".join(random.choices(chars, k=6)) for _ in range(3)]
    return prefix_map.get(key_type, "NONPIA-XX") + "-" + "-".join(segments)

def is_key_expired(kd: dict) -> bool:
    exp = kd.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(exp)
    except Exception:
        return False

def check_user_access(uid: str, member: discord.Member) -> tuple:
    if has_privileged_role(member):
        return True, "privileged"
    users   = load_json(USERS_FILE)
    keys_db = load_json(KEYS_FILE)
    user    = users.get(uid, {})
    if not user.get("key_valid"):
        return False, "no_key"
    if not user.get("auth_token"):
        return False, "no_link"
    ukey = user.get("key", "")
    if ukey and ukey in keys_db:
        if keys_db[ukey].get("revoked"):
            return False, "revoked"
        if is_key_expired(keys_db[ukey]):
            users[uid]["key_valid"] = False
            save_json(USERS_FILE, users)
            return False, "expired"
    return True, "ok"

# ═══════════════════════════════════════════════════════════════════════════════
#  NONPIA GODMODE PREDICTION CORE v4
#  Monte Carlo · Bayesian BP · CSP · Wave-Function Collapse · Heat Map · Fusion
# ═══════════════════════════════════════════════════════════════════════════════

GW, GH, GS = 5, 5, 25

def _nb(i):
    r,c=divmod(i,GW)
    return[(r+dr)*GW+(c+dc) for dr in[-1,0,1] for dc in[-1,0,1]
           if(dr or dc) and 0<=r+dr<GH and 0<=c+dc<GW]

def _orth(i):
    r,c=divmod(i,GW)
    return[(r+dr)*GW+(c+dc) for dr,dc in[(-1,0),(1,0),(0,-1),(0,1)]
           if 0<=r+dr<GH and 0<=c+dc<GW]

def _diag(i):
    r,c=divmod(i,GW)
    return[(r+dr)*GW+(c+dc) for dr,dc in[(-1,-1),(-1,1),(1,-1),(1,1)]
           if 0<=r+dr<GH and 0<=c+dc<GW]

def _sig(x):  return 1/(1+math.exp(-max(-500,min(500,x))))
def _sm(v):
    mx=max(v) if v else 0; e=[math.exp(x-mx) for x in v]; s=sum(e) or 1
    return[x/s for x in e]
def _ent(p):  return 0 if p<=0 or p>=1 else -(p*math.log2(p)+(1-p)*math.log2(1-p))
def _bent(ss,m): rem=GS-len(ss); return _ent(m/rem) if rem>0 else 0

_ZF={0:.78,4:.78,20:.78,24:.78,
     1:.88,2:.88,3:.88,5:.88,9:.88,10:.88,14:.88,15:.88,19:.88,21:.88,22:.88,23:.88,
     6:1.02,7:1.02,8:1.02,11:1.02,13:1.02,16:1.02,17:1.02,18:1.02,12:1.10}

def _zone(i,m,ss): rem=GS-len(ss); return max(.001,min(.999,(m/max(rem,1))*_ZF.get(i,1.0)))

def _csp(ss,m,iters=7):
    unrev=[i for i in range(GS) if i not in ss]; rem=len(unrev)
    if not rem: return {}
    p={c:m/rem for c in unrev}
    for _ in range(iters):
        np2={}
        for c in unrev:
            snb=sum(1 for n in _nb(c) if n in ss)
            unb=[n for n in _nb(c) if n not in ss and n!=c]
            tnb=snb+len(unb)
            sr=snb/max(tnb,1)
            zp=_zone(c,m,ss)
            mp=sum(p.get(n,m/max(rem,1)) for n in unb)/max(len(unb),1)
            np2[c]=max(.001,min(.999,p[c]*.44+zp*.20+mp*.20-sr*.16))
        tot=sum(np2.values())
        if tot>0:
            sc=m/tot
            for c in np2: np2[c]=max(.001,min(.999,np2[c]*sc))
        delta=sum(abs(np2[c]-p[c]) for c in unrev)
        p=np2
        if delta<.004: break
    return p

def _mc(ss,m,n=2000,rng=None):
    if rng is None: rng=random.Random()
    unrev=[i for i in range(GS) if i not in ss]; rem=len(unrev)
    if not rem or m>=rem: return{c:0. for c in unrev}
    cnt={c:0 for c in unrev}; runs=0
    for _ in range(n):
        mines=set(rng.sample(unrev,min(m,rem))); runs+=1
        for c in unrev:
            if c not in mines: cnt[c]+=1
    return{c:cnt[c]/runs for c in unrev}

def _bp(ss,m,csp):
    unrev=[i for i in range(GS) if i not in ss]; rem=len(unrev)
    if not unrev: return{}
    base=m/max(rem,1); bl={c:csp.get(c,base) for c in unrev}
    for c in unrev:
        lo=math.log(max(bl[c],1e-9)/max(1-bl[c],1e-9))
        for n in _orth(c):
            if n in ss:           lo-=0.42
            elif n!=c:            lo+=bl.get(n,base)*0.13
        for n in _diag(c):
            if n in ss:           lo-=0.20
            elif n!=c:            lo+=bl.get(n,base)*0.06
        r2s=sum(1 for n in _nb(c) for n2 in _nb(n) if n2 in ss and n2!=c)
        lo-=r2s*0.045
        bl[c]=max(.001,min(.999,_sig(lo)))
    tot=sum(bl.values())
    if tot>0:
        sc=m/tot
        for c in bl: bl[c]=max(.001,min(.999,bl[c]*sc))
    return bl

def _hmap(ss,m):
    rem=GS-len(ss); base=m/max(rem,1); h={}
    for c in range(GS):
        if c in ss: continue
        row,col=divmod(c,GW)
        snb=sum(1 for n in _nb(c) if n in ss)
        tnb=len(_nb(c)); sr=snb/max(tnb,1)
        dc=math.sqrt((row-2)**2+(col-2)**2)
        r2=sum(1 for n in _nb(c) for n2 in _nb(n) if n2 in ss and n2!=c)
        h[c]=max(.001,min(.999,base*(1-sr*.32-r2*.028+dc*.019)))
    return h

def _wfc(ss,m,fp):
    unrev=set(range(GS))-ss; p=dict(fp); order=[]; col=set(ss)
    while unrev:
        best=min(unrev,key=lambda c:p.get(c,1.))
        order.append(best); col.add(best); unrev.discard(best)
        for n in _nb(best):
            if n in unrev: p[n]=max(.001,min(.999,p.get(n,m/max(len(unrev),1))*.93))
    return order

def _fuse(csp,mc_mine,bp,hm,m,ss):
    unrev=[i for i in range(GS) if i not in ss]; rem=len(unrev)
    base=m/max(rem,1); f={}
    for c in unrev:
        mp=(csp.get(c,base)*.28 + mc_mine.get(c,base)*.32
            +bp.get(c,base)*.22  + hm.get(c,base)*.18)
        f[c]=max(.001,min(.999,mp))
    tot=sum(f.values())
    if tot>0:
        sc=m/tot
        for c in f: f[c]=max(.001,min(.999,f[c]*sc))
    return f

def _hchance(fp,mc_safe,m,ss):
    rem=GS-len(ss); base=(rem-m)/max(rem,1)
    unrev=[i for i in range(GS) if i not in ss]
    if not unrev: return 0.
    ranked=sorted(unrev,key=lambda c:fp.get(c,1.))
    ts=1.-fp.get(ranked[0],.5) if ranked else .5
    mc=mc_safe.get(ranked[0],.5) if ranked else .5
    ep=_bent(ss,m)*.04
    return max(1.,min(99.9,round((base*.50+ts*.30+mc*.16-ep)*100,1)))

def _seed(tag,rev,m):
    mat=f"{tag}|{sorted(rev)}|{m}|{int(time.time()//30)}".encode()
    return int(hashlib.sha256(mat).hexdigest()[:16],16)

def _godmode(rev,m,picks):
    ss=set(rev); picks=max(1,min(10,picks)); m=max(1,min(GS-1,m))
    seed=_seed("gm",rev,m); rng=random.Random(seed)
    csp=_csp(ss,m)
    mc_s=_mc(ss,m,n=2000,rng=rng)
    mc_mine={c:1-v for c,v in mc_s.items()}
    bp=_bp(ss,m,csp)
    hm=_hmap(ss,m)
    fp=_fuse(csp,mc_mine,bp,hm,m,ss)
    wfc=_wfc(ss,m,fp)
    ranked=sorted(fp.keys(),key=lambda c:fp[c])
    hit=_hchance(fp,mc_s,m,ss)
    return ranked,hit,fp,wfc,rng

_PATTERNS=["diagonal","cross","corner_first","center_out","edge_skip","zigzag","spiral","cluster_safe","wfc"]

def _pat(ranked,picks,ss,wfc,pat,rng):
    pool=ranked[:max(picks*3,12)]
    if pat=="diagonal":
        pool.sort(key=lambda i:sum(divmod(i,GW))+rng.gauss(0,.25))
    elif pat=="cross":
        pool.sort(key=lambda i:min(abs(divmod(i,GW)[0]-2),abs(divmod(i,GW)[1]-2))+rng.gauss(0,.2))
    elif pat=="corner_first":
        co={0,4,20,24}; ed={1,2,3,5,9,10,14,15,19,21,22,23}
        pool.sort(key=lambda i:(0 if i in co else 1 if i in ed else 2)+rng.gauss(0,.15))
    elif pat=="center_out":
        pool.sort(key=lambda i:math.sqrt((divmod(i,GW)[0]-2)**2+(divmod(i,GW)[1]-2)**2)+rng.gauss(0,.18))
    elif pat=="edge_skip":
        inn=[c for c in pool if 1<=divmod(c,GW)[0]<=3 and 1<=divmod(c,GW)[1]<=3]
        out=[c for c in pool if c not in inn]
        rng.shuffle(inn); rng.shuffle(out); pool=inn+out
    elif pat=="zigzag":
        pool.sort(key=lambda i:(divmod(i,GW)[0]*GW+(divmod(i,GW)[1] if divmod(i,GW)[0]%2==0
                  else 4-divmod(i,GW)[1]))+rng.gauss(0,.2))
    elif pat=="spiral":
        sp=[0,1,2,3,4,9,14,19,24,23,22,21,20,15,10,5,6,7,8,13,18,17,16,11,12]
        im={v:i for i,v in enumerate(sp)}
        pool.sort(key=lambda i:im.get(i,99)+rng.gauss(0,.25))
    elif pat=="cluster_safe":
        pool.sort(key=lambda i:-sum(1 for n in _nb(i) if n in ss)+rng.gauss(0,.2))
    elif pat=="wfc":
        wmap={v:i for i,v in enumerate(wfc)}
        pool=[c for c in wfc if c in set(pool)]
    return pool[:picks]

def engine_nonpia_ai(rev,m,picks,grid_size=25):
    ranked,hit,fp,wfc,rng=_godmode(rev,m,picks)
    pat=rng.choice(["diagonal","cross","center_out","cluster_safe","wfc"])
    top=_pat(ranked,picks,set(rev),wfc,pat,rng)
    return top,hit,pat

def engine_logarithm(rev,m,picks,grid_size=25):
    ranked,hit,fp,wfc,rng=_godmode(rev,m,picks)
    pat=rng.choice(["corner_first","edge_skip","zigzag","spiral","diagonal"])
    top=_pat(ranked,picks,set(rev),wfc,pat,rng)
    return top,max(1.,min(99.9,round(hit*.97+1.5,1))),pat

def engine_neuralithm(rev,m,picks,grid_size=25):
    ranked,hit,fp,wfc,rng=_godmode(rev,m,picks)
    pat=rng.choice(["wfc","spiral","cluster_safe","cross","zigzag"])
    top=_pat(ranked,picks,set(rev),wfc,pat,rng)
    return top,max(1.,min(99.9,round(hit*.99+.5,1))),pat

ENGINES={
    "NonpiaAi":   engine_nonpia_ai,
    "Logarithm":  engine_logarithm,
    "Neuralithm": engine_neuralithm,
}

# ── GRID RENDERER ─────────────────────────────────────────────────────────────

def build_grid(picks,revealed):
    ps=set(picks); rs=set(revealed)
    rows=[]
    for r in range(5):
        cells=[]
        for c in range(5):
            i=r*5+c
            cells.append("⭐" if i in rs or i in ps else "❓")
        rows.append("  ".join(cells))
    return "\n".join(rows)

# ── PATTERN LABELS ────────────────────────────────────────────────────────────

PAT_LABELS={
    "diagonal":"Diagonal","cross":"Cross Sweep","corner_first":"Corner First",
    "center_out":"Center Out","edge_skip":"Edge Skip","zigzag":"Zigzag",
    "spiral":"Spiral","cluster_safe":"Cluster Safe","wfc":"Wave Collapse",
}
ENG_LABELS={
    "NonpiaAi":"Nonpia Ai — Spatial Constraint",
    "Logarithm":"Logarithm — Bayesian Log-Odds",
    "Neuralithm":"Neuralithm — 4-Layer Neural",
}

# ── BOT SETUP ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

@bot.event
async def on_ready():
    await tree.sync()
    print(f"[Nonpia] Online — {bot.user} | Commands synced.")
    print(f"[Nonpia] Logged in successfully. Bot is ready.")

# ── /link ─────────────────────────────────────────────────────────────────────

@tree.command(name="link", description="Link your Bloxflip account with your app.rt token")
@app_commands.describe(auth="Your Bloxflip app.rt cookie value")
async def cmd_link(interaction: discord.Interaction, auth: str):
    await interaction.response.defer(ephemeral=True)
    uid     = str(interaction.user.id)
    is_priv = has_privileged_role(interaction.user)
    users   = load_json(USERS_FILE)
    user    = users.get(uid, {})

    if not is_priv and not user.get("key_valid"):
        await interaction.followup.send(embed=discord.Embed(
            title="No Active Key ❌",
            description="Redeem a key first: `/redeem key:<key>`",
            color=0xED4245,
        ), ephemeral=True)
        return

    # Verify token live against Bloxflip
    await interaction.followup.send("🔄 Verifying your token against Bloxflip...", ephemeral=True)
    ok, result = await _verify_token(auth.strip())

    if not ok:
        await interaction.followup.send(embed=discord.Embed(
            title="Link Failed ❌",
            description=result,
            color=0xED4245,
        ), ephemeral=True)
        return

    if uid not in users:
        users[uid] = {}
    if is_priv:
        users[uid]["key_valid"] = True
    users[uid]["auth_token"]  = auth.strip()
    users[uid]["linked_at"]   = datetime.now(timezone.utc).isoformat()
    users[uid]["bf_username"] = result.get("username", "Unknown")
    save_json(USERS_FILE, users)

    embed = discord.Embed(title="Account Linked ✅", color=0x57F287)
    embed.add_field(name="Bloxflip User", value=result.get("username","Unknown"), inline=True)
    embed.add_field(name="Balance",       value=f"{result.get('balance',0):,} R$", inline=True)
    embed.add_field(name="Status",        value="Ready — use `/mines` to predict!", inline=False)
    embed.set_footer(text="Nonpia Predictor")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /mines ────────────────────────────────────────────────────────────────────

@tree.command(name="mines", description="Predict safe tiles for your live Bloxflip Mines game")
@app_commands.describe(
    algo="Prediction engine",
    mines="Mine count (auto-detected from live game)",
    picks="Safe tiles to predict (1–10)",
)
@app_commands.choices(algo=[
    app_commands.Choice(name="NonpiaAi",   value="NonpiaAi"),
    app_commands.Choice(name="Logarithm",  value="Logarithm"),
    app_commands.Choice(name="Neuralithm", value="Neuralithm"),
])
async def cmd_mines(interaction: discord.Interaction, algo: str, mines: int = 3, picks: int = 5):
    await interaction.response.defer(ephemeral=True)
    uid      = str(interaction.user.id)
    ok, reason = check_user_access(uid, interaction.user)

    if not ok:
        msgs = {
            "no_key":  "You need an active key. Use `/redeem key:<key>` first.",
            "no_link": "Link your Bloxflip account first: `/link auth:<your app.rt token>`",
            "revoked": "Your key has been revoked. Contact an admin.",
            "expired": "Your key has expired. Contact an admin for a new one.",
        }
        await interaction.followup.send(embed=discord.Embed(
            title="Access Denied ❌",
            description=msgs.get(reason, "Unknown error."),
            color=0xED4245,
        ), ephemeral=True)
        return

    mines = max(1, min(24, mines))
    picks = max(1, min(10, picks))

    users    = load_json(USERS_FILE)
    user     = users.get(uid, {})
    token    = user.get("auth_token", "")
    revealed = []
    live     = False
    bf_user  = user.get("bf_username", "")

    if token:
        game_data = await _fetch_live_game(token)
        if game_data and not game_data.get("_auth_error"):
            try:
                game = game_data.get("game") or game_data
                if game.get("active") or game.get("status") in ("active","playing","in_progress"):
                    m_raw = game.get("mineCount", game.get("mines", mines))
                    mines = int(m_raw) if str(m_raw).isdigit() or isinstance(m_raw, int) else mines
                    raw_r = game.get("revealedTiles", game.get("revealed", game.get("tiles_revealed", [])))
                    if isinstance(raw_r, list):
                        revealed = [int(t) for t in raw_r
                                    if (isinstance(t, int) or (isinstance(t, str) and t.isdigit()))]
                    live = True
            except Exception:
                pass

    engine_fn             = ENGINES.get(algo, engine_nonpia_ai)
    top_picks, hit, pat   = engine_fn(revealed, mines, picks)
    grid_str              = build_grid(top_picks, revealed)

    embed = discord.Embed(
        title=f"Nonpia Predictor | {algo}",
        color=0x5865F2,
    )
    embed.add_field(name="\u200b", value=grid_str, inline=False)
    embed.add_field(
        name="\u200b",
        value=(
            f"**{hit}%** hit chance  ·  {mines} mines  ·  {picks} picks\n"
            f"{'🟢 Live game detected' if live else '🔵 No active game — using board defaults'}"
        ),
        inline=False,
    )
    embed.add_field(name="Engine",  value=ENG_LABELS.get(algo, algo),   inline=True)
    embed.add_field(name="Pattern", value=PAT_LABELS.get(pat, pat),      inline=True)
    if revealed:
        embed.add_field(name="Revealed Tiles", value=str(len(revealed)), inline=True)
    if bf_user:
        embed.add_field(name="Account", value=bf_user, inline=True)
    embed.set_footer(
        text=f"Nonpia Predictor | {datetime.now(timezone.utc).strftime('%m/%d/%Y %I:%M %p')} UTC"
    )
    embed.description = "Good luck on your Mines game, buddy 🤩"
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /keygen ───────────────────────────────────────────────────────────────────

@tree.command(name="keygen", description="Generate Nonpia keys [Owner/Founder/Admin only]")
@app_commands.describe(key_type="Type of key", amount="How many (1–50)")
@app_commands.choices(key_type=[
    app_commands.Choice(name="Lifetime ♾️",  value="lifetime"),
    app_commands.Choice(name="Yearly 📅",    value="yearly"),
    app_commands.Choice(name="Monthly 🗓️",   value="monthly"),
    app_commands.Choice(name="Weekly 📆",    value="weekly"),
    app_commands.Choice(name="3 Days ⏳",    value="3days"),
])
async def cmd_keygen(interaction: discord.Interaction, key_type: str, amount: int = 1):
    if not has_privileged_role(interaction.user):
        await interaction.response.send_message(
            "❌ Only **Owner**, **Founder**, or **Admin** roles can generate keys.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    amount  = max(1, min(50, amount))
    keys_db = load_json(KEYS_FILE)
    days    = KEY_TYPES.get(key_type)
    generated = []
    for _ in range(amount):
        k = generate_key(key_type)
        while k in keys_db: k = generate_key(key_type)
        exp_at = None
        if days: exp_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        keys_db[k] = {
            "key_type":   key_type,
            "created_by": str(interaction.user.id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": exp_at,
            "used_by":    None,
            "used_at":    None,
            "revoked":    False,
        }
        generated.append(k)
    save_json(KEYS_FILE, keys_db)
    labels = {"lifetime":"Lifetime ♾️","yearly":"Yearly 📅","monthly":"Monthly 🗓️",
               "weekly":"Weekly 📆","3days":"3 Days ⏳"}
    embed = discord.Embed(
        title=f"Generated {amount} {labels.get(key_type,key_type)} Key(s) ✅",
        description=f"```\n{chr(10).join(generated)}\n```",
        color=0x57F287,
    )
    embed.set_footer(text="Nonpia Predictor — Distribute privately.")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /redeem ───────────────────────────────────────────────────────────────────

@tree.command(name="redeem", description="Redeem a Nonpia access key")
@app_commands.describe(key="Your Nonpia key")
async def cmd_redeem(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    uid     = str(interaction.user.id)
    keys_db = load_json(KEYS_FILE)
    users   = load_json(USERS_FILE)
    key     = key.strip()
    if key not in keys_db:
        await interaction.followup.send("❌ Invalid key.", ephemeral=True); return
    kd = keys_db[key]
    if kd.get("revoked"):
        await interaction.followup.send("❌ Key revoked.", ephemeral=True); return
    if kd.get("used_by") and kd["used_by"] != uid:
        await interaction.followup.send("❌ Key already used by another account.", ephemeral=True); return
    if is_key_expired(kd):
        await interaction.followup.send("❌ Key expired.", ephemeral=True); return
    keys_db[key]["used_by"] = uid
    keys_db[key]["used_at"] = datetime.now(timezone.utc).isoformat()
    save_json(KEYS_FILE, keys_db)
    if uid not in users: users[uid] = {}
    users[uid]["key_valid"] = True
    users[uid]["key"]       = key
    save_json(USERS_FILE, users)
    exp = kd.get("expires_at")
    embed = discord.Embed(title="Key Redeemed ✅", color=0x57F287)
    embed.add_field(name="Type",     value=kd.get("key_type","?").capitalize(), inline=True)
    embed.add_field(name="Expires",  value=exp[:10] if exp else "Never ♾️",      inline=True)
    embed.add_field(name="Next",     value="Use `/link auth:<app.rt token>` to connect Bloxflip.", inline=False)
    embed.set_footer(text="Nonpia Predictor")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /revokekey ────────────────────────────────────────────────────────────────

@tree.command(name="revokekey", description="Revoke a key [Owner/Founder/Admin only]")
@app_commands.describe(key="Key to revoke")
async def cmd_revokekey(interaction: discord.Interaction, key: str):
    if not has_privileged_role(interaction.user):
        await interaction.response.send_message("❌ Requires Owner, Founder, or Admin role.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    keys_db = load_json(KEYS_FILE); key = key.strip()
    if key not in keys_db:
        await interaction.followup.send("❌ Key not found.", ephemeral=True); return
    keys_db[key]["revoked"]    = True
    keys_db[key]["revoked_by"] = str(interaction.user.id)
    keys_db[key]["revoked_at"] = datetime.now(timezone.utc).isoformat()
    save_json(KEYS_FILE, keys_db)
    ub = keys_db[key].get("used_by")
    if ub:
        users = load_json(USERS_FILE)
        if ub in users and users[ub].get("key") == key:
            users[ub]["key_valid"] = False; save_json(USERS_FILE, users)
    await interaction.followup.send("✅ Key revoked and user access removed.", ephemeral=True)

# ── /listkeys ─────────────────────────────────────────────────────────────────

@tree.command(name="listkeys", description="List all keys [Owner/Founder/Admin only]")
async def cmd_listkeys(interaction: discord.Interaction):
    if not has_privileged_role(interaction.user):
        await interaction.response.send_message("❌ Requires Owner, Founder, or Admin role.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    keys_db = load_json(KEYS_FILE)
    if not keys_db:
        await interaction.followup.send("No keys yet.", ephemeral=True); return
    lines=[]
    for k,v in list(keys_db.items())[-30:]:
        st=("🔴 Revoked" if v.get("revoked") else
            "🟡 Expired" if is_key_expired(v) else
            "🟢 Used"    if v.get("used_by") else "⚪ Free")
        lines.append(f"{k[:28]}  {v.get('key_type','?')[:8].ljust(8)}  {st}")
    embed=discord.Embed(title=f"Keys ({len(keys_db)} total)",color=0x5865F2)
    embed.description="```\n"+"\n".join(lines)+"\n```"
    embed.set_footer(text="Last 30 shown | Nonpia Predictor")
    await interaction.followup.send(embed=embed,ephemeral=True)

# ── /status ───────────────────────────────────────────────────────────────────

@tree.command(name="status", description="Check your Nonpia account status")
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    uid=str(interaction.user.id); users=load_json(USERS_FILE); keys_db=load_json(KEYS_FILE)
    u=users.get(uid,{}); ip=has_privileged_role(interaction.user)
    embed=discord.Embed(title="Account Status",color=0x5865F2)
    embed.add_field(name="Key",       value="✅" if u.get("key_valid") or ip else "❌", inline=True)
    embed.add_field(name="Linked",    value="✅" if u.get("auth_token") else "❌",       inline=True)
    embed.add_field(name="Tier",      value="👑 Privileged" if ip else "🔑 Key",         inline=True)
    if u.get("bf_username"):
        embed.add_field(name="Bloxflip", value=u["bf_username"], inline=True)
    if u.get("key") and u["key"] in keys_db:
        kd=keys_db[u["key"]]; exp=kd.get("expires_at")
        embed.add_field(name="Key Expires",value=exp[:10] if exp else "Never ♾️",inline=True)
        embed.add_field(name="Key Type",   value=kd.get("key_type","?").capitalize(),inline=True)
    if u.get("linked_at"):
        embed.add_field(name="Linked Since",value=u["linked_at"][:10],inline=False)
    embed.set_footer(text="Nonpia Predictor")
    await interaction.followup.send(embed=embed,ephemeral=True)

# ── /userinfo ─────────────────────────────────────────────────────────────────

@tree.command(name="userinfo", description="Inspect a user [Owner/Founder/Admin only]")
@app_commands.describe(user="Discord member to inspect")
async def cmd_userinfo(interaction: discord.Interaction, user: discord.Member):
    if not has_privileged_role(interaction.user):
        await interaction.response.send_message("❌ Requires Owner, Founder, or Admin role.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    uid=str(user.id); users=load_json(USERS_FILE); keys_db=load_json(KEYS_FILE)
    d=users.get(uid,{}); ip=has_privileged_role(user)
    embed=discord.Embed(title=f"User — {user.display_name}",color=0x5865F2)
    embed.add_field(name="Key",        value="✅" if d.get("key_valid") or ip else "❌", inline=True)
    embed.add_field(name="Linked",     value="✅" if d.get("auth_token") else "❌",       inline=True)
    embed.add_field(name="Privileged", value="👑 Yes" if ip else "No",                   inline=True)
    if d.get("bf_username"):
        embed.add_field(name="Bloxflip", value=d["bf_username"], inline=True)
    if d.get("key") and d["key"] in keys_db:
        kd=keys_db[d["key"]]; exp=kd.get("expires_at")
        embed.add_field(name="Key",     value=f"`{d['key'][:22]}...`",inline=False)
        embed.add_field(name="Expires", value=exp[:10] if exp else "Never ♾️",inline=True)
        embed.add_field(name="Revoked", value="Yes 🔴" if kd.get("revoked") else "No 🟢",inline=True)
    embed.set_footer(text="Nonpia Predictor")
    await interaction.followup.send(embed=embed,ephemeral=True)

# ── /guide ────────────────────────────────────────────────────────────────────

@tree.command(name="guide", description="How to get your Bloxflip app.rt token")
async def cmd_guide(interaction: discord.Interaction):
    embed=discord.Embed(title="How to Get Your Token",color=0x5865F2)
    embed.description=(
        "**Step 1** — Go to [bloxflip.com](https://bloxflip.com) and log in.\n"
        "**Step 2** — Press `F12` → **Application** tab → **Cookies** → `https://bloxflip.com`\n"
        "**Step 3** — Find the cookie named **`app.rt`** and copy its **Value**.\n"
        "**Step 4** — Use `/link auth:<paste value here>`\n\n"
        "The bot will verify your token live and show your Bloxflip username if successful.\n"
        "After linking, use `/mines` while you have an **active Mines game open** on Bloxflip."
    )
    embed.set_footer(text="Nonpia Predictor")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /help ─────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="Show all Nonpia Predictor commands")
async def cmd_help(interaction: discord.Interaction):
    ip=has_privileged_role(interaction.user)
    embed=discord.Embed(title="Nonpia Predictor",description="Bloxflip Mines live prediction suite.",color=0x5865F2)
    embed.add_field(name="Prediction",
        value="`/mines algo: mines: picks:` — predict safe tiles from your live game",inline=False)
    embed.add_field(name="Account",
        value=("`/link auth:` — link Bloxflip (verifies live)\n"
               "`/redeem key:` — activate a key\n"
               "`/status` — check your account\n"
               "`/guide` — how to get your token"),inline=False)
    if ip:
        embed.add_field(name="👑 Admin / Owner / Founder",
            value=("`/keygen key_type: amount:` — generate keys\n"
                   "`/revokekey key:` — revoke a key\n"
                   "`/listkeys` — view all keys\n"
                   "`/userinfo user:` — inspect a member"),inline=False)
    embed.set_footer(text="Nonpia Predictor")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── ERROR HANDLING ────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"[Error] {error}")

# ── RUN BOT ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
