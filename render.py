import json, os, subprocess, requests, tempfile, sys, traceback, base64

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']
bot_token = os.environ.get('BOT_TOKEN', '')
chat_id = os.environ.get('CHAT_ID', '8946671215') or '8946671215'

bgs = [payload['bg1'], payload['bg2'], payload['bg3'], payload['bg4']]
audio_url = payload.get('audioUrl', '')
texto1 = payload['texto1']
texto2 = payload['texto2']
texto3 = payload['texto3']
texto4 = payload['texto4']
duration = float(payload.get('duration', 60))
titulo = payload.get('titulo', 'Historia viral - MarcoPeru')

workdir = tempfile.mkdtemp()
FALLBACK = "https://videos.pexels.com/video-files/6945204/6945204-hd_1080_1920_30fps.mp4"

PEXELS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.pexels.com/",
    "Accept": "*/*"
}

def download(url, path):
    for h in [PEXELS_HEADERS, {}]:
        try:
            r = requests.get(url, timeout=120, stream=True, headers=h)
            if r.status_code == 200:
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                size = os.path.getsize(path)
                if size > 10000:
                    print(f"OK {path}: {size} bytes")
                    return path
        except Exception as e:
            print(f"  Intento fallido: {e}")
    print(f"Usando fallback para {path}")
    r = requests.get(FALLBACK, timeout=120, stream=True, headers=PEXELS_HEADERS)
    with open(path, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return path

def download_audio(url, path):
    """Descarga audio con seguimiento de redirecciones (necesario para Google Drive)"""
    session = requests.Session()
    session.max_redirects = 10
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*"
    }
    r = session.get(url, timeout=180, stream=True, headers=headers, allow_redirects=True)
    r.raise_for_status()
    with open(path, 'wb') as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)
    size = os.path.getsize(path)
    print(f"Audio descargado: {size} bytes")
    if size < 1000:
        raise Exception(f"Audio demasiado pequeño ({size} bytes) - probable error de descarga")
    return path

def get_audio_duration(path):
    r = subprocess.run([
        'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path
    ], capture_output=True, text=True)
    stdout = r.stdout.strip()
    if not stdout:
        raise Exception(f"ffprobe no retornó datos para {path}. stderr: {r.stderr[:200]}")
    data = json.loads(stdout)
    dur = float(data['format']['duration'])
    print(f"Duracion del audio: {dur:.1f}s")
    return dur

def loop_video_to_duration(inp, out, dur):
    result = subprocess.run([
        'ffmpeg', '-y',
        '-stream_loop', '-1',
        '-i', inp,
        '-t', str(dur),
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', out
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg loop error: {result.stderr[-200:]}")

def upload_to_fileio(path):
    """Sube el video a file.io y retorna URL publica temporal (expira en 1 hora)"""
    print("=== Subiendo video a file.io ===")
    with open(path, 'rb') as f:
        r = requests.post(
            'https://file.io/?expires=1h',
            files={'file': ('video.mp4', f, 'video/mp4')},
            timeout=300
        )
    raw = r.text
    print(f"file.io response: {raw[:200]}")
    if not raw.strip():
        raise Exception("file.io devolvio respuesta vacia")
    data = r.json()
    if data.get('success'):
        url = data['link']
        print(f"URL publica: {url}")
        return url
    raise Exception(f"file.io error: {data}")

try:
    print("=== Descargando videos ===")
    videos = []
    for i, url in enumerate(bgs):
        v = download(url, f"{workdir}/v{i+1}.mp4")
        videos.append(v)

    has_audio = bool(audio_url and len(audio_url) > 10)
    audio_duration = duration
    if has_audio:
        print(f"=== Descargando audio: {audio_url[:80]} ===")
        audio = f"{workdir}/audio.mp3"
        download_audio(audio_url, audio)
        audio_duration = get_audio_duration(audio)
        duration = audio_duration

    seg = duration / 4
    print(f"Duracion total del video: {duration:.1f}s ({duration/60:.1f} min)")

    print("=== Procesando clips con loop ===")
    for i, v in enumerate(videos):
        loop_video_to_duration(v, f"{workdir}/c{i+1}.mp4", seg)

    print("=== Concatenando ===")
    with open(f"{workdir}/list.txt", 'w') as f:
        for i in range(1, 5):
            f.write(f"file '{workdir}/c{i}.mp4'\n")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', f"{workdir}/list.txt", '-c', 'copy', f"{workdir}/base.mp4"
    ], check=True, capture_output=True)

    print("=== Renderizando con subtitulos y audio completo ===")
    t = [0, seg, seg*2, seg*3]
    e = [seg, seg*2, seg*3, duration]
    txts = [texto1, texto2, texto3, texto4]
    srt_path = f"{workdir}/subs.srt"

    def format_time(secs):
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = int(secs % 60)
        ms = int((secs % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(srt_path, 'w', encoding='utf-8') as srt:
        for i in range(4):
            srt.write(f"{i+1}\n")
            srt.write(f"{format_time(t[i])} --> {format_time(e[i])}\n")
            srt.write(f"{txts[i]}\n\n")

    vf = (
        "colorchannelmixer=rr=0.4:gg=0.4:bb=0.4,"
        f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=22,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
        "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=60'"
    )

    cmd = ['ffmpeg', '-y', '-i', f"{workdir}/base.mp4"]
    if has_audio:
        cmd += ['-i', audio]
    cmd += ['-vf', vf, '-c:v', 'libx264', '-preset', 'fast', '-crf', '28']
    if has_audio:
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-map', '0:v', '-map', '1:a', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg render error: {result.stderr[-400:]}")

    final_path = f"{workdir}/final.mp4"
    final_size = os.path.getsize(final_path)
    print(f"=== Video final: {final_size} bytes ===")

    # 1. Subir a file.io para obtener URL publica
    video_url = upload_to_fileio(final_path)

    # 2. Enviar a Telegram (solo si hay bot_token)
    if bot_token:
        print("=== Enviando a Telegram ===")
        with open(final_path, 'rb') as f:
            r = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendVideo",
                data={"chat_id": chat_id, "caption": "🎬 Video listo! Publicando en YouTube y Facebook..."},
                files={"video": ("video.mp4", f, "video/mp4")},
                timeout=300
            )
        raw = r.text
        if not raw.strip():
            print("Telegram: respuesta vacia, ignorando")
        else:
            result_tg = r.json()
            print(f"Telegram ok: {result_tg.get('ok')}")
            if not result_tg.get('ok'):
                print(f"Telegram warning: {result_tg}")
    else:
        print("BOT_TOKEN no configurado, saltando Telegram directo (n8n lo maneja)")

    # 3. Callback a n8n con URL real y titulo
    requests.post(callback_url, json={
        'status': 'done',
        'video_url': video_url,
        'titulo': titulo
    }, timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    tb = traceback.format_exc()
    print(f"ERROR:\n{tb}")
    requests.post(callback_url, json={'video_url': '', 'status': 'error', 'message': str(e)}, timeout=15)
    sys.exit(1)
