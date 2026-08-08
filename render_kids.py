import json, os, subprocess, requests, tempfile, sys, traceback, time, re

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']
bot_token = os.environ.get('BOT_TOKEN', '')
chat_id = os.environ.get('CHAT_ID', '8946671215') or '8946671215'
github_token = os.environ.get('GH_TOKEN', '')
release_id = os.environ.get('RELEASE_ID', '367237403')
repo = os.environ.get('REPO', 'frankywithjesus-rgb/marco-video-renderer')

scenes = payload['scenes']  # [{image, text}, ...] en orden
audio_url = payload.get('audioUrl', '')
titulo = payload.get('titulo', 'Escuela Sabática Kids')

workdir = tempfile.mkdtemp()
FALLBACK = "https://images.pexels.com/photos/1367192/pexels-photo-1367192.jpeg"

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

def download(url, path):
    try:
        r = requests.get(url, timeout=120, stream=True)
        if r.status_code == 200:
            with open(path, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            size = os.path.getsize(path)
            if size > 5000 and is_valid_image(path):
                print(f"OK {path}: {size} bytes")
                return path
    except Exception as e:
        print(f"  Intento fallido: {e}")
    print(f"Usando fallback para {path}")
    r = requests.get(FALLBACK, timeout=120, stream=True)
    with open(path, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return path

def get_audio_duration(path):
    r = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', path], capture_output=True, text=True)
    data = json.loads(r.stdout)
    dur = float(data['format']['duration'])
    print(f"Duracion del audio: {dur:.1f}s")
    return dur

def image_to_kenburns(inp, out, dur, zoom_in=True):
    # Resolucion de trabajo reducida (720p) y fps mas bajo: zoompan es MUY pesado
    # por-frame en CPU, y a 1080p/30fps con escenas largas (~20-25s) se pasaba
    # del limite de 25 min del job. 1280x720@20fps recorta el trabajo total a
    # menos de la mitad manteniendo buena calidad percibida en YouTube/redes.
    fps = 20
    frames = max(1, int(dur * fps))
    zexpr = "zoom+0.0012" if zoom_in else "if(lte(zoom,1.0),1.25,zoom-0.0012)"
    vf = (
        "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        f"zoompan=z='{zexpr}':d={frames}:s=1280x720:fps={fps},"
        "format=yuv420p"
    )
    print(f"  Ken Burns: {dur:.1f}s, {frames} frames @ {fps}fps, zoom_in={zoom_in}", flush=True)
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
        images.append(download(sc['image'], f"{workdir}/s{i+1}.jpg"))

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

    print("=== Animando escenas con efecto Ken Burns ===")
    for i, (img, dur) in enumerate(zip(images, durations)):
        image_to_kenburns(img, f"{workdir}/c{i+1}.mp4", dur, zoom_in=(i % 2 == 0))

    print("=== Concatenando ===")
    with open(f"{workdir}/list.txt", 'w') as f:
        for i in range(1, len(scenes) + 1):
            f.write(f"file '{workdir}/c{i}.mp4'\n")
    subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', f"{workdir}/list.txt", '-c', 'copy', f"{workdir}/base.mp4"], check=True, capture_output=True, timeout=120)
    print("  -> concatenado listo", flush=True)

    print("=== Generando subtitulos por escena ===")
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
        cursor = seg_end

    with open(srt_path, 'w', encoding='utf-8') as srt:
        for idx, start, end, s in entries:
            srt.write(f"{idx}\n{format_time(start)} --> {format_time(end)}\n{s}\n\n")

    vf = (
        f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=26,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
        "Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=40'"
    )

    cmd = ['ffmpeg', '-y', '-i', f"{workdir}/base.mp4"]
    if has_audio:
        cmd += ['-i', audio]
    cmd += ['-vf', vf, '-c:v', 'libx264', '-preset', 'fast', '-crf', '23']
    if has_audio:
        cmd += ['-c:a', 'aac', '-b:a', '128k', '-map', '0:v', '-map', '1:a', '-shortest']
    cmd.append(f"{workdir}/final.mp4")
    print("  Renderizando video final con subtitulos...", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise Exception("FFmpeg render final colgado mas de 300s")
    if result.returncode != 0:
        raise Exception(f"FFmpeg render error: {result.stderr[-400:]}")
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
