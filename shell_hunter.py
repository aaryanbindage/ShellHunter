"""Shell Hunter - see every shell Claude Code is running, which session owns it, and what it's doing.

    python shell_hunter.py            # open the live dashboard in your browser
    python shell_hunter.py --once     # print a text snapshot and exit
    python shell_hunter.py --port N   # choose the port (default 8765)
"""
import argparse
import glob
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
SHELLS = {"bash", "sh", "zsh", "powershell", "pwsh", "cmd"}
IGNORE_CHILDREN = {"conhost"}


def pname(p):
    try:
        return os.path.splitext(p.name().lower())[0]
    except psutil.Error:
        return ""


def cmdline(p):
    try:
        return " ".join(p.cmdline()) or p.name()
    except psutil.Error:
        return ""


def load_sessions():
    """Live Claude instances keyed by pid, from ~/.claude/sessions/<pid>.json."""
    out = {}
    for f in glob.glob(os.path.join(CLAUDE_DIR, "sessions", "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            pid = int(d["pid"])
            p = psutil.Process(pid)
            # guard against pid reuse
            if d.get("startedAt") and abs(p.create_time() * 1000 - d["startedAt"]) > 60000:
                continue
            out[pid] = d
        except (OSError, ValueError, KeyError, psutil.Error):
            continue
    return out


def last_tool_call(session_id, cwd):
    """Most recent tool_use in the session transcript -> short string."""
    hits = glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", session_id + ".jsonl"))
    if not hits:
        return None
    try:
        with open(hits[0], "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 400_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"tool_use"' not in line:
            continue
        try:
            msg = json.loads(line).get("message", {})
            for c in reversed(msg.get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    i = c.get("input", {})
                    detail = i.get("description") or i.get("command") or i.get("file_path") or i.get("pattern") or ""
                    return f"{c.get('name')}: {' '.join(str(detail).split())[:160]}"
        except (ValueError, AttributeError):
            continue
    return None


def claude_marked(p):
    """True if the process was spawned by Claude Code (it exports CLAUDECODE=1)."""
    try:
        return p.environ().get("CLAUDECODE") == "1"
    except (psutil.Error, OSError):
        return False


def deepest(p):
    """Follow the (last) non-conhost child chain to the leaf process."""
    chain = []
    while True:
        try:
            kids = [k for k in p.children() if pname(k) not in IGNORE_CHILDREN]
        except psutil.Error:
            break
        if not kids:
            break
        p = max(kids, key=lambda k: k.create_time())
        chain.append(p)
    return chain


def snapshot():
    sessions = load_sessions()
    shells = []
    now = time.time()
    for cpid, s in sessions.items():
        try:
            claude = psutil.Process(cpid)
            descendants = claude.children(recursive=True)
        except psutil.Error:
            continue
        # candidates: anything Claude marked in the env (works even when a shell
        # exec()s its command and stops looking like a shell), or shell by name.
        # cmd.exe wrappers and conhost are plumbing, not shells.
        wrappers = {d.pid for d in descendants if pname(d) == "cmd"
                    and any(pname(k) in SHELLS - {"cmd"} for k in d.children())}
        cands = {d.pid: d for d in descendants
                 if d.pid not in wrappers and pname(d) not in IGNORE_CHILDREN
                 and (pname(d) in SHELLS or claude_marked(d))}
        # keep only the topmost candidate per branch (nested bash -> bash -> x is one shell slot)
        def has_cand_ancestor(d):
            try:
                return any(a.pid in cands for a in d.parents() if a.pid != cpid)
            except psutil.Error:
                return False
        for d in [c for c in cands.values() if not has_cand_ancestor(c)]:
            chain = deepest(d)
            try:
                started = d.create_time()
                cpu = d.cpu_percent(interval=None)
                mem = d.memory_info().rss
            except psutil.Error:
                continue
            is_shell = pname(d) in SHELLS
            busy = bool(chain) or not is_shell
            leaf = chain[-1] if chain else d
            shells.append({
                "pid": d.pid,
                "ppid": d.ppid(),
                "shell": pname(d) if is_shell else pname(d) + " (exec)",
                "cmdline": cmdline(d),
                "age": int(now - started),
                "mem_mb": round(mem / 1048576, 1),
                "state": "running" if busy else "idle",
                "doing": cmdline(leaf) if busy else None,
                "doing_pid": leaf.pid if busy else None,
                "children": [{"pid": c.pid, "name": c.name()} for c in chain],
                "session": {
                    "claude_pid": cpid,
                    "id": s.get("sessionId"),
                    "name": s.get("name"),
                    "cwd": s.get("cwd"),
                    "status": s.get("status"),
                    "kind": s.get("kind"),
                    "version": s.get("version"),
                    "last_tool": last_tool_call(s.get("sessionId", ""), s.get("cwd")),
                },
            })
    shells.sort(key=lambda x: (x["session"]["claude_pid"], x["pid"]))
    return {
        "time": now,
        "sessions": [
            {"claude_pid": pid, "id": s.get("sessionId"), "name": s.get("name"),
             "cwd": s.get("cwd"), "status": s.get("status"), "kind": s.get("kind")}
            for pid, s in sessions.items()
        ],
        "shells": shells,
    }


def fmt_age(sec):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def print_once():
    snap = snapshot()
    if not snap["sessions"]:
        print("No live Claude sessions found.")
        return
    for s in snap["sessions"]:
        print(f"\n== {s['name']}  [{s['status']}]  claude pid {s['claude_pid']}  session {s['id']}\n   {s['cwd']}")
        mine = [x for x in snap["shells"] if x["session"]["claude_pid"] == s["claude_pid"]]
        if not mine:
            print("   (no shells)")
        for x in mine:
            print(f"   PID {x['pid']:<6} {x['shell']:<10} {x['state']:<8} up {fmt_age(x['age'])}  {x['mem_mb']} MB")
            print(f"        doing: {(x['doing'] or 'waiting for next command')[:150]}")
        if mine and mine[0]["session"]["last_tool"]:
            print(f"   last tool call: {mine[0]['session']['last_tool']}")


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Shell Hunter</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1b;--mute:#6b6a65;--line:#e3e1db;--run:#c2410c;--idle:#15803d;--acc:#2f5fd0}
@media(prefers-color-scheme:dark){:root{--bg:#161615;--card:#212120;--ink:#ecebe6;--mute:#9a9890;--line:#34332f;--run:#fb923c;--idle:#4ade80;--acc:#7ea2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,sans-serif}
header{display:flex;gap:16px;align-items:baseline;padding:18px 24px;border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{margin:0;font-size:20px}.mute{color:var(--mute)}.sp{flex:1}
main{padding:20px 24px;display:grid;gap:20px;max-width:1100px}
.sess{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.sh{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.sh b{font-size:15px}.pill{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.pill.busy{color:var(--run);border-color:var(--run)}
.cwd,.mono{font-family:Consolas,monospace;font-size:12.5px}
.last{padding:8px 16px;border-bottom:1px solid var(--line);color:var(--mute);font-size:13px}
.row{display:grid;grid-template-columns:90px 1fr auto;gap:12px;padding:12px 16px;border-top:1px solid var(--line);align-items:start}
.row:first-of-type{border-top:0}
.pid{font:600 18px Consolas,monospace}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.running .dot{background:var(--run);animation:p 1s infinite}.idle .dot{background:var(--idle)}
@keyframes p{50%{opacity:.3}}
.doing{word-break:break-all}.chain{color:var(--mute);font-size:12px;margin-top:2px}
button{font:inherit;border:1px solid var(--line);background:none;color:var(--mute);border-radius:6px;padding:3px 10px;cursor:pointer}
button:hover{color:var(--run);border-color:var(--run)}
.empty{padding:16px;color:var(--mute)}
@media(max-width:600px){.row{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Shell Hunter</h1><span class="mute" id="sum"></span><span class="sp"></span><span class="mute" id="upd"></span></header>
<main id="m"></main>
<script>
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const age=s=>{const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h}h ${m}m`:`${m}m ${x}s`};
async function kill(pid){if(!confirm("Kill shell "+pid+" and its child processes?"))return;
 await fetch("/kill?pid="+pid,{method:"POST"});load()}
async function load(){
 try{const d=await (await fetch("/api")).json();
 document.getElementById("sum").textContent=`${d.sessions.length} session(s) · ${d.shells.length} shell(s) · ${d.shells.filter(s=>s.state==="running").length} running`;
 document.getElementById("upd").textContent="updated "+new Date(d.time*1000).toLocaleTimeString();
 document.getElementById("m").innerHTML=d.sessions.map(s=>{
  const sh=d.shells.filter(x=>x.session.claude_pid===s.claude_pid);
  const lt=sh.length&&sh[0].session.last_tool;
  return `<section class="sess"><div class="sh"><b>${esc(s.name||"(unnamed)")}</b>
   <span class="pill ${s.status==="busy"?"busy":""}">${esc(s.status)}</span>
   <span class="pill">claude pid ${s.claude_pid}</span><span class="pill mono">${esc((s.id||"").slice(0,8))}</span>
   <span class="cwd mute">${esc(s.cwd)}</span></div>
   ${lt?`<div class="last">last tool call → ${esc(lt)}</div>`:""}
   ${sh.length?sh.map(x=>`<div class="row ${x.state}">
     <div><div class="pid">${x.pid}</div><div class="mute">${esc(x.shell)}</div></div>
     <div><div><span class="dot"></span><b>${x.state}</b> <span class="mute">· up ${age(x.age)} · ${x.mem_mb} MB</span></div>
      <div class="doing mono">${x.doing?esc(x.doing):'<span class="mute">waiting for next command</span>'}</div>
      ${x.children.length?`<div class="chain">${x.children.map(c=>esc(c.name)+" "+c.pid).join(" → ")}</div>`:""}</div>
     <button onclick="kill(${x.pid})">Kill</button></div>`).join(""):'<div class="empty">No shells right now — Claude spawns one when it runs a command.</div>'}
  </section>`}).join("")||'<div class="empty">No live Claude sessions found.</div>';
 }catch(e){document.getElementById("upd").textContent="disconnected"}}
load();setInterval(load,2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api":
            self._send(200, json.dumps(snapshot()), "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self.path.startswith("/kill?pid="):
            return self._send(404, "not found", "text/plain")
        try:
            pid = int(self.path.split("=", 1)[1])
        except ValueError:
            return self._send(400, "bad pid", "text/plain")
        # only ever kill shells that Claude owns right now
        if pid not in {s["pid"] for s in snapshot()["shells"]}:
            return self._send(403, "not a Claude shell", "text/plain")
        try:
            p = psutil.Process(pid)
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
        except psutil.Error as e:
            return self._send(500, str(e), "text/plain")
        self._send(200, "ok", "text/plain")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.once:
        return print_once()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Shell Hunter running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
