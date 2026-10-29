<p align="center">
  <img src="logo.png" width="230" alt="Seguidores de la Grasa - SDLG">
</p>

<h1 align="center">PAPU-LAN</h1>

<p align="center">
  <b>SEGUIDORES DE LA GRASA · SDLG</b><br>
  Intercambio de archivos entre usuarios en la red local. Sin nube, sin internet.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/RED-LOCAL-D40000" alt="Red local">
  <img src="https://img.shields.io/badge/SDLG-SEGUIDORES_DE_LA_GRASA-FFDF00" alt="SDLG">
  <img src="https://img.shields.io/badge/SIN-DEPENDENCIAS-000000" alt="Sin dependencias">
</p>

---

## De que va

Aplicacion web para pasar archivos entre estudiantes dentro de la misma red
(laboratorio o aula). Un usuario sube el archivo, le manda una peticion a otro
numero de usuario, el otro la acepta y descarga. Al descargar, el archivo se
borra solo del servidor.

Todo corre en un solo archivo (`app.py`), solo con la libreria estandar de
Python. La interfaz esta pensada para el celular.

## Flujo

| Paso | Que pasa |
| ---- | -------- |
| 1 | Entras con tu numero de usuario y un nombre (queda en tu navegador) |
| 2 | Subes el archivo que quieres mandar |
| 3 | Escribes el numero del destinatario y mandas la peticion |
| 4 | El otro la acepta y se habilita la descarga |
| 5 | Al descargar, el archivo se autodestruye en el servidor |

La descarga solo se permite si existe una peticion **aceptada** para ese
archivo y ese usuario. Si nadie la acepta, el archivo no se puede bajar ni
escribiendo la URL a mano.

## Limites

| Limite | Valor |
| ------ | ----- |
| Tamano maximo por archivo | 100 MB |
| Archivos maximos por usuario | 10 |
| Peticiones pendientes por usuario | 10 |
| Archivo sin descargar | se borra en 2 horas |
| Peticion sin responder | expira en 30 minutos |

## Uso

```bash
python3 app.py --port 8000
```

Al arrancar imprime las direcciones:

- Este equipo: `http://localhost:8000`
- Otros equipos (mismo Wi-Fi): `http://TU-IP:8000`

Opciones:

```bash
python3 app.py --port 8080          # otro puerto
python3 app.py --ip 192.168.0.10    # forzar la IP que se muestra
python3 app.py --dir /ruta/archivos --data /ruta/datos
```

Tambien puedes usar `./iniciar.sh`.

## Pruebas

```bash
python3 -m unittest -v test_app.py
```

23 pruebas: subida, limites, colision de nombres, nombre malicioso, flujo
completo de peticion, autodestruccion, permisos de descarga y borrado,
concurrencia y cliente lento.

## API

| Metodo | Ruta | Que hace |
| ------ | ---- | -------- |
| GET | `/api/info` | IP, puerto y nombre del equipo |
| GET | `/api/files?user=N` | archivos del usuario N |
| GET | `/api/offers?user=N` | peticiones enviadas y recibidas por N |
| POST | `/api/upload?from=N` | sube uno o varios archivos (multipart) |
| POST | `/api/offers` | crea una peticion `{from,to,file}` |
| POST | `/api/offers/<id>/accept` | acepta (solo el destinatario) |
| POST | `/api/offers/<id>/reject` | rechaza (solo el destinatario) |
| GET | `/download/<archivo>?by=N` | descarga y borra (si N no es el dueno) |
| DELETE | `/api/files/<archivo>?by=N` | borra (solo el dueno) |

## Seguridad

Esto es una practica escolar, no un sistema de produccion:

- El numero de usuario **identifica, no autentica**. Cualquiera puede escribir
  el numero de otro; no hay contrasenas.
- Los archivos viajan **sin cifrar** por la red local. No subas nada sensible.
- Aun asi, la API si verifica que quien descarga tenga una peticion aceptada y
  que quien borra sea el dueno del archivo.

## Si no conecta desde el celular

1. Verifica que esten en la **misma red** y que la IP sea la correcta.
2. Revisa el firewall del equipo: debe permitir el puerto (8000 por defecto).
3. Muchas redes de campus tienen **aislamiento de clientes** (cada equipo ve
   solo al router). Si es el caso, no hay forma de que funcione: usa el
   **hotspot de un celular** y conecta ambos equipos ahi.
4. Si el puerto esta ocupado, arranca con otro: `--port 8080`.

## Estructura

```
papu-lan/
  app.py        servidor + pagina web, todo en uno
  test_app.py   pruebas automaticas
  logo.png      escudo SDLG
  iniciar.sh    arranque rapido
  archivos/     archivos subidos (no se suben a git)
  data/         peticiones y metadatos en JSON (no se suben a git)
```

## Practica escolar

Proyecto de la materia de Software: servicio en red local, protocolo
peticion-respuesta sobre HTTP y limpieza automatica de recursos
(autodestruccion por descarga y por tiempo).
