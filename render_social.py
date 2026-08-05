import os, sys, base64, tempfile, subprocess, requests, json

GOOGLE_TTS_KEY = "AIzaSyCJRk0BsasiDBMOLuLrmcHTaaK9PU0mcsE"

def main():
    video_urls_b64 = os.environ["VIDEO_URLS_B64"]
    tts_text  = os.environ["TTS_TEXT"]
    titulo    = os.environ["TITULO"]
    webhook   = os.environ["WEBHOOK_URL"]
    gh_token  = os.environ["GH_TOKEN"]
    repo      = os.environ["GITHUB_REPOSITORY"]
    run_id    = os.environ["GITHUB_RUN_ID"]

    video_urls_raw = base64.b64decode(video_urls_b64).decode("utf-8")
    print(f"DEBUG video_urls_raw: {video_urls_raw!r}")
    video_urls = json.loads(video_urls_raw)
    if isinstance(video_urls, str):
        video_urls = [video_urls]
    video_urls = [u for u in video_urls if u][:4]
    if not video_urls:
        raise ValueError(f"No se recibieron video_urls validas. raw={video_urls_raw!r}")

    with tempfile.TemporaryDirectory() as tmp:
        # 1. Generar audio con Google TTS
        tts_resp = requests.post(
            f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}",
            json={
                "input": {"text": tts_text},
                "voice": {"languageCode": "es-ES", "name": "es-ES-Wavenet-B", "ssmlGender": "MALE"},
                "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.05, "pitch": -1}
            },
            timeout=30
        )
        tts_resp.raise_for_status()
        apath = f"{tmp}/audio.mp3"
        open(apath, "wb").write(base64.b64decode(tts_resp.json()["audioContent"]))

        # 2. Duración del audio
        dur = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", apath
        ]).decode().strip())

        n = len(video_urls)
        seg_dur = dur / n

        # 3. Descargar y recortar/loopear cada clip a su segmento
        segment_paths = []
        for i, url in enumerate(video_urls):
            raw_path = f"{tmp}/clip{i}.mp4"
            open(raw_path, "wb").write(requests.get(url, timeout=60).content)
            seg_path = f"{tmp}/seg{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", raw_path,
                "-t", f"{seg_dur:.2f}",
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-an", seg_path
            ], check=True)
            segment_paths.append(seg_path)

        # 4. Concatenar segmentos (mismo codec/params -> concat demuxer con copy)
        concat_list = f"{tmp}/list.txt"
        with open(concat_list, "w") as f:
            for p in segment_paths:
                f.write(f"file '{p}'\n")
        concat_out = f"{tmp}/concat.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c", "copy", concat_out
        ], check=True)

        # 5. Combinar video concatenado con el audio
        out = f"{tmp}/final.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-i", concat_out, "-i", apath,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", out
        ], check=True)

        # 6. Subir a GitHub Releases
        tag = f"social-{run_id}"
        headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
        rel = requests.post(
            f"https://api.github.com/repos/{repo}/releases",
            headers=headers,
            json={"tag_name": tag, "name": tag, "draft": False, "prerelease": True}
        ).json()
        upload_url = rel["upload_url"].replace("{?name,label}", "")
        with open(out, "rb") as f:
            asset = requests.post(
                f"{upload_url}?name=social.mp4",
                headers={**headers, "Content-Type": "video/mp4"},
                data=f
            ).json()

        requests.post(webhook, json={
            "status": "ok",
            "video_url": asset["browser_download_url"],
            "titulo": titulo
        })

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"::error::RENDER FAILED: {e}")
        print(tb)
        try:
            requests.post(os.environ.get("WEBHOOK_URL", ""), json={
                "status": "error", "video_url": "", "message": f"{e}\n{tb}"[:1500]
            }, timeout=15)
        except Exception as e2:
            print(f"::error::ALSO FAILED TO NOTIFY WEBHOOK: {e2}")
        sys.exit(1)
