#!/usr/bin/env python3
"""
PAPU-LAN - Intercambio de archivos y datos en red local universitaria.
Sin dependencias: solo Python 3 stdlib.
Uso:
    python3 app.py [--port 8000] [--dir ./archivos]
Luego abre en el navegador: http://<IP-local>:8000
"""
import argparse
import html
import json
import mimetypes
import os
import socket
import sys
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = Path(__file__).parent.resolve()
ARCHIVOS = BASE / "archivos"
DATA = BASE / "data"
MSG_FILE = DATA / "mensajes.json"
USERS_FILE = DATA / "usuarios.json"
OFFERS_FILE = DATA / "peticiones.json"
FILES_META = DATA / "archivos.json"
MAX_MSG = 200
ONLINE_SECS = 15
# Límites de transferencia entre usuarios
MAX_FILE_MB = 100            # tamaño máximo por archivo
MAX_FILES_PER_USER = 10      # archivos máximos subidos por usuario
FILE_TTL_H = 2               # se borra si nadie lo descarga en 2 h
OFFER_TTL_MIN = 30           # la petición pendiente expira en 30 min
MAX_BYTES = MAX_FILE_MB * 1024 * 1024

ARCHIVOS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)
if not MSG_FILE.exists():
    MSG_FILE.write_text("[]", encoding="utf-8")
for _f in (USERS_FILE, OFFERS_FILE, FILES_META):
    if not _f.exists():
        _f.write_text("{}" if "archivos" in _f.name else "[]", encoding="utf-8")


def _load_json(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_num(s: str) -> str:
    return "".join(ch for ch in str(s).strip() if ch.isalnum() or ch in ("-", "_"))[:20]


def _sweep():
    """Limpieza: borra archivos vencidos y expira peticiones viejas."""
    now = time.time()
    try:
        meta = _load_json(FILES_META, {})
        offers = _load_json(OFFERS_FILE, [])
        # Expirar peticiones pendientes
        dirty_o = False
        for o in offers:
            if o.get("status") == "pendiente" and now - float(o.get("ts", now)) > OFFER_TTL_MIN * 60:
                o["status"] = "expirada"
                dirty_o = True
        if dirty_o:
            _save_json(OFFERS_FILE, offers[-300:])
        # Borrar archivos vencidos (2 h sin descargar)
        dirty_m = False
        for name, m in list(meta.items()):
            if now - float(m.get("ts", now)) > FILE_TTL_H * 3600:
                try:
                    (ARCHIVOS / name).unlink(missing_ok=True)
                except Exception:
                    pass
                meta.pop(name, None)
                dirty_m = True
        if dirty_m:
            _save_json(FILES_META, meta)
    except Exception:
        pass


def _oneshot_delete(name: str):
    """Borra el archivo tras ser descargado y marca sus peticiones como completadas."""
    try:
        (ARCHIVOS / name).unlink(missing_ok=True)
        meta = _load_json(FILES_META, {})
        meta.pop(name, None)
        _save_json(FILES_META, meta)
        offers = _load_json(OFFERS_FILE, [])
        for o in offers:
            if o.get("file") == name and o.get("status") == "aceptada":
                o["status"] = "completada"
        _save_json(OFFERS_FILE, offers[-300:])
    except Exception:
        pass


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return socket.gethostbyname(socket.gethostname())


def safe_name(name: str) -> str:
    name = os.path.basename(name.strip())
    if not name or name in (".", ".."):
        return f"archivo_{int(time.time())}"
    return name


def load_msgs():
    try:
        return json.loads(MSG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_msgs(msgs):
    MSG_FILE.write_text(json.dumps(msgs[-MAX_MSG:], ensure_ascii=False, indent=2), encoding="utf-8")


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0c0c0e">
<title>PAPU-LAN · SDLG · Red local</title>
<style>
:root{--bg:#0c0c0e;--card:#141114;--acc:#FFDF00;--acc2:#D40000;--txt:#F5F2EA;--mut:#B8B2A7;--r:14px}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--txt);font-family:Arial,Helvetica,sans-serif;min-height:100dvh;font-size:16px;line-height:1.55;touch-action:manipulation;padding-bottom:env(safe-area-inset-bottom)}
h1,h2,.varsity{font-family:"Arial Black",Impact,Haettenschweiler,"Arial Narrow Bold",Arial,sans-serif;letter-spacing:.5px}
header{position:sticky;top:0;z-index:10;background:#000;border-bottom:4px solid var(--acc2);padding:12px 16px;padding-top:calc(12px + env(safe-area-inset-top));display:flex;align-items:center;gap:12px}
.escudo{width:52px;height:52px;flex:none;border-radius:10px;border:2px solid var(--acc2);background:#000}
.t{flex:1;min-width:0}.t h1{font-size:22px;color:var(--acc);text-shadow:2px 2px 0 #000,-1px -1px 0 #000}.t p{color:#fff;background:var(--acc2);display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;margin-top:3px;letter-spacing:.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.badge{background:var(--acc2);color:#fff;border:2px solid #000;padding:6px 10px;border-radius:6px;font-size:12px;font-weight:700;white-space:nowrap;font-family:"Arial Black",Arial,sans-serif}
main{max-width:960px;margin:0 auto;padding:16px;display:grid;gap:16px}
.card{background:var(--card);border:2px solid var(--acc2);border-radius:var(--r);padding:22px 18px}
.card h2{font-size:17px;margin-bottom:14px;padding-bottom:10px;border-bottom:2px solid var(--acc2);color:var(--acc)}
.card h2 span{font-size:12px;display:block;margin-top:6px;font-family:Arial,Helvetica,sans-serif;letter-spacing:0}
.conn{background:#000;border:2px dashed var(--acc);border-radius:8px;padding:14px;font-size:15px;line-height:1.6;margin-bottom:12px;word-break:break-all}
.conn b{color:var(--acc)}
.copy{width:100%;margin-top:10px}
.grid2{display:grid;grid-template-columns:1fr;gap:16px}
label{display:block;margin:14px 0 2px;font-weight:700;font-size:14px}
input,textarea{width:100%;background:#000;border:2px solid #4a4a4a;color:var(--txt);border-radius:8px;padding:14px;font-size:16px;font-family:Arial,Helvetica,sans-serif}
textarea{resize:vertical;min-height:80px}
button{background:linear-gradient(180deg,#E30613,#8F0000);color:#fff;border:3px solid #000;border-radius:10px;padding:15px 16px;min-height:52px;font-weight:900;cursor:pointer;font-size:15px;width:100%;font-family:"Arial Black",Arial,sans-serif;letter-spacing:.5px}
button.ghost{background:#000;color:var(--acc);border:2px solid var(--acc2)}button.danger{background:#3a0000;color:#ff8080;width:auto;min-height:44px;padding:10px 14px;border:2px solid #000}
button:active{transform:scale(.98)}
.row{display:flex;gap:10px;margin-top:12px;flex-direction:column}
.fila{display:grid;grid-template-columns:36px 1fr;gap:4px 10px;padding:12px;border:2px solid #4a4a4a;border-radius:8px;margin-bottom:8px;font-size:15px;background:#000}
.fila .ico{font-size:22px;grid-row:span 2}
.fila .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.fila .s{color:var(--mut);font-size:12px}
.fila .acc{display:flex;gap:8px;grid-column:1/-1;margin-top:6px}
.fila .acc a{flex:1;text-align:center;background:var(--acc);color:#000;font-family:"Arial Black",Arial,sans-serif;text-decoration:none;border:3px solid #000;border-radius:8px;padding:12px;min-height:48px;display:flex;align-items:center;justify-content:center}
.msg{border-left:4px solid var(--acc2);background:#000;padding:10px 12px;border-radius:0 8px 8px 0;margin-bottom:8px}
.msg .m{font-size:12px;color:var(--mut)}.msg .t{font-size:15px;margin-top:2px;word-break:break-word}
footer{text-align:center;color:var(--mut);font-size:12px;line-height:1.7;padding:24px 16px}
#drop{border:2px dashed var(--acc2);border-radius:10px;padding:20px;text-align:center;color:var(--mut);font-size:14px;margin-bottom:10px}
#drop.over{border-color:var(--acc);color:var(--acc)}
@media(hover:none){#drop{display:none}}
@media(min-width:760px){main{padding:24px;gap:20px}.grid2{grid-template-columns:1fr 1fr;gap:20px}.card{padding:24px}button{width:auto}.row{flex-direction:row}.off .btns{flex-direction:row}.fila{display:flex;align-items:center;background:none;border:none;border-bottom:1px solid #4a4a4a;border-radius:0;padding:10px;margin:0}.fila .acc{display:contents}.fila .acc a{flex:none;padding:8px 14px;min-height:0}.t p{white-space:normal}}
#login{position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.96);display:flex;align-items:center;justify-content:center;padding:20px}
#login .box{background:#0a0a0a;border:3px solid var(--acc2);border-radius:12px;padding:28px 24px;width:100%;max-width:380px;text-align:center}
#login .box .escudo-big{width:110px;height:110px;border-radius:12px;border:3px solid var(--acc2);background:#000}
#login .box h2{margin-bottom:6px}#login .box p{color:var(--mut);font-size:13px;margin-bottom:12px}
#login input{margin-bottom:12px}
.me{display:flex;align-items:center;gap:10px;background:#000;border:2px solid var(--acc2);border-radius:10px;padding:10px 14px;font-size:14px;margin-bottom:12px}
.me button{width:auto;min-height:0;padding:6px 12px;font-size:13px}
.off{border:2px solid #4a4a4a;border-radius:10px;padding:16px;margin-bottom:12px;background:#000}
.off.pend{border-color:var(--acc)}.off.ok{border-color:var(--acc)}.off.no{opacity:.75}
.off .t2{font-size:16px;font-weight:700}.off .d{font-size:13px;color:var(--mut);margin:4px 0 14px;line-height:1.5}
.off .btns{display:flex;gap:10px;flex-direction:column}.off .btns button{flex:1}
.usr{display:flex;align-items:center;gap:10px;padding:10px;border-bottom:1px solid var(--acc2);font-size:15px}
.usr:last-child{border:none}.usr .dot{width:10px;height:10px;border-radius:50%;background:#475569}.usr .dot.on{background:#4ade80}
.usr small{color:var(--mut)}
select{width:100%;background:#000;border:2px solid #4a4a4a;color:var(--txt);border-radius:8px;padding:14px;font-size:16px;font-family:Arial,Helvetica,sans-serif}
.pacstrip{display:flex;align-items:center;gap:10px;background:#000;border:2px solid var(--acc2);border-radius:10px;padding:10px 14px;margin-bottom:12px;overflow:hidden}
.pacman{width:0;height:0;flex:none;border-top:13px solid #FFE600;border-bottom:13px solid #FFE600;border-left:13px solid #FFE600;border-right:13px solid transparent;border-radius:50%;animation:muerde .32s infinite alternate}
@keyframes muerde{from{transform:rotate(-18deg)}to{transform:rotate(18deg)}}
.coco{width:8px;height:8px;flex:none;border-radius:50%;background:#fff;box-shadow:0 0 6px #fff;animation:avanza 1.1s infinite linear}
.coco:nth-of-type(3){animation-delay:.15s}.coco:nth-of-type(4){animation-delay:.3s}.coco:nth-of-type(5){animation-delay:.45s}.coco:nth-of-type(6){animation-delay:.6s}
@keyframes avanza{0%{transform:translateX(0);opacity:1}80%{opacity:1}100%{transform:translateX(-46px);opacity:0}}
.sticker{display:inline-block;background:#000;color:#fff;border-top:3px solid var(--acc2);border-bottom:3px solid var(--acc2);padding:4px 14px;font-weight:900;font-size:13px;letter-spacing:2px;font-family:"Arial Black",Arial,sans-serif}
</style>
</head>
<body>
<header>
<div class="logo"><img class="escudo" src="/logo.png" alt="SDLG"></div>
<div class="t"><h1>PAPU-LAN</h1><p>SEGUIDORES DE LA GRASA</p></div>
<div class="badge">EN LINEA</div>
</header>
<div id="login"><div class="box">
<img class="escudo-big" src="/logo.png" alt="Seguidores de la Grasa">
<h2 style="color:var(--acc);margin-top:8px">PAPU-LAN</h2>
<p><span class="sticker">SEGUIDORES DE LA GRASA</span></p>
<p>Pon tu numero de usuario del laboratorio.<br>Por ahora no se valida que exista, solo te identifica entre los demas.</p>
<input id="linum" placeholder="Tu número de usuario (ej. 2024007)" maxlength="20" inputmode="numeric">
<input id="linom" placeholder="Tu nombre (ej. Ana)" maxlength="40" autocomplete="name">
<div class="row"><button onclick="entrar()">ENTRAR</button></div>
</div></div>
<main>
<div class="pacstrip"><div class="pacman"></div><div class="coco"></div><div class="coco"></div><div class="coco"></div><div class="coco"></div><div class="coco"></div><span style="font-size:12px;letter-spacing:1px">TRANSFIRIENDO EN LA RED LOCAL</span></div>
<div class="me"><span id="meinfo">Sin sesion</span><button class="ghost" onclick="salir()">SALIR</button></div>
<div class="card"><h2>CONEXION EN EL LABORATORIO</h2>
<div class="conn" id="conn">Cargando dirección local…</div>
<button class="ghost copy" onclick="copiar()">COPIAR DIRECCION</button>
<p style="color:var(--mut);font-size:13px;margin-top:8px">Mismo Wi-Fi del laboratorio: abre la direccion en tu celular y pon tu numero de usuario.</p>
</div>
<div class="card"><h2>1. SUBE EL ARCHIVO QUE QUIERES MANDAR</h2>
<p style="font-size:12px;color:var(--mut);margin-bottom:8px">Limite: <b>100 MB</b> por archivo · <b>10</b> archivos por usuario · se borra solo al descargar o en <b>2 horas</b> · la peticion expira en <b>30 min</b>.</p>
<div id="drop">Arrastra archivos aquí o elígelos abajo</div>
<input type="file" id="file" multiple>
<div class="row"><button onclick="subir()">SUBIR ARCHIVOS</button></div>
<p id="st" style="font-size:13px;color:var(--mut);margin-top:8px"></p></div>
<div class="grid2">
<div class="card"><h2>2. MANDA LA PETICION</h2>
<label style="font-size:13px;color:var(--mut)">Archivo</label>
<select id="selFile" style="margin:6px 0 10px"></select>
<label style="font-size:13px;color:var(--mut)">Para (numero de usuario)</label>
<input id="dest" placeholder="Numero del destinatario (ej. 2024015)" style="margin:6px 0 10px" maxlength="20" inputmode="numeric">
<div class="row"><button onclick="pedir()">ENVIAR PETICION DE INTERCAMBIO</button></div>
<p id="st2" style="font-size:13px;color:var(--mut);margin-top:8px"></p></div>
</div>
<div class="card"><h2>PETICIONES PARA TI <span style="font-size:12px;color:var(--mut)">(acepta para iniciar la transferencia)</span></h2><div id="inbox">—</div></div>
<div class="card"><h2>TUS PETICIONES ENVIADAS</h2><div id="outbox">—</div></div>
</main>
<footer>PAPU-LAN · <span class="sticker">SEGUIDORES DE LA GRASA</span> · practica de Sistemas Operativos · red local</footer>
<script>
let YO='',NOMBRE='',USERS=[],FILES=[],OFFERS=[];
YO=localStorage.getItem('papu_num')||'';NOMBRE=localStorage.getItem('papu_nom')||'';
async function entrar(){const n=document.getElementById('linum').value.trim();if(!n){alert('Pon tu numero de usuario');return}
const nm=document.getElementById('linom').value.trim();
const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numero:n,nombre:nm})});
const d=await r.json();if(!d.ok){alert('No se pudo entrar');return}
YO=d.numero;NOMBRE=d.nombre||nm;localStorage.setItem('papu_num',YO);localStorage.setItem('papu_nom',NOMBRE);
verSesion();cargarTodo()}
function salir(){localStorage.removeItem('papu_num');YO='';verSesion()}
function verSesion(){document.getElementById('login').style.display=YO?'none':'flex';
document.getElementById('meinfo').textContent=YO?('USUARIO '+YO+(NOMBRE?' · '+NOMBRE:'')):'Sin sesion';
if(YO){document.getElementById('linum').value=YO;document.getElementById('linom').value=NOMBRE||''}}
async function ping(){if(!YO)return;await fetch('/api/ping',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numero:YO})})}
function avisarSalida(){if(!YO)return;try{navigator.sendBeacon('/api/leave',JSON.stringify({numero:YO}))}catch(e){}}
window.addEventListener('pagehide',avisarSalida);
document.addEventListener('visibilitychange',()=>{if(document.hidden){avisarSalida()}else{ping()}});
async function cargarTodo(){verSesion();if(!YO)return;ping();cargarFiles();cargarConn();cargarOffers()}
async function cargarConn(){try{const r=await fetch('/api/info');const d=await r.json();window._url='http://'+d.ip+':'+d.port;document.getElementById('conn').innerHTML='En tu celular abre:<br><b>'+window._url+'</b><br><span style="color:var(--mut);font-size:12px">'+d.host+'</span>'}catch(e){}}
async function copiar(){try{await navigator.clipboard.writeText(window._url||'');alert('Dirección copiada')}catch(e){}}
async function cargarFiles(){try{const r=await fetch('/api/files');FILES=await r.json();
document.getElementById('selFile').innerHTML=FILES.filter(f=>!YO||f.from===YO||!f.from).map(f=>`<option value="${f.name}">${f.name} (${f.size_h})</option>`).join('')||'<option value="">(sube un archivo primero)</option>'}catch(e){}}
async function cargarOffers(){if(!YO)return;try{const r=await fetch('/api/offers?user='+encodeURIComponent(YO));OFFERS=await r.json();
const inc=OFFERS.filter(o=>o.to===YO),out=OFFERS.filter(o=>o.from===YO);
document.getElementById('inbox').innerHTML=inc.length?inc.map(o=>{
if(o.status==='pendiente')return `<div class="off pend"><div class="t2">${o.file}</div><div class="d">De <b>${o.from}</b> · ${o.fecha} · pendiente tu respuesta</div><div class="btns"><button onclick="resp('${o.id}',true)">ACEPTAR E INICIAR TRANSFERENCIA</button><button class="ghost" onclick="resp('${o.id}',false)">RECHAZAR</button></div></div>`;
if(o.status==='aceptada')return `<div class="off ok"><div class="t2">${o.file}</div><div class="d">De ${o.from} · aceptada · la transferencia esta en curso</div><div class="btns"><a href="/download/${encodeURIComponent(o.file)}?by=${encodeURIComponent(YO)}" style="flex:1;text-align:center;background:var(--acc);color:#000;font-family:Arial Black,Arial,sans-serif;text-decoration:none;border:3px solid #000;border-radius:8px;padding:13px">DESCARGAR AHORA (SE BORRA AL DESCARGAR)</a></div></div>`;
if(o.status==='completada')return `<div class="off ok"><div class="t2">${o.file}</div><div class="d">De ${o.from} · ya se descargo y se borro del servidor</div></div>`;
if(o.status==='expirada')return `<div class="off no"><div class="t2">${o.file}</div><div class="d">De ${o.from} · la peticion expiro (30 min), pide que te la manden de nuevo</div></div>`;
return `<div class="off no"><div class="t2">${o.file}</div><div class="d">De ${o.from} · ${o.status}</div></div>`}).join(''):'<p style="color:var(--mut)">Sin peticiones por ahora.</p>';
document.getElementById('outbox').innerHTML=out.length?out.map(o=>{
let extra=o.status==='aceptada'?` · <a href="/download/${encodeURIComponent(o.file)}" style="color:var(--acc)">descargar</a>`:'';
return `<div class="off ${o.status==='pendiente'?'pend':o.status==='aceptada'?'ok':'no'}"><div class="t2">${o.status.toUpperCase()} · ${o.file}</div><div class="d">Para <b>${o.to}</b> · ${o.fecha}${extra}</div></div>`}).join(''):'<p style="color:var(--mut)">Aún no envías peticiones.</p>'}catch(e){}}
async function resp(id,ok){await fetch('/api/offers/'+id+(ok?'/accept':'/reject'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user:YO})});cargarOffers()}
async function pedir(){const f=document.getElementById('selFile').value;const t=document.getElementById('dest').value.trim();
if(!YO){alert('Entra primero con tu numero');return}if(!f){alert('Sube o elige un archivo');return}if(!t){alert('Escribe el numero destino');return}
const r=await fetch('/api/offers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from:YO,to:t,file:f})});
const d=await r.json();document.getElementById('st2').textContent=d.ok?'Peticion enviada a '+t:'Error: '+(d.error||'Error');cargarOffers()}
async function subir(){const inp=document.getElementById('file');if(!inp.files.length){document.getElementById('st').textContent='Elige al menos un archivo.';return}
const fd=new FormData();for(const f of inp.files)fd.append('f',f,f.name);
document.getElementById('st').textContent='Subiendo…';
const r=await fetch('/api/upload?from='+encodeURIComponent(YO),{method:'POST',body:fd});const d=await r.json();
document.getElementById('st').textContent=(d.ok?('OK: '+d.saved.join(', ')):'Error: '+(d.error||'Error al subir'))+(d.aviso?' · '+d.aviso:'');
inp.value='';cargarFiles()}
async function borrar(n){if(!confirm('¿Eliminar "'+n+'"?'))return;await fetch('/api/files/'+encodeURIComponent(n),{method:'DELETE'});cargarFiles()}
const drop=document.getElementById('drop');if(drop){
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',ev=>{document.getElementById('file').files=ev.dataTransfer.files;subir()})}
verSesion();if(YO)cargarTodo();setInterval(()=>{if(YO){ping();cargarOffers();cargarFiles()}},5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PapuLAN/1.0"

    def log_message(self, *a):
        sys.stdout.write(f"[{datetime.now():%H:%M:%S}] {a[0] % a[1:]}\n")

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        _sweep()
        if path in ("/", "/index.html"):
            b = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif path == "/logo.png":
            lp = BASE / "logo.png"
            if not lp.is_file():
                self.send_error(404)
                return
            data = lp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/info":
            self._json({"ip": get_lan_ip(), "port": self.server.server_port,
                        "host": socket.gethostname()})
        elif path == "/api/users":
            users = _load_json(USERS_FILE, [])
            now = time.time()
            out = [{"numero": x.get("numero"), "nombre": x.get("nombre", ""),
                    "online": (now - float(x.get("seen", 0))) < ONLINE_SECS}
                   for x in users if x.get("numero")]
            out.sort(key=lambda x: (not x["online"], x["numero"]))
            self._json(out)
        elif path == "/api/files":
            meta = _load_json(FILES_META, {})
            out = []
            for f in sorted(ARCHIVOS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.is_file():
                    st = f.stat()
                    out.append({"name": f.name, "size": st.st_size,
                                "size_h": fmt_size(st.st_size),
                                "date": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m %H:%M"),
                                "from": meta.get(f.name, {}).get("from", "")})
            self._json(out)
        elif path == "/api/messages":
            self._json(load_msgs())
        elif path == "/api/offers":
            me = clean_num(qs.get("user", [""])[0])
            offers = _load_json(OFFERS_FILE, [])
            mine = [o for o in offers if o.get("from") == me or o.get("to") == me]
            mine.sort(key=lambda o: o.get("ts", 0), reverse=True)
            self._json(mine[:60])
        elif path.startswith("/download/"):
            name = safe_name(urllib.parse.unquote(path[len("/download/"):]))
            by = clean_num(qs.get("by", [""])[0])
            fp = ARCHIVOS / name
            if not fp.is_file():
                self.send_error(404, "No existe o ya fue descargado")
                return
            meta_now = _load_json(FILES_META, {})
            owner = meta_now.get(name, {}).get("from", "")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(name)}"')
            self.end_headers()
            self.wfile.write(data)
            # Autodestrucción: si lo descarga otro usuario, se borra del servidor.
            if by and by != owner:
                _oneshot_delete(name)
        else:
            self.send_error(404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)
        if path == "/api/login":
            d = self._body_json()
            num = clean_num(d.get("numero", ""))
            nom = html.escape(str(d.get("nombre", ""))[:40].strip())
            if not num:
                return self._json({"ok": False, "error": "numero requerido"}, 400)
            # Por ahora no se valida que el número exista: se registra al entrar.
            users = _load_json(USERS_FILE, [])
            users = [x for x in users if x.get("numero") != num]
            users.append({"numero": num, "nombre": nom, "seen": time.time()})
            _save_json(USERS_FILE, users[-500:])
            return self._json({"ok": True, "numero": num, "nombre": nom})
        elif path == "/api/ping":
            d = self._body_json()
            num = clean_num(d.get("numero", ""))
            if not num:
                return self._json({"ok": False}, 400)
            users = _load_json(USERS_FILE, [])
            found = False
            for x in users:
                if x.get("numero") == num:
                    x["seen"] = time.time()
                    found = True
            if not found:
                users.append({"numero": num, "nombre": "", "seen": time.time()})
            _save_json(USERS_FILE, users[-500:])
            return self._json({"ok": True})
        elif path == "/api/leave":
            d = self._body_json()
            num = clean_num(d.get("numero", ""))
            users = _load_json(USERS_FILE, [])
            for x in users:
                if x.get("numero") == num:
                    x["seen"] = 0
            _save_json(USERS_FILE, users[-500:])
            return self._json({"ok": True})
        elif path == "/api/upload":
            frm = clean_num(qs.get("from", [""])[0])
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return self._json({"ok": False, "error": "multipart requerido"}, 400)
            try:
                boundary = ctype.split("boundary=")[1].encode()
            except IndexError:
                return self._json({"ok": False, "error": "boundary faltante"}, 400)
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > MAX_BYTES + 1024 * 1024:
                return self._json({"ok": False, "error": f"límite: {MAX_FILE_MB} MB por archivo"}, 413)
            meta = _load_json(FILES_META, {})
            propios = sum(1 for m in meta.values() if m.get("from") == frm) if frm else 0
            if frm and propios >= MAX_FILES_PER_USER:
                return self._json({"ok": False, "error": f"límite: {MAX_FILES_PER_USER} archivos por usuario"}, 429)
            raw = self.rfile.read(length)
            saved, big = [], False
            for part in raw.split(b"--" + boundary):
                if b'filename="' not in part:
                    continue
                head, _, body = part.partition(b"\r\n\r\n")
                fn = head.split(b'filename="')[1].split(b'"')[0].decode("utf-8", "ignore")
                fn = safe_name(urllib.parse.unquote(fn))
                body = body.rsplit(b"\r\n", 1)[0]
                if not body:
                    continue
                if len(body) > MAX_BYTES:
                    big = True
                    continue
                dest = ARCHIVOS / fn
                i = 1
                while dest.exists():
                    dest = ARCHIVOS / f"{dest.stem}_{i}{dest.suffix}"
                    i += 1
                dest.write_bytes(body)
                saved.append(dest.name)
                meta[dest.name] = {"from": frm, "ts": time.time()}
            _save_json(FILES_META, meta)
            r = {"ok": bool(saved), "saved": saved,
                 "limites": f"{MAX_FILE_MB} MB/archivo · {MAX_FILES_PER_USER} archivos/usuario · se borra al descargar o en {FILE_TTL_H} h"}
            if big:
                r["aviso"] = f"algún archivo superó {MAX_FILE_MB} MB y se rechazó"
            return self._json(r)
        elif path == "/api/messages":
            try:
                d = self._body_json()
                texto = html.escape(str(d.get("texto", ""))[:1000].strip())
                autor = html.escape(str(d.get("autor", "Anónimo"))[:40].strip()) or "Anónimo"
                if not texto:
                    return self._json({"ok": False}, 400)
                msgs = load_msgs()
                msgs.append({"autor": autor, "texto": texto,
                             "fecha": datetime.now().strftime("%d/%m %H:%M")})
                save_msgs(msgs)
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 400)
        elif path == "/api/offers":
            d = self._body_json()
            frm, to = clean_num(d.get("from", "")), clean_num(d.get("to", ""))
            fn = safe_name(str(d.get("file", "")))
            if not frm or not to or not fn:
                return self._json({"ok": False, "error": "from/to/file requeridos"}, 400)
            if frm == to:
                return self._json({"ok": False, "error": "no puedes enviarte a ti mismo"}, 400)
            if not (ARCHIVOS / fn).is_file():
                return self._json({"ok": False, "error": "archivo no existe"}, 404)
            offers = _load_json(OFFERS_FILE, [])
            oid = str(int(time.time() * 1000))[-8:] + str(os.getpid())[-2:]
            offers.append({"id": oid, "from": frm, "to": to, "file": fn,
                           "status": "pendiente", "ts": time.time(),
                           "fecha": datetime.now().strftime("%d/%m %H:%M")})
            _save_json(OFFERS_FILE, offers[-300:])
            return self._json({"ok": True, "id": oid})
        elif path.startswith("/api/offers/") and (path.endswith("/accept") or path.endswith("/reject")):
            oid = path[len("/api/offers/"):].rsplit("/", 1)[0]
            d = self._body_json()
            me = clean_num(d.get("user", ""))
            offers = _load_json(OFFERS_FILE, [])
            ok = False
            for o in offers:
                if o.get("id") == oid and o.get("to") == me and o.get("status") == "pendiente":
                    o["status"] = "aceptada" if path.endswith("/accept") else "rechazada"
                    ok = True
            _save_json(OFFERS_FILE, offers[-300:])
            return self._json({"ok": ok})
        else:
            self.send_error(404)

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path
        if p.startswith("/api/files/"):
            name = safe_name(urllib.parse.unquote(p[len("/api/files/"):]))
            fp = ARCHIVOS / name
            if fp.is_file():
                fp.unlink()
                return self._json({"ok": True})
            return self._json({"ok": False}, 404)
        self.send_error(404)


def main():
    global ARCHIVOS
    ap = argparse.ArgumentParser(description="Papu-LAN: archivos en red local")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dir", default=str(ARCHIVOS))
    a = ap.parse_args()
    ARCHIVOS = Path(a.dir).resolve()
    ARCHIVOS.mkdir(parents=True, exist_ok=True)
    srv = HTTPServer(("0.0.0.0", a.port), Handler)
    ip = get_lan_ip()
    print("=" * 50)
    print("  📁 Papu-LAN funcionando")
    print(f"  👉 Este equipo : http://localhost:{a.port}")
    print(f"  👉 Red local   : http://{ip}:{a.port}")
    print(f"  📂 Carpeta     : {ARCHIVOS}")
    print("  Pulsa Ctrl+C para detener")
    print("=" * 50)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPapu-LAN detenido.")


if __name__ == "__main__":
    main()
