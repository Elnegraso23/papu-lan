<p align="center">
  <img src="logo.png" width="230" alt="Seguidores de la Grasa - SDLG">
</p>

<h1 align="center">PAPU-LAN</h1>

<p align="center">
  <b>SEGUIDORES DE LA GRASA · SDLG</b><br>
  Intercambio de archivos entre usuarios en la red local. Sin nube, sin internet.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white" alt="Python 3.11">
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
| 1 | Entras con tu numero de usuario (por ahora no se valida que exista) |
| 2 | Subes el archivo que quieres mandar |
| 3 | Escribes el numero del destinatario y mandas la peticion |
| 4 | El otro la acepta y se habilita la descarga |
| 5 | Al descargar, el archivo se autodestruye en el servidor |

## Limites

| Limite | Valor |
| ------ | ----- |
| Tamano maximo por archivo | 100 MB |
| Archivos maximos por usuario | 10 |
| Archivo sin descargar | se borra en 2 horas |
| Peticion sin responder | expira en 30 minutos |

## Uso

```bash
python3 app.py --port 8000
```

- Este equipo: http://localhost:8000
- Otros equipos (mismo Wi-Fi): http://TU-IP:8000 (la pagina muestra la direccion)

## Estructura

```
papu-lan/
  app.py        servidor + pagina web, todo en uno
  logo.png      escudo SDLG
  iniciar.sh    arranque rapido
  archivos/     archivos subidos (no se suben a git)
  data/         usuarios, peticiones y archivos en JSON (no se suben a git)
```

## Practica escolar

Proyecto de Sistemas Operativos: servicio en red local, protocolo
peticion-respuesta sobre HTTP y limpieza automatica de recursos
(autodestruccion por descarga y por tiempo).
