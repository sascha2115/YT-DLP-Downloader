# Analysis helper: summarize a captured `yt-dlp -J` info JSON (Odysee probe).
# Run from the repo root:  python3 temp/analyze_odysee.py /tmp/odysee.json

import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/odysee.json"
with open(path) as fh:
    d = json.load(fh)

print("extractor:", d.get("extractor"), "| id:", d.get("id"))
print("title:", (d.get("title") or "")[:70])
print("upload_date:", d.get("upload_date"), "| timestamp:", d.get("timestamp"))
print("duration_string:", d.get("duration_string"), "| duration:", d.get("duration"))
print("channel:", d.get("channel"), "| uploader_id:", d.get("uploader_id"))
print("height/width/fps:", d.get("height"), d.get("width"), d.get("fps"))
print("ext/vcodec/acodec (top):", d.get("ext"), d.get("vcodec"), d.get("acodec"))
print("thumbnail:", (d.get("thumbnail") or "")[:60])
print("language:", d.get("language"))
print("subtitles keys:", list((d.get("subtitles") or {}).keys()))
print("automatic_captions keys:", list((d.get("automatic_captions") or {}).keys()))
print("license:", d.get("license"), "| tags:", (d.get("tags") or [])[:3])
print("playlist fields:", d.get("_type"), d.get("playlist_count"), d.get("playlist"))
print(f"\nformats ({len(d.get('formats') or [])}):")
for f in d.get("formats") or []:
    print(
        f"  {f.get('format_id'):>10} | ext:{f.get('ext'):<5} | {str(f.get('height')):>5}p"
        f" | vcodec:{str(f.get('vcodec'))[:12]:<12} | acodec:{str(f.get('acodec'))[:12]:<12}"
        f" | tbr:{f.get('tbr')} | proto:{f.get('protocol')} | size:{f.get('filesize')}"
    )
