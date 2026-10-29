#!/usr/bin/env python3
"""
PAPU-LAN - Intercambio de archivos en red local universitaria.
Sin dependencias: solo Python 3 stdlib.

Uso:
    python3 app.py [--port 8000] [--dir ./archivos] [--data ./data] [--ip 192.168.0.10]
Luego abre en el navegador: http://<IP-local>:8000
"""
import argparse
import json
import mimetypes
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).parent.resolve()
ARCHIVOS = BASE / "archivos"
DATA = BASE / "data"
OFFERS_FILE = DATA / "peticiones.json"
FILES_META = DATA / "archivos.json"

# Limites de transferencia entre usuarios
MAX_FILE_MB = 100            # tamano maximo por archivo
MAX_FILES_PER_USER = 10      # archivos maximos subidos por usuario
MAX_OFFERS_PER_USER = 10     # peticiones pendientes maximas por usuario
FILE_TTL_H = 2               # se borra si nadie lo descarga en 2 h
OFFER_TTL_MIN = 30           # la peticion pendiente expira en 30 min
MAX_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_NAME_LEN = 120           # largo maximo del nombre de archivo
MAX_JSON_BYTES = 64 * 1024   # tope del cuerpo JSON de las peticiones
SWEEP_SECS = 60              # cada cuanto corre la limpieza
OFFERS_KEEP = 300            # peticiones que se conservan en el historial

# Un solo candado para todos los JSON: las escrituras son criticas y cortas.
LOCK = threading.RLock()


def init_dirs(archivos=None, data=None):
    """Prepara las carpetas de trabajo. Se llama desde main() y desde los tests."""
    global ARCHIVOS, DATA, OFFERS_FILE, FILES_META
    if archivos:
        ARCHIVOS = Path(archivos).resolve()
    if data:
        DATA = Path(data).resolve()
    ARCHIVOS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    OFFERS_FILE = DATA / "peticiones.json"
    FILES_META = DATA / "archivos.json"
    if not OFFERS_FILE.exists():
        _save_json(OFFERS_FILE, [])
    if not FILES_META.exists():
        _save_json(FILES_META, {})


def _load_json(p, default):
    try:
        with LOCK:
            return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(p, obj):
    """Escritura atomica: escribe a un temporal y lo renombra.

    Un Ctrl+C o un corte de luz nunca deja el JSON a medias.
    """
    p = Path(p)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with LOCK:
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)


def clean_num(s: str) -> str:
    return "".join(ch for ch in str(s).strip() if ch.isalnum() or ch in ("-", "_"))[:20]


def safe_name(name: str) -> str:
    """Nombre de archivo seguro: sin rutas, sin caracteres de control ni de HTML."""
    name = str(name).replace("\\", "/")
    name = os.path.basename(name).strip()
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = name.strip(" .")
    if not name or name in (".", ".."):
        return f"archivo_{int(time.time())}"
    if len(name) > MAX_NAME_LEN:
        suf = Path(name).suffix[:16]
        name = Path(name).stem[: MAX_NAME_LEN - len(suf)] + suf
    return name


def _unique_dest(fn: str) -> Path:
    """Primer nombre libre: a.txt, a_1.txt, a_2.txt (antes se acumulaba a_1_2)."""
    dest = ARCHIVOS / fn
    if not dest.exists():
        return dest
    stem, suf = Path(fn).stem, Path(fn).suffix
    i = 1
    while True:
        dest = ARCHIVOS / f"{stem}_{i}{suf}"
        if not dest.exists():
            return dest
        i += 1


def _sweep():
    """Limpieza: borra archivos vencidos, expira peticiones y quita metadatos huerfanos."""
    now = time.time()
    try:
        with LOCK:
            meta = _load_json(FILES_META, {})
            offers = _load_json(OFFERS_FILE, [])
            dirty_o = False
            for o in offers:
                if o.get("status") == "pendiente" and now - float(o.get("ts", now)) > OFFER_TTL_MIN * 60:
                    o["status"] = "expirada"
                    dirty_o = True
            if dirty_o:
                _save_json(OFFERS_FILE, offers[-OFFERS_KEEP:])
            dirty_m = False
            for name, m in list(meta.items()):
                vencido = now - float(m.get("ts", now)) > FILE_TTL_H * 3600
                if vencido or not (ARCHIVOS / name).is_file():
                    if vencido:
                        try:
                            (ARCHIVOS / name).unlink(missing_ok=True)
                        except Exception:
                            pass
                    meta.pop(name, None)
                    dirty_m = True
            if dirty_m:
                _save_json(FILES_META, meta)
            # restos de subidas interrumpidas
            for t in ARCHIVOS.glob(".subiendo_*.part"):
                try:
                    if now - t.stat().st_mtime > 3600:
                        t.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass


def _sweep_loop():
    while True:
        time.sleep(SWEEP_SECS)
        _sweep()


def _oneshot_delete(name: str):
    """Borra el archivo descargado y cierra TODAS las peticiones que lo usaban."""
    try:
        (ARCHIVOS / name).unlink(missing_ok=True)
        with LOCK:
            meta = _load_json(FILES_META, {})
            meta.pop(name, None)
            _save_json(FILES_META, meta)
            offers = _load_json(OFFERS_FILE, [])
            for o in offers:
                if o.get("file") == name and o.get("status") in ("pendiente", "aceptada"):
                    o["status"] = "completada"
            _save_json(OFFERS_FILE, offers[-OFFERS_KEEP:])
    except Exception:
        pass


def get_lan_ip(prefer=None):
    if prefer:
        return prefer
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


def all_lan_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    main = get_lan_ip()
    if main not in ips:
        ips.insert(0, main)
    return ips


def fmt_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


class _Multipart:
    """Lector multipart en streaming: nunca carga el cuerpo completo en memoria."""

    def __init__(self, rfile, boundary, length):
        self.rfile = rfile
        self.delim = b"--" + boundary
        self.left = max(0, int(length))
        self.buf = b""

    def _fill(self, size=65536):
        if self.left <= 0:
            return False
        try:
            data = self.rfile.read(min(size, self.left))
        except Exception:
            data = b""
        if not data:
            self.left = 0
            return False
        self.left -= len(data)
        self.buf += data
        return True

    def _line(self):
        while b"\r\n" not in self.buf:
            if not self._fill():
                line, self.buf = self.buf, b""
                return line
        line, _, self.buf = self.buf.partition(b"\r\n")
        return line

    def _body_to(self, fh):
        # El separador real es CRLF + "--boundary": asi un archivo que
        # contenga "--boundary" en su contenido no se corta.
        sep = b"\r\n" + self.delim
        keep = len(sep) + 2
        size = 0
        while True:
            idx = self.buf.find(sep)
            if idx != -1:
                fh.write(self.buf[:idx])
                size += idx
                self.buf = self.buf[idx + len(sep):]
                return size
            if len(self.buf) > keep:
                chunk = self.buf[:-keep]
                fh.write(chunk)
                size += len(chunk)
                self.buf = self.buf[-keep:]
            if not self._fill():
                fh.write(self.buf)
                size += len(self.buf)
                self.buf = b""
                return size

    def parts(self):
        """Genera (filename, size, tmp_path) escribiendo cada parte a disco."""
        line = self._line()
        while line and not line.startswith(self.delim):
            line = self._line()          # preambulo
        if not line:
            return
        while True:
            if line[len(self.delim):].startswith(b"--"):
                return
            cd = ""
            while True:
                ln = self._line()
                if not ln:
                    break
                k, _, v = ln.partition(b":")
                if k.strip().lower() == b"content-disposition":
                    cd = v.strip().decode("utf-8", "ignore")
            fn = ""
            if 'filename="' in cd:
                fn = cd.split('filename="', 1)[1].split('"', 1)[0]
            tmp = tempfile.NamedTemporaryFile(
                delete=False, dir=str(ARCHIVOS), prefix=".subiendo_", suffix=".part"
            )
            with tmp:
                size = self._body_to(tmp)
            yield fn, size, Path(tmp.name)
            if self.buf.startswith(b"--"):
                return
            if self.buf.startswith(b"\r\n"):
                self.buf = self.buf[2:]
            line = self.delim


PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<link rel="icon" href="/logo.png">
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
<div class="badge" id="badge">EN LÍNEA</div>
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
<div class="card"><h2>MIS ARCHIVOS EN EL SERVIDOR</h2>
<div id="mine">—</div>
<p style="color:var(--mut);font-size:12px;margin-top:8px">Se borran solos al descargarlos o a las 2 h.</p></div>
<div class="card"><h2>PETICIONES PARA TI <span style="font-size:12px;color:var(--mut)">(acepta para iniciar la transferencia)</span></h2><div id="inbox">—</div></div>
<div class="card"><h2>TUS PETICIONES ENVIADAS</h2><div id="outbox">—</div></div>
</main>
<footer>PAPU-LAN · <span class="sticker">SEGUIDORES DE LA GRASA</span> · practica de Sistemas Operativos · red local</footer>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let YO=localStorage.getItem('papu_num')||'',NOMBRE=localStorage.getItem('papu_nom')||'',FILES=[],OFFERS=[];
function badge(txt,ok){const b=document.getElementById('badge');if(!b)return;b.textContent=txt;b.style.background=ok?'var(--acc2)':'#3a3a3a';b.style.color=ok?'#fff':'var(--mut)'}
async function entrar(){const n=document.getElementById('linum').value.trim();if(!n){alert('Pon tu número de usuario');return}
const nm=document.getElementById('linom').value.trim();
const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({numero:n,nombre:nm})});
const d=await r.json();if(!d.ok){alert('No se pudo entrar');return}
YO=d.numero;NOMBRE=d.nombre||nm;localStorage.setItem('papu_num',YO);localStorage.setItem('papu_nom',NOMBRE);
verSesion();cargarTodo()}
function salir(){localStorage.removeItem('papu_num');localStorage.removeItem('papu_nom');YO='';NOMBRE='';verSesion()}
function verSesion(){document.getElementById('login').style.display=YO?'none':'flex';
document.getElementById('meinfo').textContent=YO?('USUARIO '+YO+(NOMBRE?' · '+NOMBRE:'')):'Sin sesión';
if(YO){document.getElementById('linum').value=YO;document.getElementById('linom').value=NOMBRE||''}}
async function cargarTodo(){verSesion();if(!YO)return;cargarFiles();cargarConn();cargarOffers()}
async function cargarConn(){try{const r=await fetch('/api/info');const d=await r.json();
window._url='http://'+d.ip+':'+d.port;
document.getElementById('conn').innerHTML='En tu celular abre:<br><b>'+esc(window._url)+'</b><br><span style="color:var(--mut);font-size:12px">'+esc(d.host)+'</span>'}catch(e){badge('SIN CONEXIÓN',false)}}
function avisoCopiado(){const b=document.querySelector('.copy');if(!b)return;const t=b.textContent;b.textContent='✓ DIRECCIÓN COPIADA';setTimeout(()=>{b.textContent=t},1600)}
function copiar(){const url=window._url||'';
const manual=()=>{try{const ta=document.createElement('textarea');ta.value=url;ta.setAttribute('readonly','');ta.style.position='fixed';ta.style.top='-1000px';document.body.appendChild(ta);ta.select();ta.setSelectionRange(0,99999);const ok=document.execCommand('copy');document.body.removeChild(ta);if(ok){avisoCopiado();return}}catch(e){}
if(url){prompt('Copia la dirección:',url)}};
if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(url).then(avisoCopiado,manual);return}
manual()}
async function cargarFiles(){if(!YO)return;try{const r=await fetch('/api/files?user='+encodeURIComponent(YO));FILES=await r.json();
document.getElementById('selFile').innerHTML=FILES.map(f=>`<option value="${esc(f.name)}">${esc(f.name)} (${esc(f.size_h)})</option>`).join('')||'<option value="">(sube un archivo primero)</option>';
document.getElementById('mine').innerHTML=FILES.length?FILES.map((f,i)=>`<div class="fila"><span class="ico">&#128196;</span><span class="n">${esc(f.name)}</span><span class="s">${esc(f.size_h)} · ${esc(f.date)}</span><div class="acc"><button class="danger" data-i="${i}">ELIMINAR</button></div></div>`).join(''):'<p style="color:var(--mut);font-size:13px">Aún no has subido archivos.</p>';badge('EN LÍNEA',true)}catch(e){badge('SIN CONEXIÓN',false)}}
async function borrar(n){if(!confirm('¿Eliminar "'+n+'" del servidor?'))return;
const r=await fetch('/api/files/'+encodeURIComponent(n)+'?by='+encodeURIComponent(YO),{method:'DELETE'});
const d=await r.json();if(!d.ok)alert('No se pudo eliminar: '+(d.error||'error'));cargarFiles()}
async function cargarOffers(){if(!YO)return;try{const r=await fetch('/api/offers?user='+encodeURIComponent(YO));OFFERS=await r.json();
const inc=OFFERS.filter(o=>o.to===YO),out=OFFERS.filter(o=>o.from===YO);
document.getElementById('inbox').innerHTML=inc.length?inc.map(o=>{
if(o.status==='pendiente')return `<div class="off pend"><div class="t2">${esc(o.file)}</div><div class="d">De <b>${esc(o.from)}</b> · ${esc(o.fecha)} · pendiente tu respuesta</div><div class="btns"><button onclick="resp('${esc(o.id)}',true)">ACEPTAR E INICIAR TRANSFERENCIA</button><button class="ghost" onclick="resp('${esc(o.id)}',false)">RECHAZAR</button></div></div>`;
if(o.status==='aceptada')return `<div class="off ok"><div class="t2">${esc(o.file)}</div><div class="d">De ${esc(o.from)} · aceptada · la transferencia está en curso</div><div class="btns"><a href="/download/${encodeURIComponent(o.file)}?by=${encodeURIComponent(YO)}" style="flex:1;text-align:center;background:var(--acc);color:#000;font-family:Arial Black,Arial,sans-serif;text-decoration:none;border:3px solid #000;border-radius:8px;padding:13px">DESCARGAR AHORA (SE BORRA AL DESCARGAR)</a></div></div>`;
if(o.status==='completada')return `<div class="off ok"><div class="t2">${esc(o.file)}</div><div class="d">De ${esc(o.from)} · ya se descargó y se borró del servidor</div></div>`;
if(o.status==='expirada')return `<div class="off no"><div class="t2">${esc(o.file)}</div><div class="d">De ${esc(o.from)} · la petición expiró (30 min), pide que te la manden de nuevo</div></div>`;
return `<div class="off no"><div class="t2">${esc(o.file)}</div><div class="d">De ${esc(o.from)} · ${esc(o.status)}</div></div>`}).join(''):'<p style="color:var(--mut)">Sin peticiones por ahora.</p>';
document.getElementById('outbox').innerHTML=out.length?out.map(o=>{
let extra=o.status==='aceptada'?` · <a href="/download/${encodeURIComponent(o.file)}?by=${encodeURIComponent(YO)}" style="color:var(--acc)">descargar</a>`:'';
return `<div class="off ${o.status==='pendiente'?'pend':o.status==='aceptada'?'ok':'no'}"><div class="t2">${esc(String(o.status).toUpperCase())} · ${esc(o.file)}</div><div class="d">Para <b>${esc(o.to)}</b> · ${esc(o.fecha)}${extra}</div></div>`}).join(''):'<p style="color:var(--mut)">Aún no envías peticiones.</p>';badge('EN LÍNEA',true)}catch(e){badge('SIN CONEXIÓN',false)}}
async function resp(id,ok){await fetch('/api/offers/'+encodeURIComponent(id)+(ok?'/accept':'/reject'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user:YO})});cargarOffers()}
async function pedir(){const f=document.getElementById('selFile').value;const t=document.getElementById('dest').value.trim();
if(!YO){alert('Entra primero con tu número');return}if(!f){alert('Sube o elige un archivo');return}if(!t){alert('Escribe el número destino');return}
const r=await fetch('/api/offers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from:YO,to:t,file:f})});
const d=await r.json();const st=document.getElementById('st2');
st.textContent=d.ok?('Petición enviada a '+t+(d.repetida?' (ya existía)':'')):'Error: '+(d.error||'Error');
st.style.color=d.ok?'var(--mut)':'#ff8080';cargarOffers()}
async function subir(){const inp=document.getElementById('file');if(!inp.files.length){document.getElementById('st').textContent='Elige al menos un archivo.';return}
if(!YO){alert('Entra primero con tu número');return}
const fd=new FormData();for(const f of inp.files)fd.append('f',f,f.name);
const st=document.getElementById('st');st.textContent='Subiendo…';st.style.color='var(--mut)';
try{const r=await fetch('/api/upload?from='+encodeURIComponent(YO),{method:'POST',body:fd});
const d=await r.json();
st.style.color=d.ok?'var(--mut)':'#ff8080';
st.textContent=(d.ok?('OK: '+d.saved.join(', ')):'Error: '+(d.error||'Error al subir'))+(d.aviso?' · '+d.aviso:'');badge('EN LÍNEA',true)}
catch(e){st.style.color='#ff8080';st.textContent='Error de red al subir';badge('SIN CONEXIÓN',false)}
inp.value='';cargarFiles()}
const drop=document.getElementById('drop');if(drop){
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('over')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('over')}));
drop.addEventListener('drop',ev=>{if(ev.dataTransfer.files.length){document.getElementById('file').files=ev.dataTransfer.files;subir()}})}
document.getElementById('mine').addEventListener('click',ev=>{const b=ev.target.closest('button[data-i]');if(b)borrar(FILES[+b.dataset.i].name)});
verSesion();if(YO)cargarTodo();setInterval(()=>{if(YO){cargarOffers();cargarFiles()}},5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PapuLAN/2.0"
    timeout = 30          # corta clientes colgados sin tumbar al resto

    def log_message(self, fmt, *args):
        try:
            sys.stdout.write(f"[{datetime.now():%H:%M:%S}] {fmt % args}\n")
        except Exception:
            pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    # ---------- utilidades ----------
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n <= 0 or n > MAX_JSON_BYTES:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---------- GET ----------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)

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
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        elif path == "/api/info":
            self._json({"ip": get_lan_ip(), "port": self.server.server_port,
                        "host": socket.gethostname()})

        elif path == "/api/files":
            me = clean_num(qs.get("user", [""])[0])
            if not me:
                return self._json({"ok": False, "error": "falta ?user="}, 400)
            meta = _load_json(FILES_META, {})
            out = []
            for f in ARCHIVOS.iterdir():
                if not f.is_file() or f.name.startswith("."):
                    continue
                if meta.get(f.name, {}).get("from") != me:
                    continue
                st = f.stat()
                out.append({"name": f.name, "size": st.st_size,
                            "size_h": fmt_size(st.st_size),
                            "date": datetime.fromtimestamp(st.st_mtime).strftime("%d/%m %H:%M"),
                            "from": me})
            out.sort(key=lambda x: x["name"])
            self._json(out)

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
            if not by:
                return self._json({"ok": False, "error": "falta ?by=<numero>"}, 400)
            meta = _load_json(FILES_META, {})
            owner = meta.get(name, {}).get("from", "")
            permitido = by == owner
            if not permitido:
                offers = _load_json(OFFERS_FILE, [])
                permitido = any(o.get("file") == name and o.get("to") == by
                                and o.get("status") == "aceptada" for o in offers)
            if not permitido:
                return self._json(
                    {"ok": False, "error": "no tienes una petición aceptada para este archivo"}, 403)

            size = fp.stat().st_size
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            fallback = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{fallback}\"; "
                f"filename*=UTF-8''{urllib.parse.quote(name, safe='')}")
            self.end_headers()
            with open(fp, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 64 * 1024)
            # Autodestruccion: solo si lo baja alguien distinto del dueno.
            if by != owner:
                _oneshot_delete(name)

        else:
            self.send_error(404)

    # ---------- POST ----------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path, qs = u.path, urllib.parse.parse_qs(u.query)

        if path == "/api/login":
            d = self._body_json()
            num = clean_num(d.get("numero", ""))
            if not num:
                return self._json({"ok": False, "error": "numero requerido"}, 400)
            nom = str(d.get("nombre", ""))[:40].strip()
            return self._json({"ok": True, "numero": num, "nombre": nom})

        if path == "/api/upload":
            frm = clean_num(qs.get("from", [""])[0])
            if not frm:
                return self._json({"ok": False, "error": "falta ?from=<numero>"}, 400)
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype or "boundary=" not in ctype:
                return self._json({"ok": False, "error": "multipart requerido"}, 400)
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return self._json({"ok": False, "error": "Content-Length requerido"}, 411)

            mp = _Multipart(self.rfile, boundary, length)
            pendientes, rechazados, motivos = [], [], []
            try:
                for raw_name, size, tmp in mp.parts():
                    fn = safe_name(urllib.parse.unquote(raw_name))
                    if not fn:
                        tmp.unlink(missing_ok=True)
                        continue
                    if size == 0:
                        tmp.unlink(missing_ok=True)
                        rechazados.append(fn)
                        motivos.append(f"{fn}: archivo vacío")
                        continue
                    if size > MAX_BYTES:
                        tmp.unlink(missing_ok=True)
                        rechazados.append(fn)
                        motivos.append(f"{fn}: supera {MAX_FILE_MB} MB")
                        continue
                    with LOCK:
                        dest = _unique_dest(fn)
                        try:
                            os.replace(tmp, dest)
                            os.chmod(dest, 0o644)
                        except Exception:
                            tmp.unlink(missing_ok=True)
                            raise
                    pendientes.append(dest)
            except Exception:
                for d in pendientes:
                    d.unlink(missing_ok=True)
                return self._json({"ok": False, "error": "cuerpo multipart inválido"}, 400)

            saved = []
            with LOCK:
                meta = _load_json(FILES_META, {})
                propios = sum(1 for m in meta.values() if m.get("from") == frm)
                for dest in pendientes:
                    if propios >= MAX_FILES_PER_USER:
                        dest.unlink(missing_ok=True)
                        rechazados.append(dest.name)
                        motivos.append(f"{dest.name}: límite {MAX_FILES_PER_USER} archivos/usuario")
                        continue
                    meta[dest.name] = {"from": frm, "ts": time.time()}
                    propios += 1
                    saved.append(dest.name)
                _save_json(FILES_META, meta)

            r = {"ok": bool(saved), "saved": saved}
            if rechazados:
                r["rechazados"] = rechazados
                r["aviso"] = "; ".join(motivos)
            if not saved and not rechazados:
                r["error"] = "no se recibió ningún archivo"
            return self._json(r, 200 if saved else 400)

        if path == "/api/offers":
            d = self._body_json()
            frm, to = clean_num(d.get("from", "")), clean_num(d.get("to", ""))
            fn = safe_name(str(d.get("file", "")))
            if not frm or not to or not fn:
                return self._json({"ok": False, "error": "from/to/file requeridos"}, 400)
            if frm == to:
                return self._json({"ok": False, "error": "no puedes enviarte a ti mismo"}, 400)
            with LOCK:
                meta = _load_json(FILES_META, {})
                if not (ARCHIVOS / fn).is_file():
                    return self._json({"ok": False, "error": "archivo no existe"}, 404)
                if meta.get(fn, {}).get("from") != frm:
                    return self._json({"ok": False, "error": "ese archivo no es tuyo"}, 403)
                offers = _load_json(OFFERS_FILE, [])
                for o in offers:
                    if (o.get("file") == fn and o.get("from") == frm
                            and o.get("to") == to and o.get("status") == "pendiente"):
                        return self._json({"ok": True, "id": o["id"], "repetida": True})
                pend = sum(1 for o in offers
                           if o.get("from") == frm and o.get("status") == "pendiente")
                if pend >= MAX_OFFERS_PER_USER:
                    return self._json(
                        {"ok": False,
                         "error": f"límite: {MAX_OFFERS_PER_USER} peticiones pendientes"},
                        429)
                oid = secrets.token_hex(4)
                offers.append({"id": oid, "from": frm, "to": to, "file": fn,
                               "status": "pendiente", "ts": time.time(),
                               "fecha": datetime.now().strftime("%d/%m %H:%M")})
                _save_json(OFFERS_FILE, offers[-OFFERS_KEEP:])
            return self._json({"ok": True, "id": oid})

        if path.startswith("/api/offers/") and (path.endswith("/accept") or path.endswith("/reject")):
            oid = path[len("/api/offers/"):].rsplit("/", 1)[0]
            d = self._body_json()
            me = clean_num(d.get("user", ""))
            aceptar = path.endswith("/accept")
            with LOCK:
                offers = _load_json(OFFERS_FILE, [])
                ok = False
                for o in offers:
                    if o.get("id") == oid and o.get("to") == me and o.get("status") == "pendiente":
                        o["status"] = "aceptada" if aceptar else "rechazada"
                        ok = True
                if ok:
                    _save_json(OFFERS_FILE, offers[-OFFERS_KEEP:])
            return self._json({"ok": ok})

        self.send_error(404)

    # ---------- DELETE ----------
    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if not u.path.startswith("/api/files/"):
            return self.send_error(404)
        name = safe_name(urllib.parse.unquote(u.path[len("/api/files/"):]))
        by = clean_num(qs.get("by", [""])[0])
        with LOCK:
            meta = _load_json(FILES_META, {})
            owner = meta.get(name, {}).get("from", "")
            if not by or by != owner:
                return self._json({"ok": False, "error": "solo el dueño puede borrarlo"}, 403)
            fp = ARCHIVOS / name
            if not fp.is_file():
                return self._json({"ok": False, "error": "no existe"}, 404)
            fp.unlink(missing_ok=True)
            meta.pop(name, None)
            _save_json(FILES_META, meta)
            offers = _load_json(OFFERS_FILE, [])
            for o in offers:
                if o.get("file") == name and o.get("status") in ("pendiente", "aceptada"):
                    o["status"] = "cancelada"
            _save_json(OFFERS_FILE, offers[-OFFERS_KEEP:])
        return self._json({"ok": True})


def main():
    ap = argparse.ArgumentParser(description="Papu-LAN: archivos en red local")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dir", default=None, help="carpeta de archivos (default ./archivos)")
    ap.add_argument("--data", default=None, help="carpeta de metadatos (default ./data)")
    ap.add_argument("--ip", default=None, help="IP que se muestra a los demas")
    a = ap.parse_args()

    init_dirs(a.dir, a.data)
    ip = get_lan_ip(a.ip)
    threading.Thread(target=_sweep_loop, daemon=True).start()

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    except OSError as e:
        print(f"No se pudo abrir el puerto {a.port}: {e}")
        print("Prueba con otro: python3 app.py --port 8080")
        return 1

    print("=" * 50)
    print("  PAPU-LAN funcionando")
    print(f"  Este equipo : http://localhost:{a.port}")
    print(f"  Red local   : http://{ip}:{a.port}")
    otras = [x for x in all_lan_ips() if x != ip]
    for x in otras:
        print(f"  Tambien     : http://{x}:{a.port}")
    print(f"  Carpeta     : {ARCHIVOS}")
    print(f"  Datos       : {DATA}")
    print("  Ctrl+C para detener")
    print("=" * 50)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nPapu-LAN detenido.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
