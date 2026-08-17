import json, os, subprocess, requests, tempfile, sys, traceback, time, re, random, math

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
FPS = 30         # 30fps para movimiento fluido
W, H = 1280, 720
# Escalar a 1.5x para tener margen real de movimiento
SW, SH = 1920, 1080  # 1920/1280 = 1.5x, 1080/720 = 1.5x
# Offset máximo de crop: x=640 (1920-1280), y=360 (1080-720)
MAX_X, MAX_Y = 640, 360

XFADE_DUR = 0.8   # transición más larga para más impacto
MUSIC_URL = "https://cdn.pixabay.com/audio/2025/08/05/09-24-29-986_200x200.mp3"
MUSIC_VOL = 0.10

def ease_out_quad(t):
    """Aceleración al inicio, desaceleración al final (cinematic feel)"""
    return 1 - (1 - t) ** 2

def ease_in_out(t):
    """Suave al inicio y al final"""
    return 0.5 - math.cos(math.pi * t) / 2

# Movimientos cinemáticos: (desc, x_start, x_end, y_start, y_end, easing_fn)
MOVEMENTS = [
    # Zoom-in dramático al centro
    ("push-in",        320, 160,  180,  90,  "ease_in_out"),
    # Paneo lateral rápido (slide)
    ("slide-left",     600,   0,  180, 180,  "ease_out"),
    ("slide-right",      0, 600,  180, 180,  "ease_out"),
    # Barrido vertical (tilt)
    ("tilt-up",        320, 320,  340,   0,  "ease_in_out"),
    ("tilt-down",      320, 320,    0, 340,  "ease_in_out"),
    # Diagonal dramática
    ("diagonal-in",    600,   0,  340,   0,  "ease_in_out"),
    ("diagonal-out",     0, 600,    0, 340,  "ease_in_out"),
    # Pull-back (alejamiento)
    ("pull-back",      160, 320,   90, 180,  "ease_in_out"),
    # Floating (movimiento mínimo, muy suave, para escenas de reflexión)
    ("float",          320, 280,  180, 150,  "ease_in_out"),
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
        print(f"  Fallo: {e}")
    print(f"Fallback para {base_path}")
    final_path = base_path + '.jpg'
    r = requests.get(FALLBACK, timeout=120, stream=True)
    with open(final_path, 'wb') as f:
        for chunk in r.iter_content(8192): f.write(chunk)
    return final_path

def get_audio_duration(path):
    r = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path],
        capture_output=True, text=True)
    dur = float(json.loads(r.stdout)['format']['duration'])
    print(f"Audio: {dur:.1f}s")
    return dur

def image_to_clip(inp, out, dur, movement_idx):
    """Genera un clip con movimiento cinematográfico via segmentos con easing.
    Escala a 1.5x y mueve el crop con interpolación suave para efecto Ken Burns avanzado."""
    mv = MOVEMENTS[movement_idx % len(MOVEMENTS)]
    desc, x_start, x_end, y_start, y_end, easing = mv

    # Clamp a los límites máximos
    x_start = max(0, min(MAX_X, x_start))
    x_end   = max(0, min(MAX_X, x_end))
    y_start = max(0, min(MAX_Y, y_start))
    y_end   = max(0, min(MAX_Y, y_end))

    # Número de segmentos — más FPS y más segmentos = movimiento más fluido
    SEG_DUR = 0.15  # segmentos de 150ms para suavidad
    n_segs = max(2, int(dur / SEG_DUR))
    seg_dur = dur / n_segs

    tmp_clips = []
    for seg_i in range(n_segs):
        t = (seg_i + 0.5) / n_segs  # fracción 0..1

        # Aplicar easing
        if easing == "ease_in_out":
            t_eased = ease_in_out(t)
        else:  # ease_out
            t_eased = ease_out_quad(t)

        cx = int(x_start + (x_end - x_start) * t_eased)
        cy = int(y_start + (y_end - y_start) * t_eased)
        cx = max(0, min(MAX_X, cx))
        cy = max(0, min(MAX_Y, cy))

        seg_out = f"{out}.seg{seg_i}.mp4"
        tmp_clips.append(seg_out)

        vf = (
            f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
            f"crop={W}:{H}:{cx}:{cy},format=yuv420p"
        )

        r = subprocess.run([
            'ffmpeg', '-y',
            '-f', 'image2', '-loop', '1', '-framerate', str(FPS), '-i', inp,
            '-t', str(round(seg_dur, 4)), '-vf', vf,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '26',
            '-r', str(FPS), '-an', seg_out
        ], capture_output=True, text=True, timeout=60)

        if r.returncode != 0:
            raise Exception(f"FFmpeg seg error (mov={movement_idx},seg={seg_i}): {r.stderr[-300:]}")

    # Concatenar segmentos
    list_path = out + '.list.txt'
    with open(list_path, 'w') as lf:
        for tc in tmp_clips:
            lf.write(f"file '{tc}'\n")

    r = subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_path,
        '-c', 'copy', out
    ], capture_output=True, text=True, timeout=120)

    for tc in tmp_clips:
        try: os.remove(tc)
        except: pass
    try: os.remove(list_path)
    except: pass

    if r.returncode != 0:
        raise Exception(f"FFmpeg concat clip error: {r.stderr[-300:]}")
    print(f"  -> {out} ({n_segs} segs, {desc})", flush=True)

def concat_with_xfade(clips, durations, out_path):
    """Encadena clips con transiciones cinemáticas variadas."""
    if len(clips) == 1:
        subprocess.run(['ffmpeg', '-y', '-i', clips[0], '-c', 'copy', out_path],
                       check=True, capture_output=True, timeout=60)
        return

    # Transiciones más impactantes
    TRANSITIONS = [
        'fade', 'fadeblack', 'dissolve',
        'slideleft', 'slideright',
        'wipeleft', 'wiperight',
        'smoothleft', 'smoothright',
        'circlecrop', 'rectcrop',
    ]

    n = len(clips)
    cmd = ['ffmpeg', '-y']
    for c in clips:
        cmd += ['-i', c]

    filters = []
    prev = '0:v'
    offset = 0.0
    for i in range(1, n):
        trans = TRANSITIONS[(i - 1) % len(TRANSITIONS)]
        offset_val = max(0.01, offset + durations[i - 1] - XFADE_DUR)
        out_label = f"xf{i}" if i < n - 1 else "vout"
        filters.append(
            f"[{prev}][{i}:v]xfade=transition={trans}"
            f":duration={XFADE_DUR}:offset={offset_val:.3f}[{out_label}]"
        )
        prev = out_label
        offset += durations[i - 1] - XFADE_DUR

    cmd += ['-filter_complex', ';'.join(filters),
            '-map', '[vout]',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-an', out_path]
    print(f"  xfade: {n} clips, transiciones variadas", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg xfade timeout")
    if result.returncode != 0:
        raise Exception(f"FFmpeg xfade error: {result.stderr[-500:]}")
    print("  -> xfade listo", flush=True)

def upload_to_github_release(path, token):
    print("=== Subiendo a GitHub Release ===")
    headers = {"Authorization": f"token {token}"}
    safe_title = re.sub(r'[^a-zA-Z0-9_-]', '_', titulo)[:40]
    filename = f"{safe_title}_{int(time.time())}.mp4"
    with open(path, 'rb') as f:
        r = requests.post(
            f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={filename}",
            headers={**headers, "Content-Type": "video/mp4"},
            data=f, timeout=300)
    data = r.json()
    if r.status_code in (200, 201) and 'browser_download_url' in data:
        print(f"URL: {data['browser_download_url']}")
        return data['browser_download_url']
    if r.status_code == 422:
        filename2 = f"{safe_title}_{int(time.time())+1}.mp4"
        with open(path, 'rb') as f:
            r2 = requests.post(
                f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?name={filename2}",
                headers={**headers, "Content-Type": "video/mp4"},
                data=f, timeout=300)
        data2 = r2.json()
        if r2.status_code in (200, 201):
            return data2['browser_download_url']
    raise Exception(f"GitHub Release error: {r.status_code} {data}")

def format_time(secs):
    h = int(secs // 3600); m = int((secs % 3600) // 60)
    s = int(secs % 60); ms = int((secs % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def split_sentences(text):
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]

try:
    print("=== Descargando imágenes ===")
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

    print("=== Descargando música de fondo ===")
    music_path = None
    try:
        music_dl = f"{workdir}/music.mp3"
        rm = requests.get(MUSIC_URL, timeout=60, stream=True)
        if rm.status_code == 200:
            with open(music_dl, 'wb') as f:
                for chunk in rm.iter_content(8192): f.write(chunk)
            if os.path.getsize(music_dl) > 10000:
                music_path = music_dl
                print(f"  Música: {os.path.getsize(music_dl)} bytes")
    except Exception as em:
        print(f"  Música omitida: {em}")

    char_counts = [max(len(sc.get('text', '')), 1) for sc in scenes]
    total_chars = sum(char_counts)
    n_transitions = len(scenes) - 1
    effective = total_duration + n_transitions * XFADE_DUR
    durations = [effective * c / total_chars for c in char_counts]
    print(f"Total: {total_duration:.1f}s, {len(scenes)} escenas")

    print("=== Generando clips cinemáticos ===")
    # Orden mixto para que no se repita el mismo movimiento en escenas seguidas
    random.seed(len(scenes) + len(titulo))
    order = list(range(len(MOVEMENTS)))
    random.shuffle(order)
    clips = []
    for i, (img, dur) in enumerate(zip(images, durations)):
        out = f"{workdir}/c{i+1}.mp4"
        image_to_clip(img, out, dur, order[i % len(order)])
        clips.append(out)

    print("=== Concatenando con xfade cinemático ===")
    base_path = f"{workdir}/base.mp4"
    concat_with_xfade(clips, durations, base_path)

    print("=== Generando subtítulos ===")
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

    # Subtítulos más impactantes: fuente más grande, fondo negro sólido
    sub_style = (
        "FontName=Arial,FontSize=32,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BackColour=&HA0000000,"
        "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=55"
    )

    print("=== Render final ===")
    cmd = ['ffmpeg', '-y', '-i', base_path]
    if has_audio: cmd += ['-i', audio]
    if music_path: cmd += ['-i', music_path]

    sub_vf = f"subtitles='{srt_path}':force_style='{sub_style}'"

    if has_audio and music_path:
        audio_filter = (
            f"[1:a]volume=1.0[voz];"
            f"[2:a]volume={MUSIC_VOL},aloop=loop=-1:size=2e+09[musica];"
            f"[voz][musica]amix=inputs=2:duration=first:dropout_transition=2[audio_final]"
        )
        cmd += ['-filter_complex', audio_filter]
        cmd += ['-vf', sub_vf, '-map', '0:v', '-map', '[audio_final]']
        cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
                '-c:a', 'aac', '-b:a', '128k', '-shortest']
    elif has_audio:
        cmd += ['-vf', sub_vf, '-map', '0:v', '-map', '1:a']
        cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
                '-c:a', 'aac', '-b:a', '128k', '-shortest']
    else:
        cmd += ['-vf', sub_vf, '-map', '0:v']
        cmd += ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20']

    cmd.append(f"{workdir}/final.mp4")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg render final timeout")
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
            data={"chat_id": chat_id, "caption": f"🎬 {titulo}"},
            files={"video": ("video.mp4", f, "video/mp4")},
            timeout=300)
    print(f"Telegram ok: {r.json().get('ok')}")

    requests.post(callback_url,
        json={'status': 'done', 'video_url': video_url, 'titulo': titulo},
        timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    print(f"ERROR:\n{traceback.format_exc()}")
    requests.post(callback_url,
        json={'video_url': '', 'status': 'error', 'message': str(e)},
        timeout=15)
    sys.exit(1)
