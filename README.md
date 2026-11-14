# bajar-subtitulos-esp

Skill para [Claude Code](https://claude.com/claude-code) que consigue **subtítulos en castellano
(español de España)** para películas y series en inglés, con un script de Python que hace todo
el trabajo de forma autónoma.

Basta con pedirle a Claude *"baja subtítulos"* en una carpeta: busca todos los vídeos,
mira primero si el propio archivo trae subtítulos en castellano, si no los busca en internet,
**comprueba leyendo el texto** que son castellano de España (y no latino mal etiquetado) y deja
un `.srt` junto a cada vídeo con su mismo nombre, listo para que el reproductor lo cargue solo.

## Características

- **Castellano de España, no latino.** Las etiquetas de idioma de las webs no son fiables
  (latinos marcados como "es", episodios mal subidos). El script decide el dialecto por el
  vocabulario: `vosotros`, `os`, `vale`, `chaval`, `-áis/-éis`… frente a `ustedes`,
  `computadora`, `celular`, `carro`, `ahorita`…
- **Primero mira dentro del vídeo.** Los WEB-DL de Disney+, Netflix, etc. suelen traer
  ~30 pistas de subtítulos incrustadas, castellano incluido, incluso el día del estreno, cuando
  aún no hay nada en internet. El script las lee **sin ffmpeg ni mkvtoolnix**:
  - **MKV**: parser EBML propio que salta vídeo y audio (~3 s para 3 GB).
  - **MP4 / M4V / MOV**: lee solo la caja `moov` y salta directamente a las muestras de
    subtítulos `tx3g` o WebVTT (~1 s para 10 GB). Respeta listas de edición y excluye pistas de
    capítulos.
  - Ignora pistas "forced" y distingue variantes por etiqueta (`es-ES`, `es-419`, nombres
    como "LA", "Latino", "España"…). En pistas oficiales la etiqueta latina prevalece sobre
    el detector de dialecto.
- **Varios proveedores** vía [subliminal](https://github.com/Diaoul/subliminal): OpenSubtitles
  (.org, con cuenta opcional), Addic7ed, Gestdown, Podnapisi, Subtitulamos y TVsubtitles.
  Descarga todos los candidatos en español y se queda con el mejor castellano.
- **Validación de cada candidato**: descarta subtítulos de otro episodio (según el nombre del
  release) o cuya duración no encaja con la del vídeo.
- **Plan B escalonado**: castellano › latino › inglés. Si solo hay latino, lo guarda (prefiriendo
  la pista incrustada, que va perfectamente sincronizada) y lo avisa claramente. Si no hay nada
  en español, baja inglés (primero la pista incrustada, prefiriendo SDH).
- **Nombre listo para el reproductor**: guarda `<video>.srt` en UTF-8, con el mismo nombre que
  el vídeo, para que VLC, MPC-HC, la tele, Plex… lo carguen automáticamente.
- **Re-ejecutable sin miedo**: detecta el idioma del `.srt` que ya existe; si es castellano no
  hace nada, si es latino o inglés vuelve a buscar castellano (suele aparecer días después del
  estreno). **Nunca sustituye un subtítulo por otro de un tipo peor**, y guarda copia de lo que
  reemplaza en `~/.subtitulos-esp/backup`.
- **Robusto con nombres raros**: toma el nombre de la serie del archivo y no de la carpeta
  (una carpeta `<SERIE> - FULL` hacía que la serie se buscara como "<SERIE> FULL").
- **Migra el formato antiguo**: renombra los `.es.srt` / `.en.srt` de versiones anteriores a
  `<video>.srt`.

## Cómo funciona

```
para cada vídeo de la carpeta (recursivo, ignora "sample"):
  1. ¿Ya hay <video>.srt en castellano?  → no hace nada
  2. Pistas incrustadas (MKV/MP4)        → si hay castellano, lo extrae y termina
  3. Proveedores en internet             → descarga candidatos en español, comprueba
                                           dialecto, episodio y duración
  4. Elige: castellano › latino (incrustado › web) › inglés (incrustado › web)
  5. Guarda <video>.srt (si mejora lo que hubiera) y lo apunta en el RESUMEN
```

Al final muestra un resumen con el tipo de cada subtítulo:

```
==== RESUMEN ====
CASTELLANO (incrustado)      pista 7 Spanish (Spain)        Pelicula.2026.1080p.WEB-DL.mp4
CASTELLANO                   addic7ed 12345                 Serie.S01E02.1080p.WEB.mkv
LATINO                       opensubtitles 1234567890       Serie.S01E03.1080p.WEB.mkv
INGLÉS (incrustado)          pista 3 English SDH            Serie.S01E04.1080p.WEB.mkv
```

## Instalación

Requisitos: Python 3.9+ y Claude Code. Funciona en Windows, Linux y macOS. No hay que editar
ninguna ruta: todo se guarda en la carpeta personal de cada usuario (`~/.subtitulos-esp`).

1. Clona el repositorio dentro de la carpeta de skills de Claude Code:
   ```
   git clone https://github.com/danimagan/bajar-subtitulos-esp ~/.claude/skills/bajar-subtitulos-esp
   ```
2. Crea el entorno virtual con subliminal:
   - Windows (PowerShell):
     ```
     python -m venv "$HOME\.subtitulos-esp\venv"
     & "$HOME\.subtitulos-esp\venv\Scripts\pip.exe" install -r "$HOME\.claude\skills\bajar-subtitulos-esp\requirements.txt"
     ```
   - Linux/macOS:
     ```
     python3 -m venv ~/.subtitulos-esp/venv
     ~/.subtitulos-esp/venv/bin/pip install -r ~/.claude/skills/bajar-subtitulos-esp/requirements.txt
     ```
   (Si te lo saltas, Claude lo crea la primera vez que uses la skill.)
3. (Opcional) Cuenta de [opensubtitles.org](https://www.opensubtitles.org) para obtener más
   resultados. Crea tú mismo el archivo `~/.subtitulos-esp/credenciales.json` (queda fuera del
   repositorio y nunca se sube):
   ```json
   {"opensubtitles": {"username": "<usuario>", "password": "<contraseña>"}}
   ```

## Uso

Desde Claude Code: *"baja subtítulos"* o `/bajar-subtitulos-esp <carpeta>`.

O directamente:

```
python -I bajar_subs.py <carpeta_o_video> [opciones]
```

| Opción              | Efecto                                                                 |
|---------------------|------------------------------------------------------------------------|
| `--dry-run`         | No escribe nada; muestra qué haría                                     |
| `--force`           | Rehace aunque ya haya castellano (nunca baja a un tipo peor)           |
| `--solo-castellano` | No guarda latino aunque no haya castellano                             |
| `--sin-ingles`      | No baja inglés cuando no hay nada en español                           |
| `--ingles`          | Además extrae la pista inglesa incrustada como `<video>.en.srt`        |

## Archivos

| Archivo          | Contenido                                                            |
|------------------|----------------------------------------------------------------------|
| `SKILL.md`       | Instrucciones de la skill para Claude y lecciones aprendidas         |
| `bajar_subs.py`  | Script: lectura de pistas MKV/MP4, búsqueda, validación y guardado   |
| `requirements.txt` | Dependencias de Python                                             |
