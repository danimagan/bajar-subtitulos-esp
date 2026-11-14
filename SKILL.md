---
name: bajar-subtitulos-esp
description: Descarga subtítulos en castellano (español de España, no latino) para series o películas en inglés que el usuario tiene en una carpeta. Úsala cuando pida "bajar subtítulos", "subtítulos en castellano/español" para una carpeta o vídeo.
---

# Bajar subtítulos en castellano

El usuario tiene series y películas en inglés y quiere subtítulos en **castellano**
(español de España). El subtítulo principal, sea del idioma que sea, se guarda como
**`<video>.srt`** (mismo nombre que el vídeo, sin `.es`), porque así lo cargan solos más
reproductores. Si solo hay latino, se guarda igualmente, pero el RESUMEN debe dejar claro
que es latino (`LATINO ...`) y hay que decírselo. Si no hay ni castellano ni latino, se bajan
subtítulos en **inglés** (primero la pista inglesa incrustada, prefiriendo SDH; si no, de los
proveedores), y el RESUMEN lo marca como `INGLÉS ...`. Preferencia: castellano > latino >
inglés; el script nunca sustituye un `.srt` por otro de un tipo peor (ni con `--force`).

Argumento: la ruta de una carpeta (se busca recursivamente) o de un vídeo. Si no la da, pregúntala.

## Entorno

Todas las rutas son relativas a la carpeta personal del usuario (`~`; en Windows,
`%USERPROFILE%`), así que funcionan en cualquier equipo sin tocar nada.

- Script: `bajar_subs.py`, en la carpeta base de esta skill (la ruta que aparece como
  "Base directory for this skill" al cargarla). Abajo se escribe `<skill>`.
- Venv permanente: `~/.subtitulos-esp/venv` (con `subliminal` 2.8). Si no existe, créalo:
  - Windows: `python -m venv "$HOME\.subtitulos-esp\venv"` y
    `& "$HOME\.subtitulos-esp\venv\Scripts\pip.exe" install -r "<skill>\requirements.txt"`
  - Linux/macOS: `python3 -m venv ~/.subtitulos-esp/venv` y
    `~/.subtitulos-esp/venv/bin/pip install -r "<skill>/requirements.txt"`
- Credenciales opensubtitles.org (opcional, pero da más resultados):
  `~/.subtitulos-esp/credenciales.json` con
  `{"opensubtitles": {"username": "<usuario>", "password": "<contraseña>"}}`.
  Claude no debe escribir la contraseña: si falta el archivo, el script funciona sin
  OpenSubtitles; díselo al usuario y que lo cree él.
- Copias de los `.srt` que se sustituyen: `~/.subtitulos-esp/backup`.

## Pasos

1. Ejecuta el script con el Python del venv, filtrando el ruido del refinador omdb (da 401
   siempre):
   - Windows (PowerShell):
     ```
     & "$HOME\.subtitulos-esp\venv\Scripts\python.exe" -I "<skill>\bajar_subs.py" "<ruta>" 2>&1 | Where-Object { $_ -notmatch 'omdb|Traceback|^\s+File |raise |HTTPError|^\s+\^|^\s+results' }
     ```
   - Linux/macOS:
     ```
     ~/.subtitulos-esp/venv/bin/python -I "<skill>/bajar_subs.py" "<ruta>" 2>&1 | grep -vE 'omdb|Traceback|^\s+File |raise |HTTPError|^\s+\^|^\s+results'
     ```
   Opciones: `--dry-run` (no escribe), `--force` (rehace aunque ya haya castellano),
   `--solo-castellano` (no guarda latino aunque no haya castellano), `--sin-ingles` (no baja
   inglés cuando no hay nada en español), `--ingles` (además extrae la pista inglesa incrustada,
   prefiriendo SDH, como `.en.srt` adicional). Para carpetas grandes usa timeout de 10 min o
   background.
2. **Primero mira dentro del vídeo**: en `.mkv` y en `.mp4`/`.m4v`/`.mov` el script lee las
   pistas de subtítulos de texto sin ffmpeg ni mkvtoolnix (MKV: parser EBML propio que salta
   vídeo/audio, ~3 s para 3 GB; MP4: lee solo la caja `moov` y salta a las muestras de
   subtítulos `tx3g`/WebVTT, ~1 s para 10 GB). Si hay una pista en español que no esté marcada
   como latina (`es-419`, `es-MX`, nombre con "LA"/"Latino"...) ni "forced", y el detector de
   dialecto confirma castellano, la extrae y no busca en internet (RESUMEN:
   `CASTELLANO (incrustado)`). Si no, busca en los proveedores; si allí tampoco hay
   castellano, prefiere la pista latina incrustada (va sincronizada) a un latino de internet.
3. El script guarda `<video>.srt` en UTF-8 junto al vídeo. Si encuentra un `.es.srt` o
   `.en.srt` del formato antiguo y no hay `.srt`, lo renombra a `<video>.srt`.
4. Revisa el RESUMEN y díselo al usuario indicando el tipo de cada uno:
   `CASTELLANO`, `CASTELLANO (incrustado)`, `LATINO`, `LATINO (incrustado)`, `INGLÉS`,
   `INGLÉS (incrustado)`, `ya tenía castellano`, `LATINO (ya tenía .srt)`,
   `INGLÉS (ya tenía .srt)` o `NO ENCONTRADO` (ni español ni inglés). El script detecta el
   idioma del `.srt` que ya hay leyendo el texto: si es latino o inglés, vuelve a buscar
   castellano en cada ejecución y lo sustituye cuando aparece (suele ser días después del
   estreno, sobre todo en Addic7ed).
5. Si el usuario dice que un subtítulo va desfasado, compara los tiempos de las mismas frases
   con otro candidato que sí cuadre y desplaza todos los tiempos lo necesario.

## Lecciones aprendidas (no repetir errores)

- **Los WEB-DL de las plataformas de streaming suelen traer ~30 pistas de subtítulos
  incrustadas**, con castellano (`es-ES`, "español de España") incluido. El día del estreno no
  hay nada en internet, pero el vídeo ya lo trae. Por eso el script mira las pistas antes que
  los proveedores.
- **También los MP4** (pistas `tx3g`). El idioma está en `mdhd` (`spa`) y a menudo la variante
  solo aparece en el nombre de la pista (`udta/name`, p. ej. "... ES" / "... LA"), así que se
  mira el nombre: "LA"/"Latino" = latino, "ES"/"España" = castellano.
- **En las pistas incrustadas, la etiqueta latina manda sobre el detector de dialecto**: una
  pista latina mal troceada llegó a dar españa=26 latino=11 y habría pasado por castellano.
  Las etiquetas de las pistas oficiales de las plataformas son fiables; las de las webs no.
- **El nombre de la carpeta puede romper la búsqueda.** `scan_video` aplica guessit a la ruta
  entera: con una carpeta tipo `<SERIE> - FULL` la serie salía como "<SERIE> FULL" y ningún
  proveedor devolvía nada en español (todo acababa en inglés). El script ahora toma la serie
  del nombre del archivo. Si todos salen en inglés sin candidatos en español, sospecha esto.
- **Las etiquetas de idioma mienten.** En OpenSubtitles se han visto latinos marcados como
  "es" (castellano) y un "castellano" del E05 subido como E06. Por eso el script decide el
  dialecto leyendo el texto: castellano = `vosotros/os/vale/chaval/leches/-áis/-éis...`;
  latino = `ustedes/computadora/celular/carro/enojado/ahorita...`.
- **Comprueba episodio y duración**: el script descarta subtítulos cuyo nombre indica otro
  episodio o cuyo último tiempo no encaja con la duración del vídeo.
- **Addic7ed** es la mejor fuente de castellano para series y funciona sin cuenta
  (ofrece dos versiones en español; la de castellano suele ser `updated/4/...`).
- **Se usa opensubtitles.org** (proveedor `opensubtitles` en subliminal), no opensubtitles.com:
  son cuentas distintas, y el login en .com exige nombre de usuario, no email.
- **Subtitulamos** (web española) no tiene todo el catálogo; Podnapisi/TVsubtitles suelen dar latino.
- Los archivos de OpenSubtitles .org a veces vienen en Latin-1: el script siempre guarda UTF-8.
- Dos versiones del mismo episodio (p. ej. WEB y WEBRip) suelen compartir subtítulo.
