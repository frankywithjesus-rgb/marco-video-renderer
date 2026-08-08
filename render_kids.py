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
XFADE_DUR = 0.6

# Movimientos de camara: scale a 120% + crop animado con 't'
# Todos usan escala 1.2x (1536x864) y recortan a 1280x720 moviéndose
# El crop offset maximo: x=256 (1536-1280), y=144 (864-720)
# crop=w:h:x:y donde x,y son expresiones de tiempo
# (desc, crop_x_expr_ffmpeg, crop_y_expr_ffmpeg)
# Escala siempre a 1536x864 (1.2x de 1280x720).
# crop=1280:720:X:Y donde X in [0,256] y Y in [0,144].
# Expresiones usan 't' (tiempo en segundos) -- soportado en TODAS las versiones de ffmpeg.
MOVEMENTS = [
    ("paneo dcha",   "min(256,t*10)",          "72"),
    ("paneo izq",    "max(0,256-t*10)",         "72"),
    ("centro fijo",  "128",                        "72"),
    ("paneo abajo",  "128",                        "min(144,t*8)"),
    ("paneo arriba", "128",                        "max(0,144-t*8)"),
    ("diagonal",     "min(256,t*9)",             "min(144,t*6)"),
    ("diag inv",     "max(0,256-t*9)",           "max(0,144-t*6)"),
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
    """Imagen -> clip con movimiento via scale+crop animado (sin zoompan)."""
    mv = MOVEMENTS[movement_idx % len(MOVEMENTS)]
    desc, cx_expr, cy_expr = mv
    # escalar a 1536x864 (1.2x de 1280x720), luego crop animado
    vf = (
        f"scale=1536:864:force_original_aspect_ratio=increase,crop=1536:864,"
        f"crop={W}:{H}:{cx_expr}:{cy_expr},"
        "format=yuv420p"
    )
    print(f"  Clip mov={movement_idx % len(MOVEMENTS)} ({desc}): {dur:.1f}s", flush=True)
    try:
        result = subprocess.run([
            'ffmpeg', '-y',
            '-f', 'image2', '-loop', '1', '-framerate', str(FPS), '-i', inp,
            '-t', str(dur), '-vf', vf,
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25',
            '-r', str(FPS), '-an', out
        ], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception(f"FFmpeg clip colgado en {inp} ({dur:.1f}s)")
    if result.returncode != 0:
        raise Exception(f"FFmpeg clip error: {result.stderr[-500:]}")
    print(f"  -> listo: {out}", flush=True)

def concat_with_xfade(clips, durations, out_path):
    """Encadena clips con transiciones xfade suaves."""
    if len(clips) == 1:
        subprocess.run(['ffmpeg', '-y', '-i', clips[0], '-c', 'copy', out_path],
                       check=True, capture_output=True, timeout=60)
        return

    TRANSITIONS = ['fade', 'dissolve', 'wipeleft', 'wiperight', 'slideleft', 'slideright', 'fadeblack']
    n = len(clips)
    cmd = ['ffmpeg', '-y']
    for c in clips:
        cmd += ['-i', c]

    filters = []
    prev = '0:v'
    offset = 0.0
    for i in range(1, n):
        trans = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        offset_val = offset + durations[i - 1] - XFADE_DUR
        offset_val = max(0.01, offset_val)
        out_label = f"xf{i}" if i < n - 1 else "vout"
        filters.append(
            f"[{prev}][{i}:v]xfade=transition={trans}"
            f":duration={XFADE_DUR}:offset={offset_val:.3f}[{out_label}]"
        )
        prev = out_label
        offset += durations[i - 1] - XFADE_DUR

    cmd += ['-filter_complex', ';'.join(filters),
            '-map', '[vout]',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', out_path]
    print(f"  xfade: {n} clips, transiciones {TRANSITIONS[:n-1]}", flush=True)
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
        headers=headers).json()
    for asset in assets:
        requests.delete(
            f"https://api.github.com/repos/{repo}/releases/assets/{asset['id']}",
            headers=headers)
        print(f"  Borrado: {asset['name']}")
    filename = f"episodio_{int(time.time())}.mp4"
    with open(path, 'rb') as f:
        r = requests.post(
            f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={filename}",
            headers={**headers, "Content-Type": "video/mp4"},
            data=f, timeout=300)
    data = r.json()
    if r.status_code in (200, 201) and 'browser_download_url' in data:
        print(f"URL: {data['browser_download_url']}")
        return data['browser_download_url']
    raise Exception(f"GitHub Release error: {r.status_code} {data}")

def format_time(secs):
    h = int(secs // 3600); m = int((secs % 3600) // 60)
    s = int(secs % 60); ms = int((secs % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]

try:
    print("=== Descargando imagenes ===")
    images = []
    for i, sc in enumerate(scenes):
        images.append(download(sc['image'], f"{workdir}/s{i+1}"))

    has_audio = bool(audio_url and len(audio_url) > 10)
    if has_audio:
        print("=== Descargando audio ===")
        r = requests.get(audio_url, timeout=120, stream=True)
        audio = f"{workdir}/audio.mp3"
        with open(audio, 'wb') as f:
            for chunk in r.iter_content(8192): f.write(chunk)
        print(f"Audio: {os.path.getsize(audio)} bytes")
        total_duration = get_audio_duration(audio)
    else:
        total_duration = 60.0

    char_counts = [max(len(sc.get('text', '')), 1) for sc in scenes]
    total_chars = sum(char_counts)
    n_transitions = len(scenes) - 1
    effective = total_duration + n_transitions * XFADE_DUR
    durations = [effective * c / total_chars for c in char_counts]
    print(f"Total: {total_duration:.1f}s, {len(scenes)} escenas, duraciones: {[round(d,1) for d in durations]}")

    print("=== Generando clips con movimiento variado ===")
    random.seed(42)
    order = list(range(len(MOVEMENTS)))
    random.shuffle(order)
    clips = []
    for i, (img, dur) in enumerate(zip(images, durations)):
        out = f"{workdir}/c{i+1}.mp4"
        image_to_clip(img, out, dur, order[i % len(order)])
        clips.append(out)

    print("=== Concatenando con xfade ===")
    base_path = f"{workdir}/base.mp4"
    concat_with_xfade(clips, durations, base_path)

    print("=== Generando subtitulos ===")
    srt_path = f"{workdir}/subs.srt"
    entries = []; idx = 1; cursor = 0.0
    for sc, dur in zip(scenes, durations):
        sentences = split_sentences(sc.get('text', '')) or [sc.get('text', '')]
        total_c = sum(len(s) for s in sentences) or 1
        c2 = cursor
        for s in sentences:
            frac = len(s) / total_c
            d = dur * frac
            entries.append((idx, c2, min(c2 + d, cursor + dur), s))
            idx += 1; c2 += d
        cursor += dur - XFADE_DUR

    with open(srt_path, 'w', encoding='utf-8') as srt:
        for idx, start, end, s in entries:
            srt.write(f"{idx}\n{format_time(start)} --> {format_time(end)}\n{s}\n\n")

    sub_style = (
        "FontName=Arial,FontSize=28,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&H90000000,"
        "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=50"
    )

    print("=== Render final ===")
    cmd = ['ffmpeg', '-y', '-i', base_path]
    if has_audio: cmd += ['-i', audio]
    cmd += ['-vf', f"subtitles={srt_path}:force_style='{sub_style}'", '-map', '0:v']
    if has_audio: cmd += ['-map', '1:a']
    cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '22']
    if has_audio: cmd += ['-c:a', 'aac', '-b:a', '128k', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
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
            files={"video": ("video.mp4", f, "video/mp4")}, timeout=300)
    print(f"Telegram ok: {r.json().get('ok')}")

    requests.post(callback_url, json={'status': 'done', 'video_url': video_url, 'titulo': titulo}, timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    print(f"ERROR:\n{traceback.format_exc()}")
    requests.post(callback_url, json={'video_url': '', 'status': 'error', 'message': str(e)}, timeout=15)
    sys.exit(1)
