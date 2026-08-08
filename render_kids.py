import json, os, subprocess, requests, tempfile, sys, traceback, time, re, random

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']
bot_token = os.environ.get('BOT_TOKEN', '')
chat_id = os.environ.get('CHAT_ID', '8946671215') or '8946671215'
github_token = os.environ.get('GH_TOKEN', '')
release_id = os.environ.get('RELEASE_ID', '367237403')
repo = os.environ.get('REPO', 'frankywithjesus-rgb/marco-video-renderer')

scenes = payload['scenes']  # [{image, text, tipo?, overlay?}, ...] en orden
audio_url = payload.get('audioUrl', '')
titulo = payload.get('titulo', 'Escuela Sabática Kids')

workdir = tempfile.mkdtemp()
FALLBACK = "https://images.pexels.com/photos/1367192/pexels-photo-1367192.jpeg"

# ---------------------------------------------------------------------------
# NUEVO: transiciones variadas entre escenas (xfade) y tipo de escena "escritura"
# ---------------------------------------------------------------------------
FPS = 20
TRANSITION_DUR = 0.5  # segundos de crossfade entre escenas

# Rotación de transiciones. "fade" es el comodín seguro; el resto le dan variedad.
TRANSITION_POOL = ['fade', 'wipeleft', 'wiperight', 'circleopen', 'slideup', 'dissolve', 'smoothleft']

def pick_transition(idx, sc_from, sc_to):
    """Elige transición según contenido cuando hay pistas, si no rota por índice."""
    to_tipo = (sc_to.get('tipo') or '').lower()
    to_text = (sc_to.get('text') or '').lower()
    if to_tipo == 'escritura':
        return 'circleopen'  # entrar a un momento de "escritura" se siente como revelar algo
    if any(k in to_text for k in ['corinto', 'jerusalén', 'jerusalen', 'belén', 'belen', 'calvario', 'cruz']):
        return 'wipeleft'  # cambio de lugar -> sensación de viaje
    if 'amor' in to_text or 'corazón' in to_text or 'corazon' in to_text:
        return 'dissolve'
    return TRANSITION_POOL[idx % len(TRANSITION_POOL)]

def is_valid_image(path):
    try:
        with open(path, 'rb') as f:
            header = f.read(12)
        if header[:2] == b'\xff\xd8':
            return True
        if header[:8] == b'\x89PNG\r\n\x1a\n':
            return True
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            return True
        return False
    except Exception:
        return False

def detect_ext(path):
    """Detecta la extension real por magic bytes. Guardar con la extension
    equivocada (ej. .jpg para bytes PNG) hace que el demuxer image2 de FFmpeg
    intente decodificar con el codec incorrecto y se quede colgado esperando
    frames validos que nunca llegan -- esta fue la causa real de los renders
    que se colgaban hasta el limite de tiempo del job."""
    with open(path, 'rb') as f:
        header = f.read(12)
    if header[:2] == b'\xff\xd8':
        return '.jpg'
    if header[:8] == b'\x89PNG\r\n\x1a\n':
        return '.png'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return '.webp'
    return '.jpg'

def download(url, base_path):
    """base_path SIN extension (ej. /tmp/x/s1) -- la extension final se decide
    por el contenido real descargado, no se asume de antemano."""
    tmp_path = base_path + '.tmp'
    try:
        r = requests.get(url, timeout=120, stream=True)
        if r.status_code == 200:
            with open(tmp_path, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            size = os.path.getsize(tmp_path)
            if size > 5000 and is_valid_image(tmp_path):
                final_path = base_path + detect_ext(tmp_path)
                os.rename(tmp_path, final_path)
                print(f"OK {final_path}: {size} bytes")
                return final_path
    except Exception as e:
        print(f"  Intento fallido: {e}")
    print(f"Usando fallback para {base_path}")
    final_path = base_path + '.jpg'
    r = requests.get(FALLBACK, timeout=120, stream=True)
    with open(final_path, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return final_path

def get_audio_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path], capture_output=True, text=True)
    data = json.loads(r.stdout)
    dur = float(data['format']['duration'])
    print(f"Duracion del audio: {dur:.1f}s")
    return dur

def image_to_kenburns(inp, out, dur, mode='zoom_in'):
    # Resolucion de trabajo reducida (720p) y fps mas bajo: zoompan es MUY pesado
    # por-frame en CPU, y a 1080p/30fps con escenas largas (~20-25s) se pasaba
    # del limite de 25 min del job. 1280x720@20fps recorta el trabajo total a
    # menos de la mitad manteniendo buena calidad percibida en YouTube/redes.
    fps = FPS
    frames = max(1, int(dur * fps))
    # NUEVO: más variedad de movimiento en vez de solo zoom in/out centrado.
    # Se rota entre zoom in, zoom out, y paneos diagonales sutiles para que no
    # todas las escenas se sientan iguales.
    if mode == 'zoom_in':
        zexpr = "zoom+0.0012"
        xexpr = "iw/2-(iw/zoom/2)"
        yexpr = "ih/2-(ih/zoom/2)"
    elif mode == 'zoom_out':
        zexpr = "if(lte(zoom,1.0),1.25,zoom-0.0012)"
        xexpr = "iw/2-(iw/zoom/2)"
        yexpr = "ih/2-(ih/zoom/2)"
    elif mode == 'pan_right':
        zexpr = "1.15"
        xexpr = "(iw-iw/zoom)*(on/{frames})".format(frames=frames)
        yexpr = "ih/2-(ih/zoom/2)"
    else:  # 'pan_left'
        zexpr = "1.15"
        xexpr = "(iw-iw/zoom)*(1-on/{frames})".format(frames=frames)
        yexpr = "ih/2-(ih/zoom/2)"
    vf = (
        "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d={frames}:s=1280x720:fps={fps},"
        "format=yuv420p"
    )
    print(f"  Ken Burns ({mode}): {dur:.1f}s, {frames} frames @ {fps}fps", flush=True)
    try:
        result = subprocess.run([
            'ffmpeg', '-y', '-f', 'image2', '-loop', '1', '-i', inp, '-t', str(dur),
            '-vf', vf, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-an', out
        ], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception(f"FFmpeg Ken Burns colgado mas de 300s en {inp} (dur={dur:.1f}s, {frames} frames)")
    if result.returncode != 0:
        raise Exception(f"FFmpeg Ken Burns error: {result.stderr[-300:]}")
    print(f"  -> listo: {out}", flush=True)

def _wrap_words(words, max_chars_per_line=38):
    """Distribuye palabras en líneas (para el efecto de escritura), devuelve
    lista de (word, line_index, col_index_en_esa_linea)."""
    layout = []
    line_idx = 0
    line_len = 0
    for w in words:
        wlen = len(w) + 1
        if line_len + wlen > max_chars_per_line and line_len > 0:
            line_idx += 1
            line_len = 0
        layout.append((w, line_idx))
        line_len += wlen
    return layout

def escritura_clip(inp, out, dur, text):
    """NUEVO: en vez de Ken Burns normal, anima el texto apareciendo palabra
    por palabra sobre la imagen (mano escribiendo / pergamino), tipo
    máquina de escribir, sincronizado con la duración de la escena."""
    fps = FPS
    words = [w for w in re.split(r'\s+', text.strip()) if w]
    if not words:
        words = ['...']
    layout = _wrap_words(words, max_chars_per_line=38)
    n_lines = layout[-1][1] + 1
    per_word = dur / max(len(words), 1)

    font_size = 34
    line_height = 46
    top_margin = 720 - (n_lines * line_height) - 60  # bloque de texto pegado abajo

    # Ligero zoom lento de fondo para que la imagen no se sienta congelada.
    # FIX (encontrado probando con imagenes reales): antes usaba
    # scale...increase + crop=1280:720, igual que Ken Burns normal, y eso
    # cortaba contenido pegado arriba/abajo de la imagen cuadrada de Gemini
    # (el mismo bug de recorte documentado originalmente, aplicado aca a la
    # imagen de fondo de "mano escribiendo"). Se cambia a decrease + pad para
    # mostrar la imagen completa con barras, sin recortar nada importante.
    frames = max(1, int(dur * fps))
    base_vf = (
        "scale=1280:720:force_original_aspect_ratio=decrease,"
        "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"zoompan=z='min(zoom+0.0006,1.08)':d={frames}:s=1280x720:fps={fps},"
        "format=yuv420p[bg]"
    )

    filters = [base_vf]
    last = 'bg'
    # Se agrupan las palabras por línea para calcular una x aproximada
    # (fuente monoespaciada asumida ~19px por caracter a font_size=34).
    char_w = 19
    line_texts = {}
    for w, li in layout:
        line_texts.setdefault(li, []).append(w)

    t_cursor = 0.0
    line_progress = {li: [] for li in range(n_lines)}
    for i, (w, li) in enumerate(layout):
        reveal_t = round(i * per_word, 2)
        line_progress[li].append(w)
        rendered = ' '.join(line_progress[li])
        safe_text = rendered.replace(':', '\\:').replace("'", "\u2019")
        x_pos = 640 - (len(line_texts[li]) * char_w) // 2  # centrado aprox por línea
        y_pos = top_margin + li * line_height
        node = f"t{i}"
        filters.append(
            f"[{last}]drawtext=text='{safe_text}':fontcolor=white:fontsize={font_size}:"
            f"box=1:boxcolor=black@0.45:boxborderw=10:x={x_pos}:y={y_pos}:"
            f"enable='gte(t,{reveal_t})'[{node}]"
        )
        last = node

    filter_complex = ';'.join(filters)
    cmd = [
        'ffmpeg', '-y', '-f', 'image2', '-loop', '1', '-i', inp, '-t', str(dur),
        '-filter_complex', filter_complex, '-map', f'[{last}]',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-an', out
    ]
    print(f"  Escritura: {dur:.1f}s, {len(words)} palabras, {n_lines} lineas", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception(f"FFmpeg escritura colgado mas de 300s en {inp}")
    if result.returncode != 0:
        raise Exception(f"FFmpeg escritura error: {result.stderr[-500:]}")
    print(f"  -> listo: {out}", flush=True)

def upload_to_github_release(path, token):
    print("=== Subiendo video a GitHub Release ===")
    headers = {"Authorization": f"token {token}"}
    assets = requests.get(f"https://api.github.com/repos/{repo}/releases/{release_id}/assets", headers=headers).json()
    for asset in assets:
        requests.delete(f"https://api.github.com/repos/{repo}/releases/assets/{asset['id']}", headers=headers)
        print(f"  Borrado asset anterior: {asset['name']}")
    filename = f"episodio_{int(time.time())}.mp4"
    upload_url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={filename}"
    with open(path, 'rb') as f:
        r = requests.post(upload_url, headers={**headers, "Content-Type": "video/mp4"}, data=f, timeout=300)
    data = r.json()
    if r.status_code in (200, 201) and 'browser_download_url' in data:
        url = data['browser_download_url']
        print(f"URL publica: {url}")
        return url
    raise Exception(f"GitHub Release upload error: {r.status_code} {data}")

def format_time(secs):
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int((secs % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]

try:
    print("=== Descargando imagenes de escenas ===")
    images = []
    for i, sc in enumerate(scenes):
        images.append(download(sc['image'], f"{workdir}/s{i+1}"))

    has_audio = bool(audio_url and len(audio_url) > 10)
    if has_audio:
        print("=== Descargando audio ===")
        r = requests.get(audio_url, timeout=120, stream=True)
        audio = f"{workdir}/audio.mp3"
        with open(audio, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print(f"Audio: {os.path.getsize(audio)} bytes")
        total_duration = get_audio_duration(audio)
    else:
        total_duration = 60.0

    char_counts = [max(len(sc.get('text', '')), 1) for sc in scenes]
    total_chars = sum(char_counts)
    durations = [total_duration * c / total_chars for c in char_counts]
    print(f"Duracion total: {total_duration:.1f}s repartida en {len(scenes)} escenas: {[round(d,1) for d in durations]}")

    print("=== Preparando overlays (iconos de amor / lugares) ===")
    OVERLAY_DURATION = 2.5
    overlay_events = []  # [{start, end, path}]
    cursor = 0.0
    for i, (sc, dur) in enumerate(zip(scenes, durations)):
        ov = sc.get('overlay')
        if ov and ov.get('image'):
            frac = max(0.0, min(1.0, ov.get('charFraction', 0.5)))
            center = cursor + frac * dur
            start = max(cursor, center - OVERLAY_DURATION / 2)
            end = min(cursor + dur, start + OVERLAY_DURATION)
            if end - start < 0.5:
                cursor += dur
                continue
            try:
                ov_path = download(ov['image'], f"{workdir}/ov{i+1}")
                overlay_events.append({'start': start, 'end': end, 'path': ov_path, 'label': ov.get('label', '')})
                print(f"  Overlay '{ov.get('label')}' en {start:.1f}s-{end:.1f}s", flush=True)
            except Exception as e:
                print(f"  No se pudo preparar overlay de escena {i+1}: {e}", flush=True)
        cursor += dur

    # -----------------------------------------------------------------------
    # NUEVO: extender la duración de cada clip para compensar el solape que
    # va a "comerse" el xfade entre escenas, así el timing de audio/subtítulos
    # (calculado sobre `durations` original) casi no se desincroniza.
    # -----------------------------------------------------------------------
    n = len(scenes)
    extended = list(durations)
    for i in range(n):
        pad = 0.0
        if i > 0:
            pad += TRANSITION_DUR / 2
        if i < n - 1:
            pad += TRANSITION_DUR / 2
        extended[i] = durations[i] + pad

    print("=== Animando escenas (Ken Burns variado / escritura) ===")
    modes = ['zoom_in', 'pan_right', 'zoom_out', 'pan_left']
    for i, (sc, img, dur) in enumerate(zip(scenes, images, extended)):
        out_clip = f"{workdir}/c{i+1}.mp4"
        if (sc.get('tipo') or '').lower() == 'escritura':
            escritura_clip(img, out_clip, dur, sc.get('text', ''))
        else:
            image_to_kenburns(img, out_clip, dur, mode=modes[i % len(modes)])

    print("=== Uniendo escenas con transiciones (xfade) ===")
    if n == 1:
        subprocess.run(['ffmpeg', '-y', '-i', f"{workdir}/c1.mp4", '-c', 'copy', f"{workdir}/base.mp4"], check=True, capture_output=True, timeout=120)
    else:
        cmd = ['ffmpeg', '-y']
        for i in range(1, n + 1):
            cmd += ['-i', f"{workdir}/c{i}.mp4"]
        filters = []
        last_label = '0:v'
        chain_dur = extended[0]
        for i in range(1, n):
            trans = pick_transition(i - 1, scenes[i - 1], scenes[i])
            offset = round(chain_dur - TRANSITION_DUR, 2)
            out_label = f"x{i}"
            filters.append(
                f"[{last_label}][{i}:v]xfade=transition={trans}:duration={TRANSITION_DUR}:offset={offset}[{out_label}]"
            )
            chain_dur = chain_dur + extended[i] - TRANSITION_DUR
            last_label = out_label
        filter_complex = ';'.join(filters)
        cmd += ['-filter_complex', filter_complex, '-map', f'[{last_label}]',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', f"{workdir}/base.mp4"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise Exception("FFmpeg xfade colgado mas de 300s")
        if result.returncode != 0:
            raise Exception(f"FFmpeg xfade error: {result.stderr[-500:]}")
    print("  -> union con transiciones lista", flush=True)

    print("=== Generando subtitulos por escena ===")
    srt_path = f"{workdir}/subs.srt"
    entries = []
    idx = 1
    cursor = 0.0
    for sc, dur in zip(scenes, durations):
        seg_start = cursor
        seg_end = cursor + dur
        # FIX (encontrado probando con video real): las escenas tipo
        # "escritura" ya muestran el texto en pantalla via el efecto
        # typewriter. Si ademas se les monta el subtitulo SRT global, las
        # dos capas de texto quedan una encima de la otra e ilegibles.
        # Se excluyen del SRT -- el texto ya está visible por otro camino.
        if (sc.get('tipo') or '').lower() == 'escritura':
            cursor = seg_end
            continue
        sentences = split_sentences(sc.get('text', '')) or [sc.get('text', '')]
        total_chars_seg = sum(len(s) for s in sentences) or 1
        c2 = seg_start
        for s in sentences:
            frac = len(s) / total_chars_seg
            d = dur * frac
            start = c2
            end = min(c2 + d, seg_end)
            entries.append((idx, start, end, s))
            idx += 1
            c2 = end
        cursor = seg_end

    with open(srt_path, 'w', encoding='utf-8') as srt:
        for idx, start, end, s in entries:
            srt.write(f"{idx}\n{format_time(start)} --> {format_time(end)}\n{s}\n\n")

    sub_style = (
        "FontName=Arial,FontSize=26,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BackColour=&H80000000,Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=40"
    )

    # Construir el comando: base de video + audio + N imagenes de overlay como inputs extra
    cmd = ['ffmpeg', '-y', '-i', f"{workdir}/base.mp4"]
    if has_audio:
        cmd += ['-i', audio]
    for ev in overlay_events:
        cmd += ['-loop', '1', '-t', str(round(ev['end'] - ev['start'] + 0.2, 2)), '-i', ev['path']]

    # filter_complex: subtitulos sobre la base, luego cada overlay encadenado con su ventana de tiempo
    filters = [f"[0:v]subtitles={srt_path}:force_style='{sub_style}'[v0]"]
    last_label = 'v0'
    audio_input_count = 1 if has_audio else 0
    for i, ev in enumerate(overlay_events):
        input_idx = 1 + audio_input_count + i  # despues de base(0) y audio(si existe)
        scaled = f"ov{i}"
        out_label = f"v{i+1}"
        filters.append(f"[{input_idx}:v]scale=220:220,format=rgba,fade=in:st=0:d=0.3:alpha=1,fade=out:st={round(ev['end']-ev['start']-0.3,2)}:d=0.3:alpha=1[{scaled}]")
        filters.append(
            f"[{last_label}][{scaled}]overlay=W-w-40:H-h-40:"
            f"enable='between(t,{round(ev['start'],2)},{round(ev['end'],2)})'[{out_label}]"
        )
        last_label = out_label

    filter_complex = ';'.join(filters)
    cmd += ['-filter_complex', filter_complex, '-map', f'[{last_label}]']
    if has_audio:
        cmd += ['-map', '1:a']
    cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '23']
    if has_audio:
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
    print(f"  Renderizando video final con subtitulos + {len(overlay_events)} overlays...", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg render final colgado mas de 300s")
    if result.returncode != 0:
        raise Exception(f"FFmpeg render error: {result.stderr[-500:]}")
    print("  -> render final listo", flush=True)

    final_path = f"{workdir}/final.mp4"
    print(f"=== Video final: {os.path.getsize(final_path)} bytes ===")

    video_url = upload_to_github_release(final_path, github_token)

    print("=== Enviando preview a Telegram ===")
    with open(final_path, 'rb') as f:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendVideo",
            data={"chat_id": chat_id, "caption": f"🐑 {titulo} listo!"},
            files={"video": ("video.mp4", f, "video/mp4")},
            timeout=300
        )
    result_tg = r.json()
    print(f"Telegram ok: {result_tg.get('ok')}")

    requests.post(callback_url, json={'status': 'done', 'video_url': video_url, 'titulo': titulo}, timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    tb = traceback.format_exc()
    print(f"ERROR:\n{tb}")
    requests.post(callback_url, json={'video_url': '', 'status': 'error', 'message': str(e)}, timeout=15)
    sys.exit(1)
