import json, os, subprocess, requests, tempfile, sys, traceback, time, re, random

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']
bot_token = os.environ.get('BOT_TOKEN', '')
chat_id = os.environ.get('CHAT_ID', '8946671215') or '8946671215'
github_token = os.environ.get('GH_TOKEN', '')
release_id = os.environ.get('RELEASE_ID', '367237403')
repo = os.environ.get('REPO', 'frankywithjesus-rgb/marco-video-renderer')

scenes = payload['scenes']
audio_url = payload.get('audioUrl', '')
titulo = payload.get('titulo', 'Escuela Sabática')

workdir = tempfile.mkdtemp()
FALLBACK = "https://images.pexels.com/photos/1367192/pexels-photo-1367192.jpeg"
FPS = 24
W, H = 1280, 720
XFADE_DUR = 0.5  # segundos de transición entre escenas

# Movimientos disponibles — alternan por escena para dar variedad
MOVEMENTS = [
    # (zoom_expr, x_expr, y_expr, descripcion)
    ("zoom+0.001",  "iw/2-(iw/zoom/2)",        "ih/2-(ih/zoom/2)",        "zoom-in centro"),
    ("zoom+0.001",  "0",                         "ih/2-(ih/zoom/2)",        "zoom-in paneo dcha"),
    ("zoom+0.001",  "iw-(iw/zoom)",              "ih/2-(ih/zoom/2)",        "zoom-in paneo izq"),
    ("zoom+0.001",  "iw/2-(iw/zoom/2)",          "0",                       "zoom-in paneo abajo"),
    ("if(lte(zoom,1.0),1.2,zoom-0.001)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", "zoom-out centro"),
    ("if(lte(zoom,1.0),1.2,zoom-0.001)", "0",                "0",                "zoom-out esquina TL"),
    ("zoom+0.0008", "iw/2-(iw/zoom/2)+{pan}",   "ih/2-(ih/zoom/2)",        "zoom-in paneo diagonal"),
]

def is_valid_image(path):
    try:
        with open(path, 'rb') as f:
            header = f.read(12)
        if header[:2] == b'\xff\xd8': return True
        if header[:8] == b'\x89PNG\r\n\x1a\n': return True
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP': return True
        return False
    except Exception:
        return False

def detect_ext(path):
    with open(path, 'rb') as f:
        header = f.read(12)
    if header[:2] == b'\xff\xd8': return '.jpg'
    if header[:8] == b'\x89PNG\r\n\x1a\n': return '.png'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP': return '.webp'
    return '.jpg'

def download(url, base_path):
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
    r = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path],
        capture_output=True, text=True
    )
    dur = float(json.loads(r.stdout)['format']['duration'])
    print(f"Duracion del audio: {dur:.1f}s")
    return dur

def image_to_clip(inp, out, dur, movement_idx):
    """Convierte una imagen en un clip con movimiento de cámara variado."""
    frames = max(1, int(dur * FPS))
    mv = MOVEMENTS[movement_idx % len(MOVEMENTS)]
    zoom_expr = mv[0]
    x_expr = mv[1].replace('{pan}', f'(t/{dur})*80')  # paneo de hasta 80px
    y_expr = mv[2]
    desc = mv[3]

    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
        f":d={frames}:s={W}x{H}:fps={FPS},"
        "format=yuv420p"
    )
    print(f"  Clip {movement_idx+1}: {dur:.1f}s, {frames}f, '{desc}'", flush=True)
    try:
        result = subprocess.run([
            'ffmpeg', '-y', '-f', 'image2', '-loop', '1', '-i', inp,
            '-t', str(dur), '-vf', vf,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-an', out
        ], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception(f"FFmpeg clip colgado en {inp} ({dur:.1f}s, {frames}f)")
    if result.returncode != 0:
        raise Exception(f"FFmpeg clip error: {result.stderr[-400:]}")
    print(f"  -> listo: {out}", flush=True)

def concat_with_xfade(clips, durations, out_path):
    """Encadena clips con transiciones xfade entre ellos."""
    if len(clips) == 1:
        subprocess.run(
            ['ffmpeg', '-y', '-i', clips[0], '-c', 'copy', out_path],
            check=True, capture_output=True, timeout=120
        )
        return

    # Tipos de transición que alternan — xfade soporta muchos
    TRANSITIONS = ['fade', 'dissolve', 'wipeleft', 'wiperight', 'slideleft', 'slideright', 'fadeblack']

    n = len(clips)
    # Inputs
    cmd = ['ffmpeg', '-y']
    for c in clips:
        cmd += ['-i', c]

    # filter_complex encadenado: [0][1]xfade → tmp0, [tmp0][2]xfade → tmp1, ...
    filters = []
    prev = '0:v'
    cumulative_offset = 0.0
    for i in range(1, n):
        trans = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        offset = cumulative_offset + durations[i - 1] - XFADE_DUR
        offset = max(0.0, offset)
        out_label = f"xf{i}" if i < n - 1 else "vout"
        filters.append(
            f"[{prev}][{i}:v]xfade=transition={trans}"
            f":duration={XFADE_DUR}:offset={offset:.3f}[{out_label}]"
        )
        prev = out_label
        cumulative_offset += durations[i - 1] - XFADE_DUR

    fc = ';'.join(filters)
    cmd += ['-filter_complex', fc, '-map', '[vout]',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', out_path]
    print(f"  xfade entre {n} clips ({', '.join(TRANSITIONS[:n-1])})...", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg xfade colgado mas de 300s")
    if result.returncode != 0:
        raise Exception(f"FFmpeg xfade error: {result.stderr[-500:]}")
    print("  -> xfade listo", flush=True)

def upload_to_github_release(path, token):
    print("=== Subiendo video a GitHub Release ===")
    headers = {"Authorization": f"token {token}"}
    assets = requests.get(
        f"https://api.github.com/repos/{repo}/releases/{release_id}/assets",
        headers=headers
    ).json()
    for asset in assets:
        requests.delete(
            f"https://api.github.com/repos/{repo}/releases/assets/{asset['id']}",
            headers=headers
        )
        print(f"  Borrado asset anterior: {asset['name']}")
    filename = f"episodio_{int(time.time())}.mp4"
    upload_url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={filename}"
    with open(path, 'rb') as f:
        r = requests.post(
            upload_url,
            headers={**headers, "Content-Type": "video/mp4"},
            data=f, timeout=300
        )
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
    # Compensar el tiempo "robado" por xfade en cada transición
    n_transitions = len(scenes) - 1
    effective_duration = total_duration + n_transitions * XFADE_DUR
    durations = [effective_duration * c / total_chars for c in char_counts]
    print(f"Duracion total: {total_duration:.1f}s, {len(scenes)} escenas, {n_transitions} transiciones xfade")
    print(f"Duraciones por escena: {[round(d,1) for d in durations]}")

    print("=== Generando clips con movimiento variado ===")
    # Mezclar el orden de movimientos aleatoriamente pero de forma reproducible
    random.seed(len(scenes))
    movement_order = list(range(len(MOVEMENTS)))
    random.shuffle(movement_order)
    clips = []
    for i, (img, dur) in enumerate(zip(images, durations)):
        out = f"{workdir}/c{i+1}.mp4"
        image_to_clip(img, out, dur, movement_order[i % len(movement_order)])
        clips.append(out)

    print("=== Concatenando con transiciones xfade ===")
    base_path = f"{workdir}/base.mp4"
    concat_with_xfade(clips, durations, base_path)

    print("=== Generando subtitulos .srt ===")
    srt_path = f"{workdir}/subs.srt"
    entries = []
    idx = 1
    cursor = 0.0
    for sc, dur in zip(scenes, durations):
        seg_start = cursor
        seg_end = cursor + dur
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
        cursor += dur - XFADE_DUR  # ajustar cursor por el overlap del xfade

    with open(srt_path, 'w', encoding='utf-8') as srt:
        for idx, start, end, s in entries:
            srt.write(f"{idx}\n{format_time(start)} --> {format_time(end)}\n{s}\n\n")

    sub_style = (
        "FontName=Arial,FontSize=28,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H90000000,"
        "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=50"
    )

    print("=== Render final (subtitulos + audio) ===")
    cmd = ['ffmpeg', '-y', '-i', base_path]
    if has_audio:
        cmd += ['-i', audio]
    cmd += [
        '-vf', f"subtitles={srt_path}:force_style='{sub_style}'",
        '-map', '0:v'
    ]
    if has_audio:
        cmd += ['-map', '1:a']
    cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '22']
    if has_audio:
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
    print(f"  Renderizando...", flush=True)
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

    print("=== Enviando a Telegram ===")
    with open(final_path, 'rb') as f:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendVideo",
            data={"chat_id": chat_id, "caption": f"🎬 {titulo} listo!"},
            files={"video": ("video.mp4", f, "video/mp4")},
            timeout=300
        )
    print(f"Telegram ok: {r.json().get('ok')}")

    requests.post(callback_url, json={'status': 'done', 'video_url': video_url, 'titulo': titulo}, timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    tb = traceback.format_exc()
    print(f"ERROR:\n{tb}")
    requests.post(callback_url, json={'video_url': '', 'status': 'error', 'message': str(e)}, timeout=15)
    sys.exit(1)
