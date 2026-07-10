import json, os, requests, tempfile, subprocess, sys

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']

bg1 = payload['bg1']
workdir = tempfile.mkdtemp()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.pexels.com/",
    "Accept": "*/*"
}

print(f"Intentando descargar: {bg1[:60]}")
r = requests.get(bg1, timeout=60, stream=True, headers=HEADERS)
print(f"Status: {r.status_code}")
print(f"Content-Type: {r.headers.get('content-type','?')}")
print(f"Content-Length: {r.headers.get('content-length','?')}")

path = f"{workdir}/v1.mp4"
size = 0
with open(path, 'wb') as f:
    for chunk in r.iter_content(8192):
        size += len(chunk)
        f.write(chunk)

print(f"Bytes descargados: {size}")

if size < 1000:
    content = open(path, 'rb').read()
    print(f"Contenido: {content[:200]}")
    sys.exit(1)

print("Video descargado OK, probando FFmpeg...")
result = subprocess.run(
    ['ffmpeg', '-y', '-i', path, '-t', '5',
     '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
     '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30', '-an',
     f"{workdir}/out.mp4"],
    capture_output=True, text=True
)
print(f"FFmpeg returncode: {result.returncode}")
if result.returncode != 0:
    print(f"STDERR: {result.stderr[-400:]}")
else:
    size2 = os.path.getsize(f"{workdir}/out.mp4")
    print(f"Output: {size2} bytes - OK!")
    requests.post(callback_url, json={"video_url": "TEST_OK", "status": "done"}, timeout=10)
