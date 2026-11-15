"""Descarga subtítulos en castellano (español de España) para los vídeos de una carpeta.

Uso:
    python -I bajar_subs.py <carpeta_o_video> [--force] [--solo-castellano] [--sin-ingles]
                            [--ingles] [--dry-run]

El subtítulo principal se guarda como <video>.srt (mismo nombre que el vídeo), para que
cualquier reproductor lo cargue solo. Orden de preferencia: castellano > latino > inglés.
Nunca se sustituye un .srt por otro de un tipo peor.

Primero mira las pistas de texto incrustadas en el vídeo (MKV y MP4, sin ffmpeg); si no
hay castellano, busca en todos los proveedores útiles, descarga todos los candidatos en
español y elige solo los que son castellano de España (por vocabulario), del episodio
correcto y con una duración compatible con el vídeo. Las etiquetas de idioma de las webs
NO son fiables (se han visto latinos marcados como "es" y episodios mal etiquetados), por
eso se comprueba el texto.

Credenciales de opensubtitles.org en %USERPROFILE%\\.subtitulos-esp\\credenciales.json:
    {"opensubtitles": {"username": "...", "password": "..."}}
"""
import argparse
import json
import logging
import re
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path

from babelfish import Language
from guessit import guessit
from subliminal import region, refine, scan_video
from subliminal.core import ProviderPool
from subliminal.score import compute_score, episode_scores, movie_scores
from subliminal.video import Episode

HOME = Path.home() / '.subtitulos-esp'
VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv', '.ts'}
MKV_EXT = {'.mkv'}
MP4_EXT = {'.mp4', '.m4v', '.mov'}
# opensubtitlescom queda fuera: se usa la cuenta de opensubtitles.org (son cuentas distintas).
# subtis (Argentina) y napiprojekt (polaco) no aportan castellano.
# podnapisi: el dominio dejó de resolver en 2026 (podnapisi.net sin registro A, www sin DNS).
# tvsubtitles: la búsqueda (search.php) responde 403 a programas. Además, ambos daban latino.
PROVIDERS = ['opensubtitles', 'addic7ed', 'gestdown', 'subtitulamos']

ES_RE = re.compile(
    r'\b(vosotr[oa]s|vuestr[oa]s?|os|vale(?!\s+la\s+pena)|chaval\w*|leches|ordenador\w*|móvil\w*'
    r'|gilipollas|joder|mola|guay|tío|tía|\w+áis|\w+éis)\b',
    re.IGNORECASE,
)
LA_RE = re.compile(
    r'\b(ustedes|computadora\w*|celular\w*|carro|carros|enojad\w*|ahorita|platic\w*|chamac\w*'
    r'|rentar|nomás|recién|lindo|jalar)\b',
    re.IGNORECASE,
)
# Para saber si un .srt que ya existe está en inglés o en español.
EN_PALABRAS = re.compile(r"\b(the|you|and|what|this|that|is|are|it's|don't|i'm|with)\b", re.IGNORECASE)
SP_PALABRAS = re.compile(r'\b(que|qué|el|los|las|una|por|para|está|pero|con|es)\b', re.IGNORECASE)
# Nombres de pistas incrustadas: "... LA", "Spanish (Latin America)", "Español (España)"...
LATINO_NOMBRE = re.compile(r'\b(la|lat|latam|latino\w*|latinoam\w*|latin|419|mx|méxico|mexico)\b',
                           re.IGNORECASE)
ESPANA_NOMBRE = re.compile(r'\b(es|esp|españa|spain|castellano|castilian|european|eur)\b', re.IGNORECASE)

RANGO = {None: 0, 'otro': 0, 'ingles': 1, 'latino': 2, 'castellano': 3}
ETIQUETA = {'castellano': 'CASTELLANO', 'latino': 'LATINO', 'ingles': 'INGLÉS'}


# Coincidencias de la puntuación de subliminal que indican que el subtítulo va sincronizado:
# el mismo archivo (hash) o el mismo release (grupo, plataforma, fps). El resto (source,
# resolución, códecs: 1-4 puntos) apenas dice nada de la sincronía.
SINCRONIA = ('hash', 'release_group', 'streaming_service', 'fps')


def sincronia(sub, video):
    """Parte de la puntuación de subliminal que indica sincronía con este vídeo."""
    pesos = episode_scores if isinstance(video, Episode) else movie_scores
    coincidencias = sub.get_matches(video)
    return sum(pesos[m] for m in SINCRONIA if m in coincidencias)


def dialecto(texto):
    es = len(ES_RE.findall(texto))
    la = len(LA_RE.findall(texto))
    return es, la, es >= 3 and es > 2 * la


def idioma_srt(texto):
    """'castellano', 'latino', 'ingles', 'otro' o None (vacío)."""
    if not texto.strip():
        return None
    if dialecto(texto)[2]:
        return 'castellano'
    n_en, n_sp = len(EN_PALABRAS.findall(texto)), len(SP_PALABRAS.findall(texto))
    if n_sp > n_en:
        return 'latino'
    if n_en > n_sp:
        return 'ingles'
    return 'otro'


def ultimo_tiempo(texto):
    tiempos = re.findall(r'-->\s*(\d+):(\d+):(\d+)', texto)
    if not tiempos:
        return None
    h, m, s = map(int, tiempos[-1])
    return h * 3600 + m * 60 + s


def episodio_correcto(sub, video):
    """Rechaza subtítulos cuyo nombre de release indica otro episodio."""
    if not isinstance(video, Episode):
        return True
    nombres = [getattr(sub, a, None) for a in ('movie_release_name', 'release_info', 'filename', 'title')]
    for nombre in filter(None, nombres):
        if not isinstance(nombre, str):
            continue
        g = guessit(nombre, {'type': 'episode'})
        ep, temp = g.get('episode'), g.get('season')
        if isinstance(ep, int) and video.episode and ep != video.episode:
            return False
        if isinstance(temp, int) and video.season and temp != video.season:
            return False
    return True


def duracion_ok(texto, video):
    dur = getattr(video, 'duration', None)
    fin = ultimo_tiempo(texto)
    if not dur or not fin:
        return True
    return 0.6 * dur <= fin <= dur + 120


def buscar_videos(ruta):
    if ruta.is_file():
        return [ruta]
    return sorted(p for p in ruta.rglob('*') if p.suffix.lower() in VIDEO_EXT and 'sample' not in p.name.lower())


def _ts(ms):
    ms = max(0, int(round(ms)))
    return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def _a_srt(lineas):
    """[(inicio_ms, fin_ms, texto)] -> texto SRT con CRLF."""
    lineas = sorted(l for l in lineas if l[2])
    return ''.join(f'{k}\r\n{_ts(a)} --> {_ts(b)}\r\n{txt.replace(chr(10), chr(13) + chr(10))}\r\n\r\n'
                   for k, (a, b, txt) in enumerate(lineas, 1))


# ---------- Pistas de subtítulos incrustadas en MKV (sin ffmpeg/mkvtoolnix) ----------

def _vint(f):
    """Tamaño EBML; None si es 'desconocido' o fin de archivo."""
    b = f.read(1)
    if not b:
        return None
    b = b[0]; l = 1; m = 0x80
    while l <= 8 and not b & m:
        m >>= 1; l += 1
    v = b & (m - 1); unk = v == m - 1
    for x in f.read(l - 1):
        v = (v << 8) | x; unk = unk and x == 0xFF
    return None if unk else v


def _vint_b(d, p):
    b = d[p]; l = 1; m = 0x80
    while not b & m:
        m >>= 1; l += 1
    v = b & (m - 1)
    for x in d[p + 1:p + l]:
        v = (v << 8) | x
    return v, p + l


def _eid(f):
    b = f.read(1)
    if not b:
        return None
    b0 = b[0]; l = 1; m = 0x80
    while l <= 4 and not b0 & m:
        m >>= 1; l += 1
    v = b0
    for x in f.read(l - 1):
        v = (v << 8) | x
    return v


def pistas_mkv(path):
    """Lista de dicts {num, codec, lang, ietf, name, forced} de las pistas del MKV."""
    pistas = []
    with open(path, 'rb') as f:
        if _eid(f) != 0x1A45DFA3:
            return []
        f.seek(_vint(f), 1)
        if _eid(f) != 0x18538067:
            return []
        _vint(f)
        for _ in range(50):
            i = _eid(f); s = _vint(f)
            if i is None or s is None:
                break
            if i != 0x1654AE6B:
                f.seek(s, 1); continue
            fin = f.tell() + s
            while f.tell() < fin:
                _eid(f); s2 = _vint(f); fin_t = f.tell() + s2
                t = {'lang': 'eng', 'ietf': '', 'name': '', 'forced': 0, 'codec': ''}
                while f.tell() < fin_t:
                    j = _eid(f); d = f.read(_vint(f))
                    if j == 0xD7: t['num'] = int.from_bytes(d, 'big')
                    elif j == 0x86: t['codec'] = d.decode('ascii', 'replace')
                    elif j == 0x22B59C: t['lang'] = d.decode('ascii', 'replace')
                    elif j == 0x22B59D: t['ietf'] = d.decode('ascii', 'replace')
                    elif j == 0x536E: t['name'] = d.decode('utf-8', 'replace')
                    elif j == 0x55AA: t['forced'] = int.from_bytes(d, 'big')
                pistas.append(t)
            break
    return pistas


def extraer_srt_mkv(path, nums):
    """Extrae pistas S_TEXT/UTF8 del MKV. Devuelve {num: texto_srt}. Salta los datos de vídeo/audio."""
    nums = set(nums)
    subs = {n: [] for n in nums}
    escala, ctc = 1_000_000, 0

    def bloque(d, dur):
        tn, p = _vint_b(d, 0)
        if tn not in nums:
            return
        t = (ctc + int.from_bytes(d[p:p + 2], 'big', signed=True)) * escala / 1e6
        fin = t + dur * escala / 1e6 if dur else t + 2000
        subs[tn].append((t, fin, d[p + 3:].decode('utf-8', 'replace').strip()))

    with open(path, 'rb') as f:
        size = f.seek(0, 2); f.seek(0)
        _eid(f); f.seek(_vint(f), 1); _eid(f); _vint(f)
        while f.tell() < size:
            i = _eid(f)
            if i is None:
                break
            s = _vint(f)
            if i == 0x1F43B675:          # Cluster: entrar
                continue
            if s is None:
                continue
            if i == 0x1549A966:          # Info: TimecodeScale
                fin = f.tell() + s
                while f.tell() < fin:
                    j = _eid(f); d = f.read(_vint(f))
                    if j == 0x2AD7B1:
                        escala = int.from_bytes(d, 'big')
            elif i == 0xE7:              # Timecode del cluster
                ctc = int.from_bytes(f.read(s), 'big')
            elif i == 0xA3:              # SimpleBlock
                tn, _ = _vint_b(f.read(1) + b'\0' * 8, 0); f.seek(-1, 1)
                if tn in nums:
                    bloque(f.read(s), None)
                else:
                    f.seek(s, 1)
            elif i == 0xA0:              # BlockGroup
                fin = f.tell() + s; blk = None; dur = None
                while f.tell() < fin:
                    j = _eid(f); s2 = _vint(f)
                    if j == 0xA1:
                        blk = f.read(s2)
                    elif j == 0x9B:
                        dur = int.from_bytes(f.read(s2), 'big')
                    else:
                        f.seek(s2, 1)
                if blk:
                    bloque(blk, dur)
            else:
                f.seek(s, 1)

    return {n: _a_srt(lineas) for n, lineas in subs.items()}


# ---------- Pistas de subtítulos incrustadas en MP4/M4V/MOV (sin ffmpeg) ----------
# Solo se lee la caja 'moov' (índice de muestras, unos pocos MB) y luego las muestras de
# las pistas de subtítulos, saltando directamente a su posición: no se lee vídeo ni audio.

def _cajas(d, ini, fin):
    """Itera las cajas ISO-BMFF de d[ini:fin] -> (tipo, inicio_datos, fin)."""
    while ini + 8 <= fin:
        tam, tipo = struct.unpack_from('>I4s', d, ini); cab = 8
        if tam == 1:
            tam = struct.unpack_from('>Q', d, ini + 8)[0]; cab = 16
        elif tam == 0:
            tam = fin - ini
        if tam < cab:
            break
        yield tipo.decode('latin-1'), ini + cab, min(ini + tam, fin)
        ini += tam


def _leer_moov(f):
    tam_arch = f.seek(0, 2); pos = 0
    while pos + 8 <= tam_arch:
        f.seek(pos); cab = f.read(16)
        tam, tipo = struct.unpack_from('>I4s', cab); h = 8
        if tam == 1:
            tam = struct.unpack_from('>Q', cab, 8)[0]; h = 16
        elif tam == 0:
            tam = tam_arch - pos
        if tam < h:
            break
        if tipo == b'moov':
            f.seek(pos + h)
            return f.read(tam - h)
        pos += tam
    return None


def _trak_mp4(m, ini, fin):
    t = {'num': 0, 'codec': '', 'lang': '', 'ietf': '', 'name': '', 'forced': 0, 'handler': '',
         'escala': 1000, 'stts': [], 'stsc': [], 'stsz': [], 'co': [], 'elst': [], 'chap': []}

    def rec(i, f_):
        for tipo, a, b in _cajas(m, i, f_):
            v = m[a] if a < len(m) else 0
            if tipo in ('mdia', 'minf', 'stbl', 'udta', 'edts'):
                rec(a, b)
            elif tipo == 'tkhd':
                t['num'] = struct.unpack_from('>I', m, a + (20 if v == 1 else 12))[0]
            elif tipo == 'tref':
                for t2, a2, b2 in _cajas(m, a, b):
                    if t2 == 'chap':
                        t['chap'] += list(struct.unpack_from(f'>{(b2 - a2) // 4}I', m, a2))
            elif tipo == 'hdlr':
                t['handler'] = m[a + 8:a + 12].decode('latin-1')
            elif tipo == 'mdhd':
                t['escala'] = struct.unpack_from('>I', m, a + (20 if v == 1 else 12))[0] or 1000
                c = struct.unpack_from('>H', m, a + (32 if v == 1 else 20))[0]
                if c:
                    t['lang'] = ''.join(chr(((c >> s) & 31) + 0x60) for s in (10, 5, 0))
            elif tipo == 'elng':
                t['ietf'] = m[a + 4:b].split(b'\0')[0].decode('ascii', 'replace')
            elif tipo == 'name':       # título de la pista (udta/name)
                t['name'] = m[a:b].split(b'\0')[0].decode('utf-8', 'replace').strip()
            elif tipo == 'stsd':
                t['codec'] = m[a + 12:a + 16].decode('latin-1')
            elif tipo == 'stts':
                n = struct.unpack_from('>I', m, a + 4)[0]
                t['stts'] = [struct.unpack_from('>II', m, a + 8 + 8 * k) for k in range(n)]
            elif tipo == 'stsc':
                n = struct.unpack_from('>I', m, a + 4)[0]
                t['stsc'] = [struct.unpack_from('>III', m, a + 8 + 12 * k) for k in range(n)]
            elif tipo == 'stsz':
                tam, n = struct.unpack_from('>II', m, a + 4)
                t['stsz'] = [tam] * n if tam else list(struct.unpack_from(f'>{n}I', m, a + 12))
            elif tipo == 'stco':
                n = struct.unpack_from('>I', m, a + 4)[0]
                t['co'] = list(struct.unpack_from(f'>{n}I', m, a + 8))
            elif tipo == 'co64':
                n = struct.unpack_from('>I', m, a + 4)[0]
                t['co'] = list(struct.unpack_from(f'>{n}Q', m, a + 8))
            elif tipo == 'elst':
                n = struct.unpack_from('>I', m, a + 4)[0]
                fmt, paso = ('>Qq', 20) if v == 1 else ('>Ii', 12)
                t['elst'] = [struct.unpack_from(fmt, m, a + 8 + paso * k) for k in range(n)]

    rec(ini, fin)
    return t


def pistas_mp4(path):
    """Pistas de subtítulos de texto (tx3g / WebVTT) del MP4, con el mismo formato que pistas_mkv
    más los índices de muestras. Excluye las pistas de capítulos."""
    with open(path, 'rb') as f:
        moov = _leer_moov(f)
    if not moov:
        return []
    escala_mov, traks = 1000, []
    for tipo, a, b in _cajas(moov, 0, len(moov)):
        if tipo == 'mvhd':
            escala_mov = struct.unpack_from('>I', moov, a + (20 if moov[a] == 1 else 12))[0] or 1000
        elif tipo == 'trak':
            traks.append(_trak_mp4(moov, a, b))
    capitulos = {n for t in traks for n in t['chap']}
    pistas = []
    for t in traks:
        if t['codec'] in ('tx3g', 'wvtt') and t['num'] not in capitulos:
            t['escala_mov'] = escala_mov
            t['lang'] = t['lang'] or 'und'
            pistas.append(t)
    return pistas


def _texto_muestra(d, codec):
    if codec == 'wvtt':
        partes = [d[a2:b2].decode('utf-8', 'replace')
                  for tipo, a, b in _cajas(d, 0, len(d)) if tipo == 'vttc'
                  for t2, a2, b2 in _cajas(d, a, b) if t2 == 'payl']
        return '\n'.join(partes).strip()
    n = int.from_bytes(d[:2], 'big')
    raw = d[2:2 + n]
    if raw[:2] == b'\xfe\xff':
        txt = raw[2:].decode('utf-16-be', 'replace')
    else:
        txt = raw.decode('utf-8', 'replace')
    return txt.replace('\r\n', '\n').replace('\r', '\n').strip()


def _muestras_mp4(f, p):
    """[(inicio_ms, fin_ms, texto)] de una pista de subtítulos MP4."""
    tams, stsc = p['stsz'], p['stsc']
    if not stsc or not tams:
        return []                    # MP4 fragmentado (moof): no soportado
    offs, k = [], 0
    for ci, base in enumerate(p['co'], 1):
        while k + 1 < len(stsc) and stsc[k + 1][0] <= ci:
            k += 1
        for _ in range(stsc[k][1]):
            if len(offs) >= len(tams):
                break
            offs.append(base)
            base += tams[len(offs) - 1]
    durs = [d for n, d in p['stts'] for _ in range(n)]
    # Lista de edición: un hueco inicial retrasa la pista; un media_time > 0 la adelanta.
    desfase = 0.0
    for dur_seg, t_media in p['elst']:
        if t_media == -1:
            desfase += dur_seg / p['escala_mov']
        else:
            desfase -= t_media / p['escala']
            break
    lineas, t = [], 0
    for i, (o, tam) in enumerate(zip(offs, tams)):
        d = durs[i] if i < len(durs) else 0
        if tam > 2:
            f.seek(o)
            texto = _texto_muestra(f.read(tam), p['codec'])
            if texto:
                ini = (t / p['escala'] + desfase) * 1000
                fin = ((t + d) / p['escala'] + desfase) * 1000
                if lineas and lineas[-1][2] == texto and abs(lineas[-1][1] - ini) < 1:
                    lineas[-1] = (lineas[-1][0], fin, texto)     # WebVTT parte una frase en varias muestras
                else:
                    lineas.append((ini, fin, texto))
        t += d
    return lineas


def extraer_srt_mp4(path, nums):
    nums = set(nums)
    pistas = [p for p in pistas_mp4(path) if p['num'] in nums]
    with open(path, 'rb') as f:
        return {p['num']: _a_srt(_muestras_mp4(f, p)) for p in pistas}


# ---------- Pistas incrustadas (común a MKV y MP4) ----------

def pistas_texto(path):
    ext = path.suffix.lower()
    if ext in MKV_EXT:
        return [p for p in pistas_mkv(path) if p['codec'] == 'S_TEXT/UTF8']
    if ext in MP4_EXT:
        return pistas_mp4(path)
    return []


def extraer_srt(path, nums):
    return (extraer_srt_mkv if path.suffix.lower() in MKV_EXT else extraer_srt_mp4)(path, nums)


def _forzada(p):
    return p.get('forced') or re.search(r'forced|forzad', p['name'], re.IGNORECASE)


def _es_espanol(p):
    return p['lang'] in ('spa', 'es') or p['ietf'].lower().startswith('es')


def _es_ingles(p):
    return p['lang'] in ('eng', 'en') or p['ietf'].lower().startswith('en')


def _etiqueta_latina(p):
    ietf = p['ietf'].lower()
    return (ietf.startswith('es-') and ietf != 'es-es') or bool(LATINO_NOMBRE.search(p['name']))


def _etiqueta_espana(p):
    return p['ietf'].lower() == 'es-es' or bool(ESPANA_NOMBRE.search(p['name']))


def incrustado(path, con_ingles=False):
    """Busca en las pistas de texto del MKV/MP4.
    Devuelve {'castellano', 'latino', 'ingles'} -> (texto, descripción) o None."""
    res = {'castellano': None, 'latino': None, 'ingles': None}
    try:
        pistas = [p for p in pistas_texto(path) if not _forzada(p)]
    except Exception as e:
        print(f'  (no se pudieron leer las pistas incrustadas: {e})')
        return res
    if not pistas:
        return res
    print(f'  {len(pistas)} pistas de subtítulos incrustadas', flush=True)
    esp = [p for p in pistas if _es_espanol(p)]
    eng = sorted((p for p in pistas if _es_ingles(p)), key=lambda p: 'sdh' not in p['name'].lower())
    nums = [p['num'] for p in esp] + ([eng[0]['num']] if eng and con_ingles else [])
    if not nums:
        return res
    try:
        textos = extraer_srt(path, nums)
    except Exception as e:
        print(f'  (no se pudieron extraer las pistas incrustadas: {e})')
        return res
    cast, lat = [], []
    for p in esp:
        texto = textos.get(p['num'])
        if not texto:
            continue
        es, la, ok = dialecto(texto)
        print(f'  pista incrustada {p["num"]} [{p["ietf"] or p["lang"]}] {p["name"]}: '
              f'españa={es} latino={la}', flush=True)
        desc = f'pista {p["num"]} {p["name"]}'.strip()
        # En las pistas oficiales la etiqueta latina es fiable, y el detector de dialecto no
        # basta (una pista "LA" de Disney+ dio españa=26 latino=11 y pasaba por castellano).
        if ok and not _etiqueta_latina(p):
            cast.append(((_etiqueta_espana(p), es / (la + 1)), texto, desc))
        else:
            lat.append(((_etiqueta_latina(p), la), texto, desc))
    if cast:
        res['castellano'] = max(cast, key=lambda c: c[0])[1:]
    if lat:
        res['latino'] = max(lat, key=lambda c: c[0])[1:]
    if eng and con_ingles and textos.get(eng[0]['num']):
        res['ingles'] = (textos[eng[0]['num']], f'pista {eng[0]["num"]} {eng[0]["name"]}'.strip())
    return res


def ingles_incrustado(path):
    """Extrae la mejor pista inglesa de texto del MKV/MP4 (prefiere SDH). Devuelve (texto | None, nombre)."""
    try:
        pistas = [p for p in pistas_texto(path) if not _forzada(p) and _es_ingles(p)]
        if not pistas:
            return None, ''
        pistas.sort(key=lambda p: 'sdh' not in p['name'].lower())
        texto = extraer_srt(path, [pistas[0]['num']]).get(pistas[0]['num'])
    except Exception:
        return None, ''
    return (texto or None), f'pista {pistas[0]["num"]} {pistas[0]["name"]}'.strip()


def ingles_proveedores(pool, video, max_descargas=8):
    """Busca subtítulos en inglés; descarga por orden de puntuación hasta encontrar uno válido."""
    candidatos = pool.list_subtitles(video, {Language('eng')})
    candidatos.sort(key=lambda s: compute_score(s, video), reverse=True)
    for sub in candidatos[:max_descargas]:
        if not pool.download_subtitle(sub) or not sub.content:
            continue
        texto = sub.text
        ep_ok = episodio_correcto(sub, video)
        dur_ok = duracion_ok(texto, video)
        print(f'  [inglés] {sub.provider_name:13} id={sub.id} score={compute_score(sub, video)} '
              f'episodio_ok={ep_ok} duración_ok={dur_ok}', flush=True)
        if ep_ok and dur_ok:
            return texto, sub
    return None, None


def guardar(texto, destino):
    if destino.exists():
        bk = HOME / 'backup' / f'{datetime.now():%Y%m%d-%H%M%S}_{destino.name}'
        bk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destino, bk)
    destino.write_text(texto, encoding='utf-8', newline='')


class SinRuido(logging.Filter):
    """subliminal escribe trazas largas en stderr y el refinador omdb da 401 siempre.

    Se descartan, porque no cambian el resultado:
    - los fallos de refinado (omdb...);
    - el cierre de sesión de opensubtitles.org (el servidor ya no tiene el método
      `close`, pero la búsqueda y las descargas funcionan);
    - los avisos de knowit (largos banners de "ffprobe/mkvmerge not found": el script lee
      las pistas MKV/MP4 por su cuenta y no necesita esos programas).
    Del resto de avisos se deja una sola línea, sin traza. Así la salida es limpia en
    cualquier shell y agente sin tener que filtrarla con grep / Where-Object.
    """

    def filter(self, record):
        mensaje = record.getMessage()
        if (record.name.startswith('knowit') or 'Failed to refine' in mensaje
                or 'improperly terminated' in mensaje):
            return False
        record.exc_info = record.exc_text = None
        return True


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    aviso = logging.StreamHandler()
    aviso.setFormatter(logging.Formatter('  aviso (%(name)s): %(message)s'))
    aviso.addFilter(SinRuido())
    logging.basicConfig(level=logging.WARNING, handlers=[aviso])
    ap = argparse.ArgumentParser()
    ap.add_argument('ruta', type=Path)
    ap.add_argument('--force', action='store_true',
                    help='rehacer aunque ya haya un .srt castellano (nunca se baja a un tipo peor)')
    ap.add_argument('--solo-castellano', action='store_true',
                    help='no guardar latino aunque no haya castellano (por defecto, si solo hay '
                         'latino, se guarda como .srt y el resumen lo marca como LATINO)')
    ap.add_argument('--sin-ingles', action='store_true',
                    help='no bajar inglés cuando no haya ningún subtítulo en español '
                         '(por defecto se guarda como .srt y el resumen lo marca como INGLÉS)')
    ap.add_argument('--dry-run', action='store_true', help='no escribir nada, solo mostrar qué haría')
    ap.add_argument('--ingles', action='store_true',
                    help='además, extraer la pista inglesa incrustada en el vídeo como .en.srt')
    args = ap.parse_args()
    if args.dry_run:
        global guardar
        guardar = lambda texto, destino: print(f'  [dry-run] guardaría {destino.name}')

    region.configure('dogpile.cache.memory')
    configs = {}
    cred = HOME / 'credenciales.json'
    if cred.exists():
        configs = json.loads(cred.read_text(encoding='utf-8'))
    providers = [p for p in PROVIDERS if p != 'opensubtitles' or 'opensubtitles' in configs]

    resumen = []
    with ProviderPool(providers=providers, provider_configs=configs) as pool:
        for path in buscar_videos(args.ruta):
            destino = path.with_suffix('.srt')
            # Formato antiguo: <video>.es.srt (o .en.srt si no había español) -> <video>.srt
            previo = destino if destino.exists() else next(
                (p for p in (path.with_suffix('.es.srt'), path.with_suffix('.en.srt')) if p.exists()), None)
            actual = idioma_srt(previo.read_text(encoding='utf-8', errors='replace')) if previo else None
            nota = '-'
            if previo and previo != destino:
                nota = f'renombrado desde {previo.suffixes[-2]}.srt'
                if args.dry_run:
                    print(f'  [dry-run] renombraría {previo.name} -> {destino.name}')
                else:
                    previo.rename(destino)

            if actual == 'castellano' and not args.force:
                resumen.append((path.name, 'ya tenía castellano', nota))
                continue

            print(f'\n== {path.name}', flush=True)
            if actual:
                print(f'  ya tiene {destino.name} en {actual}', flush=True)

            def aplicar(tipo, texto, estado, fuente):
                """Guarda como <video>.srt salvo que el que ya hay sea de un tipo mejor."""
                if RANGO[tipo] > RANGO[actual] or (args.force and RANGO[tipo] == RANGO[actual]):
                    guardar(texto, destino)
                    resumen.append((path.name, estado, fuente))
                else:
                    resumen.append((path.name, f'{ETIQUETA[actual]} (ya tenía .srt)', nota))

            emb = incrustado(path, args.ingles)
            destino_en = path.with_suffix('.en.srt')
            if emb['ingles'] and (args.force or not destino_en.exists()):
                guardar(emb['ingles'][0], destino_en)
            if emb['castellano']:
                aplicar('castellano', emb['castellano'][0], 'CASTELLANO (incrustado)', emb['castellano'][1][:30])
                continue

            video = scan_video(str(path))
            # scan_video adivina sobre la ruta completa: una carpeta como "<SERIE> - FULL"
            # convierte la serie en "<SERIE> FULL" y ningún proveedor encuentra nada.
            if isinstance(video, Episode):
                g = guessit(path.name)
                if g.get('title'):
                    video.series = g['title']
                    video.title = g.get('episode_title')
            refine(video, refiners=['hash', 'metadata', 'tvdb'])

            candidatos = pool.list_subtitles(video, {Language('spa')})
            castellanos, latinos = [], []
            vistos = set()
            for sub in candidatos:
                if not pool.download_subtitle(sub) or not sub.content:
                    continue
                texto = sub.text
                if texto in vistos:
                    continue
                vistos.add(texto)
                es, la, ok = dialecto(texto)
                ep_ok = episodio_correcto(sub, video)
                dur_ok = duracion_ok(texto, video)
                score = compute_score(sub, video)
                sinc = sincronia(sub, video)
                print(f'  {sub.provider_name:13} id={sub.id} score={score} sincronía={sinc} '
                      f'españa={es} latino={la} episodio_ok={ep_ok} duración_ok={dur_ok}', flush=True)
                if not (ep_ok and dur_ok):
                    continue
                (castellanos if ok else latinos).append((sinc, es, score, sub, texto))

            if castellanos:
                # 1) sincronía (mismo archivo > mismo release), 2) más señales de castellano,
                # 3) puntuación de subliminal. Antes mandaba la puntuación y un +2 por coincidir
                # en resolución y códec ganaba a un candidato con 5 veces más señales.
                sinc, es, score, sub, texto = max(castellanos, key=lambda c: (c[0], c[1], c[2]))
                aplicar('castellano', texto, 'CASTELLANO', f'{sub.provider_name} {sub.id}')
            elif emb['latino'] and not args.solo_castellano:
                # La pista incrustada va sincronizada exactamente con el vídeo: mejor que una web.
                aplicar('latino', emb['latino'][0], 'LATINO (incrustado)', emb['latino'][1][:30])
            elif latinos and not args.solo_castellano:
                sinc, es, score, sub, texto = max(latinos, key=lambda c: (c[0], c[2]))
                aplicar('latino', texto, 'LATINO', f'{sub.provider_name} {sub.id}')
            elif RANGO[actual] >= RANGO['ingles'] and (not args.force or RANGO[actual] > RANGO['ingles']):
                # Ya hay latino o inglés y no se ha encontrado nada mejor: se deja como está.
                resumen.append((path.name, f'{ETIQUETA[actual]} (ya tenía .srt)', nota))
            elif args.sin_ingles:
                resumen.append((path.name, 'NO ENCONTRADO', f'{len(latinos)} latinos descartados'))
            else:
                # Ni castellano ni latino: subtítulos en inglés como último recurso.
                texto, pista = (emb['ingles'] or (None, ''))
                texto, pista = (texto, pista) if texto else ingles_incrustado(path)
                if texto:
                    aplicar('ingles', texto, 'INGLÉS (incrustado)', pista[:30])
                    continue
                texto, sub = ingles_proveedores(pool, video)
                if texto:
                    aplicar('ingles', texto, 'INGLÉS', f'{sub.provider_name} {sub.id}')
                else:
                    resumen.append((path.name, 'NO ENCONTRADO', 'ni español ni inglés'))

    print('\n==== RESUMEN ====')
    for nombre, estado, fuente in resumen:
        print(f'{estado:28} {fuente:30} {nombre}')


if __name__ == '__main__':
    sys.exit(main())
