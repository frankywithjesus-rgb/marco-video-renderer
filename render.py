import json, os, subprocess, requests, tempfile, sys, traceback

payload = json.loads(os.environ['PAYLOAD'])
callback_url = os.environ['CALLBACK_URL']

def notify(msg, error=False):
    try:
        requests.post(callback_url, json={'video_url': '', 'status': 'error' if error else 'info', 'message': msg}, timeout=15)
    except:
        pass

try:
    bg1 = payload['bg1']
    workdir = tempfile.mkdtemp()

    BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.pexels.com/",
        "Accept": "*/*"
    }

    notify("Iniciando descarga de bg1...")
    r = requests.get(bg1, timeout=120, stream=True, headers=BROWSER_HEADERS)
    notify(f"BG1 status: {r.status_code}, content-type: {r.headers.get('content-type','?')}")
    
    path = f"{workdir}/v1.mp4"
    size = 0
    with open(path, 'wb') as f:
        for chunk in r.iter_content(8192):
            size += len(chunk)
            f.write(chunk)
    notify(f"BG1 descargado: {size} bytes")

    if size < 1000:
        content = open(path, 'rb').read()
        notify(f"ERROR: Archivo muy pequeño. Contenido: {content[:200]}", error=True)
        sys.exit(1)

    notify("Probando FFmpeg...")
    result = subprocess.run(
        ['ffmpeg', '-y', '-i', path, '-t', '5', '-vf',
         'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920',
         '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30', '-an', f"{workdir}/out.mp4"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        notify(f"FFmpeg error: {result.stderr[-300:]}", error=True)
        sys.exit(1)
    
    out_size = os.path.getsize(f"{workdir}/out.mp4")
    notify(f"FFmpeg OK! Output: {out_size} bytes")
    notify("TEST EXITOSO - render completo pendiente", error=False)

except Exception as e:
    tb = traceback.format_exc()
    notify(f"EXCEPCION: {str(e)}\n{tb[-500:]}", error=True)
    sys.exit(1)
