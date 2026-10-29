#!/usr/bin/env python3
"""Pruebas de Papu-LAN. Ejecutar con:  python3 -m unittest -v test_app.py"""
import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import app

BOUND = "----papuTestBoundary"


def multipart(files):
    out = []
    for fn, data in files:
        out.append(f"--{BOUND}\r\n".encode())
        out.append(f'Content-Disposition: form-data; name="f"; filename="{fn}"\r\n'.encode())
        out.append(b"Content-Type: application/octet-stream\r\n\r\n")
        out.append(data)
        out.append(b"\r\n")
    out.append(f"--{BOUND}--\r\n".encode())
    return b"".join(out)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        app.init_dirs(root / "archivos", root / "data")
        self._max_bytes = app.MAX_BYTES
        self._max_files = app.MAX_FILES_PER_USER
        self._max_offers = app.MAX_OFFERS_PER_USER
        self.srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.port = self.srv.server_port
        self._log = app.Handler.log_message
        app.Handler.log_message = lambda *a, **k: None
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def tearDown(self):
        app.Handler.log_message = self._log
        app.MAX_BYTES = self._max_bytes
        app.MAX_FILES_PER_USER = self._max_files
        app.MAX_OFFERS_PER_USER = self._max_offers
        self.srv.shutdown()
        self.srv.server_close()   # espera a que terminen los hilos
        self.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        try:
            js = json.loads(data)
        except Exception:
            js = None
        return r.status, js, data, hdrs

    def visibles(self):
        """Archivos reales: los .subiendo_*.part temporales no cuentan."""
        return [p for p in app.ARCHIVOS.iterdir() if not p.name.startswith(".")]

    def esperar(self, cond, timeout=3.0):
        """La autodestruccion ocurre despues de enviar el ultimo byte."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def subir(self, files, frm="111"):
        b = multipart(files)
        return self.req("POST", f"/api/upload?from={frm}", b,
                        {"Content-Type": f"multipart/form-data; boundary={BOUND}"})

    def pedir(self, frm, to, fn):
        return self.req("POST", "/api/offers",
                        json.dumps({"from": frm, "to": to, "file": fn}).encode(),
                        {"Content-Type": "application/json"})

    def responder(self, oid, user, accion):
        return self.req("POST", f"/api/offers/{oid}/{accion}",
                        json.dumps({"user": user}).encode(),
                        {"Content-Type": "application/json"})


class TestSubida(Base):
    def test_subida_ok(self):
        st, js, _, _ = self.subir([("tarea.pdf", b"contenido")])
        self.assertEqual(st, 200)
        self.assertEqual(js["saved"], ["tarea.pdf"])
        self.assertTrue((app.ARCHIVOS / "tarea.pdf").is_file())

    def test_sin_from_es_rechazado(self):
        b = multipart([("a.txt", b"x")])
        st, js, _, _ = self.req("POST", "/api/upload", b,
                                {"Content-Type": f"multipart/form-data; boundary={BOUND}"})
        self.assertEqual(st, 400)
        self.assertIn("from", js["error"])

    def test_nombre_con_html_se_sanitiza(self):
        st, js, _, _ = self.subir([("<img src=x onerror=alert(1)>.txt", b"x")])
        self.assertEqual(st, 200)
        guardado = js["saved"][0]
        self.assertNotIn("<", guardado)
        self.assertNotIn(">", guardado)
        self.assertTrue((app.ARCHIVOS / guardado).is_file())

    def test_nombre_larguisimo_no_rompe(self):
        st, js, _, _ = self.subir([("a" * 400 + ".txt", b"x")])
        self.assertEqual(st, 200)
        self.assertLessEqual(len(js["saved"][0]), app.MAX_NAME_LEN)

    def test_colision_nombres_consecutiva(self):
        for _ in range(3):
            self.subir([("a.txt", b"x")])
        nombres = sorted(p.name for p in self.visibles())
        self.assertEqual(nombres, ["a.txt", "a_1.txt", "a_2.txt"])

    def test_limite_por_archivo(self):
        app.MAX_BYTES = 1024
        st, js, _, _ = self.subir([("grande.bin", b"x" * 2000)])
        self.assertEqual(st, 400)
        self.assertEqual(js["saved"], [])
        self.assertIn("grande.bin", js["aviso"])
        self.assertFalse((app.ARCHIVOS / "grande.bin").exists())

    def test_dos_archivos_bajo_el_limite(self):
        # Antes fallaba: el limite se aplicaba a la suma de la peticion.
        app.MAX_BYTES = 1024
        st, js, _, _ = self.subir([("uno.bin", b"x" * 600), ("dos.bin", b"y" * 600)])
        self.assertEqual(st, 200)
        self.assertEqual(sorted(js["saved"]), ["dos.bin", "uno.bin"])

    def test_cuota_en_una_sola_peticion(self):
        # Antes se podian colar 11 archivos con limite de 10.
        app.MAX_FILES_PER_USER = 2
        st, js, _, _ = self.subir([("a.txt", b"1"), ("b.txt", b"2"), ("c.txt", b"3")])
        self.assertEqual(st, 200)
        self.assertEqual(len(js["saved"]), 2)
        self.assertEqual(len(self.visibles()), 2)

    def test_boundary_dentro_del_contenido(self):
        contenido = b"antes --" + BOUND.encode() + b" despues" * 100
        st, js, _, _ = self.subir([("raro.bin", contenido)])
        self.assertEqual(st, 200)
        self.assertEqual((app.ARCHIVOS / "raro.bin").read_bytes(), contenido)

    def test_archivo_vacio_se_reporta(self):
        st, js, _, _ = self.subir([("vacio.txt", b"")])
        self.assertEqual(st, 400)
        self.assertIn("vacio.txt", js["aviso"])


class TestFlujo(Base):
    def test_flujo_completo_y_autodestruccion(self):
        self.subir([("nota.txt", b"hola")], frm="111")
        st, js, _, _ = self.pedir("111", "222", "nota.txt")
        self.assertEqual(st, 200)
        oid = js["id"]

        # un tercero no puede aceptar
        st, js, _, _ = self.responder(oid, "333", "accept")
        self.assertFalse(js["ok"])

        # el destinatario si
        st, js, _, _ = self.responder(oid, "222", "accept")
        self.assertTrue(js["ok"])

        # sin identificarse no se descarga
        st, _, _, _ = self.req("GET", "/download/nota.txt")
        self.assertEqual(st, 400)
        # un tercero tampoco
        st, _, _, _ = self.req("GET", "/download/nota.txt?by=333")
        self.assertEqual(st, 403)
        # el destinatario si, y el archivo se borra
        st, _, data, hdrs = self.req("GET", "/download/nota.txt?by=222")
        self.assertEqual(st, 200)
        self.assertEqual(data, b"hola")
        self.assertIn("filename*=UTF-8", hdrs["Content-Disposition"])

        # el borrado ocurre justo despues de mandar el ultimo byte
        self.assertTrue(self.esperar(lambda: not (app.ARCHIVOS / "nota.txt").exists()))
        def completada():
            o = app._load_json(app.OFFERS_FILE, [])
            return bool(o) and o[0].get("status") == "completada"
        self.assertTrue(self.esperar(completada))

    def test_sin_peticion_aceptada_no_baja(self):
        self.subir([("x.txt", b"x")], frm="111")
        self.pedir("111", "222", "x.txt")
        st, _, _, _ = self.req("GET", "/download/x.txt?by=222")  # pendiente, no aceptada
        self.assertEqual(st, 403)
        self.assertTrue((app.ARCHIVOS / "x.txt").is_file())

    def test_dueno_descarga_sin_borrar(self):
        self.subir([("x.txt", b"x")], frm="111")
        st, _, _, _ = self.req("GET", "/download/x.txt?by=111")
        self.assertEqual(st, 200)
        self.assertTrue((app.ARCHIVOS / "x.txt").is_file())

    def test_no_puedes_pedir_archivo_ajeno(self):
        self.subir([("x.txt", b"x")], frm="111")
        st, js, _, _ = self.pedir("999", "222", "x.txt")
        self.assertEqual(st, 403)

    def test_no_puedes_enviarte_a_ti_mismo(self):
        self.subir([("x.txt", b"x")], frm="111")
        st, js, _, _ = self.pedir("111", "111", "x.txt")
        self.assertEqual(st, 400)

    def test_files_solo_muestra_los_propios(self):
        self.subir([("mio.txt", b"x")], frm="111")
        self.subir([("ajeno.txt", b"x")], frm="222")
        st, js, _, _ = self.req("GET", "/api/files?user=111")
        self.assertEqual([f["name"] for f in js], ["mio.txt"])

    def test_delete_requiere_dueno(self):
        self.subir([("x.txt", b"x")], frm="111")
        st, _, _, _ = self.req("DELETE", "/api/files/x.txt")
        self.assertEqual(st, 403)
        st, _, _, _ = self.req("DELETE", "/api/files/x.txt?by=222")
        self.assertEqual(st, 403)
        self.assertTrue((app.ARCHIVOS / "x.txt").is_file())
        st, _, _, _ = self.req("DELETE", "/api/files/x.txt?by=111")
        self.assertEqual(st, 200)
        self.assertFalse((app.ARCHIVOS / "x.txt").exists())

    def test_nombre_muy_largo_no_tumba_la_conexion(self):
        st, _, _, _ = self.req("GET", "/download/" + "a" * 400 + ".txt?by=1")
        self.assertEqual(st, 404)


class TestConcurrencia(Base):
    def test_subidas_simultaneas_no_corrompen_json(self):
        errores = []

        def uno(i):
            try:
                st, js, _, _ = self.subir([(f"f{i}.txt", b"x" * 2048)], frm=f"user{i}")
                if st != 200:
                    errores.append((i, st, js))
            except Exception as e:
                errores.append((i, repr(e)))

        hilos = [threading.Thread(target=uno, args=(i,)) for i in range(8)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual(errores, [])
        meta = app._load_json(app.FILES_META, None)
        self.assertIsInstance(meta, dict)
        self.assertEqual(len(meta), 8)
        self.assertEqual(len(self.visibles()), 8)

    def test_cliente_lento_no_bloquea_a_los_demas(self):
        # Un cliente que promete un cuerpo enorme y no lo manda.
        s = socket.create_connection(("127.0.0.1", self.port))
        s.sendall(b"POST /api/upload?from=9 HTTP/1.1\r\nHost: x\r\n"
                  b"Content-Type: multipart/form-data; boundary=BB\r\n"
                  b"Content-Length: 50000000\r\n\r\n--BB\r\n")
        try:
            t0 = time.time()
            st, _, _, _ = self.req("GET", "/api/info")
            self.assertEqual(st, 200)
            self.assertLess(time.time() - t0, 2.0, "el servidor se bloqueo con un cliente lento")
        finally:
            s.close()
            time.sleep(0.3)   # deja terminar al hilo del cliente lento


class TestSeguridad(Base):
    def test_descarga_de_archivo_inexistente(self):
        st, _, _, _ = self.req("GET", "/download/noexiste.txt?by=1")
        self.assertEqual(st, 404)

    def test_path_traversal_no_sale_de_la_carpeta(self):
        self.subir([("ok.txt", b"x")], frm="111")
        st, _, _, _ = self.req("GET", "/download/..%2F..%2Fapp.py?by=111")
        self.assertEqual(st, 404)

    def test_json_gigante_no_se_lee(self):
        st, js, _, _ = self.req("POST", "/api/login", b"x" * (app.MAX_JSON_BYTES + 10),
                                {"Content-Type": "application/json"})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
