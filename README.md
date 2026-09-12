# PAPU-LAN · SDLG

Intercambio de archivos entre usuarios en la red local (laboratorio / aula),
sin nube ni internet. Estetica de los Seguidores de la Grasa: escudo rojo y
negro, pacman amarillo.

![SDLG](logo.png)

## Requisitos

- Python 3. Sin librerias externas.

## Uso

```bash
python3 app.py --port 8000
```

- Este equipo: http://localhost:8000
- Otros equipos (mismo Wi-Fi): http://TU-IP:8000 (la pagina la muestra)

## Flujo

1. Entras con tu numero de usuario (por ahora no se valida que exista).
2. Subes el archivo que quieres mandar.
3. Buscas a la persona por numero o nombre.
4. Le mandas la peticion de intercambio.
5. El otro la acepta y se habilita la descarga (transferencia).

## Limites

- 100 MB maximo por archivo.
- 10 archivos maximo por usuario.
- El archivo se borra solo al ser descargado, o en 2 horas si nadie lo baja.
- La peticion pendiente expira en 30 minutos.

## Estructura

```
papu-lan/
  app.py        servidor + pagina web (todo en uno)
  logo.png      escudo SDLG
  iniciar.sh    arranque rapido
  archivos/     archivos subidos (se ignoran en git)
  data/         usuarios, peticiones y mensajes en JSON (se ignoran en git)
```
