# bajar-subtitulos-esp

Skill para agentes de programación ([Claude Code](https://claude.com/claude-code),
[Codex](https://developers.openai.com/codex) y [OpenCode](https://opencode.ai)) que consigue
**subtítulos en castellano (español de España)** para películas y series en inglés, con un
script de Python que hace todo el trabajo de forma autónoma.

Sigue el formato estándar de *Agent Skills* (`SKILL.md` con `name` y `description`), así que
el mismo repositorio sirve para los tres agentes sin cambiar nada.

Basta con pedirle al agente *"baja subtítulos"* en una carpeta: busca todos los vídeos,
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
  (.org, con cuenta opcional), Addic7ed, Gestdown y Subtitulamos.
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

Requisitos: Python 3.9+ y al menos uno de estos agentes: Claude Code, Codex u OpenCode.
Funciona en Windows, Linux y macOS. No hay que editar ninguna ruta: todo se guarda en la
carpeta personal de cada usuario (`~/.subtitulos-esp`).

1. Clona el repositorio en la carpeta de skills de tu agente. Cada uno busca las skills en
   un sitio distinto:

   | Agente      | Carpeta de skills del usuario                                        |
   |-------------|----------------------------------------------------------------------|
   | Claude Code | `~/.claude/skills/`                                                  |
   | Codex       | `~/.agents/skills/`                                                  |
   | OpenCode    | `~/.config/opencode/skills/` (y también lee `~/.claude/skills/` y `~/.agents/skills/`) |

   Para tenerla en los tres con una sola copia, clónala en `~/.claude/skills` (Claude Code y
   OpenCode la leen ahí) y enlázala desde `~/.agents/skills` para Codex:
   - Linux/macOS:
     ```
     git clone https://github.com/danimagan/bajar-subtitulos-esp ~/.claude/skills/bajar-subtitulos-esp
     mkdir -p ~/.agents/skills
     ln -s ~/.claude/skills/bajar-subtitulos-esp ~/.agents/skills/bajar-subtitulos-esp
     ```
   - Windows (PowerShell; la unión de directorios no necesita permisos de administrador):
     ```
     git clone https://github.com/danimagan/bajar-subtitulos-esp "$HOME\.claude\skills\bajar-subtitulos-esp"
     New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
     New-Item -ItemType Junction -Path "$HOME\.agents\skills\bajar-subtitulos-esp" -Target "$HOME\.claude\skills\bajar-subtitulos-esp"
     ```
   Si solo usas uno de los agentes, basta con clonarla en su carpeta.
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
   (Si te lo saltas, el agente lo crea la primera vez que uses la skill.)
3. (Opcional) Cuenta de [opensubtitles.org](https://www.opensubtitles.org) para obtener más
   resultados. Crea tú mismo el archivo `~/.subtitulos-esp/credenciales.json` (queda fuera del
   repositorio y nunca se sube):
   ```json
   {"opensubtitles": {"username": "<usuario>", "password": "<contraseña>"}}
   ```

## Uso

Pídeselo al agente con tus palabras (*"baja subtítulos de esta carpeta"*) o invoca la skill
por su nombre:

| Agente      | Invocación explícita                     |
|-------------|------------------------------------------|
| Claude Code | `/bajar-subtitulos-esp <carpeta>`        |
| Codex       | `$bajar-subtitulos-esp <carpeta>`        |
| OpenCode    | *"usa la skill bajar-subtitulos-esp con <carpeta>"* |

El script necesita acceso a internet para consultar los proveedores. Si tu agente ejecuta
los comandos en un sandbox sin red (Codex, por ejemplo, según su configuración), tendrás que
aprobar que lo ejecute con acceso a la red.

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
| `SKILL.md`       | Instrucciones de la skill para el agente y lecciones aprendidas      |
| `bajar_subs.py`  | Script: lectura de pistas MKV/MP4, búsqueda, validación y guardado   |
| `requirements.txt` | Dependencias de Python                                             |
| `LICENSE`        | Licencia MIT                                                         |

## Uso responsable

Este proyecto no aloja ni distribuye subtítulos: solo los busca en proveedores públicos y
los descarga para los vídeos que ya tienes. Cada proveedor tiene sus propias condiciones de
uso; respétalas. OpenSubtitles solo se consulta con tu propia cuenta gratuita de
[opensubtitles.org](https://www.opensubtitles.org) (sin ella, el script lo omite). No
lances búsquedas masivas que sobrecarguen los servicios.

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y redistribuir el código libremente,
manteniendo el aviso de copyright. Se ofrece tal cual, sin garantías.
