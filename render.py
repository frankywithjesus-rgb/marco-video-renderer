import json, os, subprocess, requests, tempfile, sys, traceback

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

def get_audio_duration(path):
    r = subprocess.run([
        'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path
    ], capture_output=True, text=True)
    data = json.loads(r.stdout)
    dur = float(data['format']['duration'])
    print(f"Duracion del audio: {dur:.1f}s")
    return dur

def loop_video_to_duration(inp, out, dur):
    """Hacer loop del video hasta alcanzar la duracion necesaria"""
    result = subprocess.run([
        'ffmpeg', '-y',
        '-stream_loop', '-1',  # loop infinito
        '-i', inp,
        '-t', str(dur),
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', out
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg loop error: {result.stderr[-200:]}")

def esc(t):
    return t.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")

try:
    print("=== Descargando videos ===")
    videos = []
    for i, url in enumerate(bgs):
        v = download(url, f"{workdir}/v{i+1}.mp4")
        videos.append(v)

    has_audio = bool(audio_url and len(audio_url) > 10)
    audio_duration = duration  # fallback
    if has_audio:
        print("=== Descargando audio ===")
        r = requests.get(audio_url, timeout=120, stream=True)
        audio = f"{workdir}/audio.mp3"
        with open(audio, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print(f"Audio: {os.path.getsize(audio)} bytes")
        # Usar la duracion REAL del audio como duracion del video
        audio_duration = get_audio_duration(audio)
        duration = audio_duration

    seg = duration / 4
    print(f"Duracion total del video: {duration:.1f}s ({duration/60:.1f} min)")

    # Hacer loop de cada clip para que dure exactamente seg segundos (sin congelarse)
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

    # Subtitulos: distribuir los 4 textos en cuartos de la duracion real
    print("=== Renderizando con subtitulos y audio completo ===")
    t = [0, seg, seg*2, seg*3]
    e = [seg, seg*2, seg*3, duration]
    txts = [texto1, texto2, texto3, texto4]
    # Generar subtítulos como archivo SRT para soporte de múltiples líneas
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
    cmd += ['-vf', vf, '-c:v', 'libx264', '-preset', 'fast', '-crf', '20']
    if has_audio:
        # NO poner -t, dejar que el audio defina la duracion
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-map', '0:v', '-map', '1:a', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg render error: {result.stderr[-400:]}")

    final_size = os.path.getsize(f"{workdir}/final.mp4")
    print(f"=== Video final: {final_size} bytes ===")

    print("=== Enviando a Telegram ===")
    with open(f"{workdir}/final.mp4", 'rb') as f:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendVideo",
            data={"chat_id": chat_id, "caption": "🎬 Video listo! ¿Lo aprobamos para publicar?"},
            files={"video": ("video.mp4", f, "video/mp4")},
            timeout=300
        )
    result_tg = r.json()
    print(f"Telegram ok: {result_tg.get('ok')}")
    if not result_tg.get('ok'):
        raise Exception(f"Telegram error: {result_tg}")

    requests.post(callback_url, json={'status': 'done', 'video_url': 'sent_via_telegram'}, timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    tb = traceback.format_exc()
    print(f"ERROR:\n{tb}")
    requests.post(callback_url, json={'video_url': '', 'status': 'error', 'message': str(e)}, timeout=15)
    sys.exit(1)
