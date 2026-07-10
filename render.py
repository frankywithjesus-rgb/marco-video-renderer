import json, os, subprocess, requests, tempfile, sys

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']

bg1 = payload['bg1']
bg2 = payload['bg2']
bg3 = payload['bg3']
bg4 = payload['bg4']
audio_url = payload.get('audioUrl', '')
texto1 = payload['texto1']
texto2 = payload['texto2']
texto3 = payload['texto3']
texto4 = payload['texto4']
duration = float(payload.get('duration', 60))
seg = duration / 4

workdir = tempfile.mkdtemp()
print(f"Workdir: {workdir}")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.pexels.com/",
    "Accept": "*/*"
}

def download(url, path, browser=False):
    h = BROWSER_HEADERS if browser else {}
    r = requests.get(url, timeout=120, stream=True, headers=h)
    print(f"  GET {url[:60]} -> {r.status_code}")
    r.raise_for_status()
    with open(path, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    size = os.path.getsize(path)
    print(f"  Descargado: {size} bytes")
    if size < 1000:
        raise Exception(f"Archivo demasiado pequeño: {size} bytes")
    return path

def trim_resize(inp, out, dur):
    r = subprocess.run([
        'ffmpeg', '-y', '-i', inp, '-t', str(dur),
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23', '-an', out
    ], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FFmpeg stderr: {r.stderr[-300:]}")
        raise Exception("FFmpeg trim fallo")

def esc(t):
    return t.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")

print("=== Descargando videos ===")
v1 = download(bg1, f"{workdir}/v1.mp4", browser=True)
v2 = download(bg2, f"{workdir}/v2.mp4", browser=True)
v3 = download(bg3, f"{workdir}/v3.mp4", browser=True)
v4 = download(bg4, f"{workdir}/v4.mp4", browser=True)

has_audio = bool(audio_url and len(audio_url) > 10)
if has_audio:
    print("=== Descargando audio ===")
    audio = download(audio_url, f"{workdir}/audio.mp3")

print("=== Recortando clips ===")
trim_resize(v1, f"{workdir}/c1.mp4", seg)
trim_resize(v2, f"{workdir}/c2.mp4", seg)
trim_resize(v3, f"{workdir}/c3.mp4", seg)
trim_resize(v4, f"{workdir}/c4.mp4", seg)

print("=== Concatenando ===")
with open(f"{workdir}/list.txt", 'w') as f:
    for i in range(1,5):
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
    print(f"FFmpeg render error:\n{result.stderr[-500:]}")
    raise Exception("FFmpeg render fallo")

final_size = os.path.getsize(f"{workdir}/final.mp4")
print(f"=== Video final: {final_size} bytes ===")

print("=== Subiendo a file.io ===")
with open(f"{workdir}/final.mp4", 'rb') as f:
    r = requests.post('https://file.io/?expires=1d', files={'file': f}, timeout=120)
data = r.json()
video_url = data.get('link', '')
print(f"URL: {video_url}")

if not video_url:
    raise Exception(f"Upload fallo: {data}")

print("=== Notificando a n8n ===")
resp = requests.post(callback_url, json={'video_url': video_url, 'status': 'done'}, timeout=30)
print(f"n8n response: {resp.status_code}")
print("=== COMPLETADO ===")
