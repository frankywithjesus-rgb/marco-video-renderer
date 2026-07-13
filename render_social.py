import os, sys, base64, tempfile, subprocess, requests

def main():
    video_url = os.environ["VIDEO_URL"]
    audio_b64 = os.environ["AUDIO_B64"]
    titulo    = os.environ["TITULO"]
    webhook   = os.environ["WEBHOOK_URL"]
    gh_token  = os.environ["GH_TOKEN"]
    repo      = os.environ["GITHUB_REPOSITORY"]
    run_id    = os.environ["GITHUB_RUN_ID"]

    with tempfile.TemporaryDirectory() as tmp:
        vpath = f"{tmp}/clip.mp4"
        open(vpath, "wb").write(requests.get(video_url, timeout=60).content)

        apath = f"{tmp}/audio.mp3"
        open(apath, "wb").write(base64.b64decode(audio_b64))

        dur = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", apath
        ]).decode().strip()

        out = f"{tmp}/final.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", vpath,
            "-i", apath,
            "-t", dur,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", out
        ], check=True)

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
        requests.post(os.environ.get("WEBHOOK_URL", ""), json={
            "status": "error", "video_url": "", "message": str(e)
        })
        sys.exit(1)
