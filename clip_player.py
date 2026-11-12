#!/usr/bin/env python3
"""
Random Twitch Clip Player (works on your PC or on a web host like Render)

Reads these settings from environment variables:
  TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_CHANNEL

Page URL options:
  ?autostart=1   start immediately (use this for OBS)
  ?title=0       hide the clip title / creator overlay
  ?muted=1       play muted
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.twitch.tv/helix"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips.json")
REFRESH_SECONDS = 6 * 60 * 60  # re-download the clip list every 6 hours

state = {"clips": []}


def http_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_clips(channel, client_id, client_secret, max_clips):
    token = http_json(
        TOKEN_URL,
        data=urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }).encode(),
    )["access_token"]
    headers = {"Client-Id": client_id, "Authorization": f"Bearer {token}"}

    users = http_json(f"{API}/users?login={urllib.parse.quote(channel)}", headers=headers)["data"]
    if not users:
        raise RuntimeError(f"Channel '{channel}' not found.")
    broadcaster_id = users[0]["id"]

    clips, cursor = [], None
    while len(clips) < max_clips:
        q = {"broadcaster_id": broadcaster_id, "first": 100}
        if cursor:
            q["after"] = cursor
        page = http_json(f"{API}/clips?{urllib.parse.urlencode(q)}", headers=headers)
        for c in page["data"]:
            clips.append({
                "id": c["id"],
                "title": c["title"],
                "creator": c["creator_name"],
                "duration": c["duration"],
                "views": c["view_count"],
            })
        print(f"  fetched {len(clips)} clips...", flush=True)
        cursor = page.get("pagination", {}).get("cursor")
        if not cursor:
            break
    return clips[:max_clips]


def refresh_loop(channel, cid, secret, max_clips):
    while True:
        wait = REFRESH_SECONDS
        try:
            print(f"Fetching clips for {channel}...", flush=True)
            clips = fetch_clips(channel, cid, secret, max_clips)
            if clips:
                state["clips"] = clips
                try:
                    with open(CACHE, "w") as f:
                        json.dump(clips, f)
                except OSError:
                    pass
                print(f"Loaded {len(clips)} clips.", flush=True)
            else:
                print("No clips found for that channel.", flush=True)
                wait = 300
        except urllib.error.HTTPError as e:
            print(f"Twitch API error {e.code}: {e.read().decode(errors='replace')}", flush=True)
            wait = 300
        except Exception as e:
            print(f"Error fetching clips: {e}", flush=True)
            wait = 300
        time.sleep(wait)


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Random Clips</title>
<style>
  html,body{margin:0;height:100%;background:#0e0e10;color:#efeff1;font-family:system-ui,sans-serif;overflow:hidden}
  iframe{position:fixed;inset:0;width:100%;height:100%;border:0}
  #start{position:fixed;inset:0;display:flex;flex-direction:column;gap:12px;align-items:center;justify-content:center;background:#0e0e10;z-index:3}
  #start button{font-size:20px;padding:14px 32px;border:0;border-radius:8px;background:#9146ff;color:#fff;cursor:pointer}
  #info{position:fixed;left:16px;bottom:16px;z-index:2;background:rgba(0,0,0,.65);padding:8px 14px;border-radius:8px;font-size:16px;max-width:70%;transition:opacity .6s}
  #info small{display:block;opacity:.75}
</style></head><body>
<div id="start"><div id="count">Loading clips, please wait...</div><button id="go">&#9654; Start random clips</button><small>Press N or &rarr; to skip</small></div>
<iframe id="player" allow="autoplay; fullscreen" allowfullscreen></iframe>
<div id="info" style="opacity:0"></div>
<script>
const params = new URLSearchParams(location.search);
const muted = params.get("muted") === "1";
const showTitle = params.get("title") !== "0";
const autostart = params.get("autostart") === "1";
let clips = [], bag = [], last = null, timer = null, started = false;

function shuffle(a){for(let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]];}return a;}
function pick(){
  if(!bag.length){
    bag = shuffle(clips.slice());
    if(bag.length>1 && bag[bag.length-1].id===last){[bag[0],bag[bag.length-1]]=[bag[bag.length-1],bag[0]];}
  }
  const c = bag.pop(); last = c.id; return c;
}
function next(){
  if(!clips.length) return;
  clearTimeout(timer);
  const c = pick();
  document.getElementById("player").src =
    `https://clips.twitch.tv/embed?clip=${encodeURIComponent(c.id)}&parent=${location.hostname}&autoplay=true&muted=${muted}`;
  const info = document.getElementById("info");
  if(showTitle){
    info.innerHTML = "";
    const t = document.createElement("div"); t.textContent = c.title;
    const s = document.createElement("small"); s.textContent = "clipped by " + c.creator;
    info.append(t, s);
    info.style.opacity = 1;
    setTimeout(()=>info.style.opacity = 0, 6000);
  }
  const buffer = parseFloat(params.get("buffer")) || 4;
  const frame = document.getElementById("player");
  // Backup timer in case the load signal never arrives.
  timer = setTimeout(next, (c.duration + buffer + 8) * 1000);
  // Restart the countdown once the clip has actually loaded.
  frame.onload = () => {
    clearTimeout(timer);
    timer = setTimeout(next, (c.duration + buffer) * 1000);
  };
}
function begin(){
  if(started || !clips.length) return;
  started = true;
  document.getElementById("start").style.display = "none";
  next();
}
document.addEventListener("keydown", e => { if(e.key==="n"||e.key==="N"||e.key==="ArrowRight") next(); });
document.getElementById("go").onclick = begin;

function load(){
  fetch("/clips.json", {cache:"no-store"}).then(r=>r.json()).then(d=>{
    if(!d.length){
      document.getElementById("count").textContent = "Loading clips, please wait...";
      setTimeout(load, 4000); return;
    }
    clips = d; bag = [];
    document.getElementById("count").textContent = d.length + " clips loaded";
    if(autostart) begin();
  }).catch(()=>setTimeout(load, 4000));
}
load();
setInterval(()=>fetch("/clips.json",{cache:"no-store"}).then(r=>r.json()).then(d=>{ if(d.length){clips=d; bag=[];} }).catch(()=>{}), 3600000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        elif path == "/clips.json":
            body, ctype = json.dumps(state["clips"]).encode(), "application/json"
        elif path == "/health":
            body, ctype = b"ok", "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description="Play random clips from a Twitch channel.")
    ap.add_argument("--channel", default=os.getenv("TWITCH_CHANNEL"), help="Twitch channel login name")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--max-clips", type=int, default=1000)
    args = ap.parse_args()

    cid = os.getenv("TWITCH_CLIENT_ID")
    secret = os.getenv("TWITCH_CLIENT_SECRET")

    # Load saved clips first so the player works right away if there is a cache.
    if os.path.exists(CACHE):
        try:
            with open(CACHE) as f:
                state["clips"] = json.load(f)
        except (OSError, ValueError):
            pass

    if args.channel and cid and secret:
        threading.Thread(
            target=refresh_loop, args=(args.channel, cid, secret, args.max_clips), daemon=True
        ).start()
    elif not state["clips"]:
        sys.exit("Need TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET and TWITCH_CHANNEL to be set.")

    hosted = "PORT" in os.environ
    port = int(os.getenv("PORT", args.port))
    host = "0.0.0.0" if hosted else "localhost"
    print(f"Player running on port {port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
