import json, os, subprocess, requests, tempfile, sys, traceback

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']
bot_token = os.environ['BOT_TOKEN']
chat_id = os.environ['CHAT_ID']

bgs = [payload['bg1'], payload['bg2'], payload['bg3'], payload['bg4']]
audio_url = payload.get('audioUrl', '')
texto1 = payload['texto1']
texto2 = payload['texto2']
texto3 = payload['texto3']
texto4 = payload['texto4']
duration = float(payload.get('duration', 60))
seg = duration / 4

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

def trim_resize(inp, out, dur):
    r = subprocess.run([
        'ffmpeg', '-y', '-i', inp, '-t', str(dur),
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', out
    ], capture_output=True, text=True)
    if r.returncode != 0:
        raise Exception(f"FFmpeg trim error: {r.stderr[-200:]}")

def esc(t):
    return t.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")

try:
    print("=== Descargando videos ===")
    videos = []
    for i, url in enumerate(bgs):
        v = download(url, f"{workdir}/v{i+1}.mp4")
        videos.append(v)

    has_audio = bool(audio_url and len(audio_url) > 10)
    if has_audio:
        print("=== Descargando audio ===")
        r = requests.get(audio_url, timeout=120, stream=True)
        audio = f"{workdir}/audio.mp3"
        with open(audio, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print(f"Audio: {os.path.getsize(audio)} bytes")

    print("=== Recortando clips ===")
    for i, v in enumerate(videos):
        trim_resize(v, f"{workdir}/c{i+1}.mp4", seg)

    print("=== Concatenando ===")
    with open(f"{workdir}/list.txt", 'w') as f:
        for i in range(1, 5):
            f.write(f"file '{workdir}/c{i}.mp4'\n")
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', f"{workdir}/list.txt", '-c', 'copy', f"{workdir}/base.mp4"
    ], check=True, capture_output=True)

    print("=== Renderizando con subtitulos ===")
    t = [1, seg+1, seg*2+1, seg*3+1]
    e = [seg-1, seg*2-1, seg*3-1, duration-1]
    txts = [texto1, texto2, texto3, texto4]
    vf_parts = ["colorchannelmixer=rr=0.4:gg=0.4:bb=0.4"]
    for i in range(4):
        vf_parts.append(
            f"drawtext=text='{esc(txts[i])}':fontsize=56:fontcolor=white"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
            f":enable='between(t,{t[i]},{e[i]})'"
            f":box=1:boxcolor=black@0.3:boxborderw=10"
        )
    vf = ",".join(vf_parts)

    cmd = ['ffmpeg', '-y', '-i', f"{workdir}/base.mp4"]
    if has_audio:
        cmd += ['-i', audio]
    cmd += ['-vf', vf, '-t', str(duration), '-c:v', 'libx264', '-preset', 'fast', '-crf', '20']
    if has_audio:
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg render error: {result.stderr[-400:]}")

    final_size = os.path.getsize(f"{workdir}/final.mp4")
    print(f"=== Video final: {final_size} bytes ===")

    # Enviar directo a Telegram (soporta hasta 50MB)
    print("=== Enviando a Telegram ===")
    with open(f"{workdir}/final.mp4", 'rb') as f:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendVideo",
            data={"chat_id": chat_id, "caption": "🎬 Video listo! ¿Lo aprobamos para publicar?"},
            files={"video": ("video.mp4", f, "video/mp4")},
            timeout=300
        )
    print(f"Telegram status: {r.status_code}")
    result = r.json()
    print(f"Telegram ok: {result.get('ok')}")
    
    if not result.get('ok'):
        raise Exception(f"Telegram error: {result}")

    # Notificar a n8n que terminó
    requests.post(callback_url, json={'status': 'done', 'video_url': 'sent_via_telegram'}, timeout=30)
    print("=== COMPLETADO ===")

except Exception as e:
    tb = traceback.format_exc()
    print(f"ERROR:\n{tb}")
    requests.post(callback_url, json={'video_url': '', 'status': 'error', 'message': str(e)}, timeout=15)
    sys.exit(1)
