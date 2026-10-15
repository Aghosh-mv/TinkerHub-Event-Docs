#!/usr/bin/env python3
"""Daily TinkerHub event digest - runs via GitHub Actions"""
import os, json, subprocess, base64, re, time
from datetime import datetime

# 1. Check tinkerhub.org/events for today's event
import requests
r = requests.get("https://tinkerhub.org/events", timeout=15)
today_str = datetime.now().strftime("%b %d").lower()
event_links = re.findall(r'href="(/events/[^"]+)"', r.text)
has_event = False
event_path = None
for link in event_links:
    if today_str in r.text.lower():
        has_event = True
        event_path = link
        break

if has_event and event_path:
    # Find YouTube stream URL
    r2 = requests.get("https://tinkerhub.org" + event_path, timeout=10)
    stream_url = re.search(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)[^"\s]+)', r2.text)
    if stream_url:
        stream = stream_url.group(1)
        print(f"Event found: {stream}")
        
        # Probe if live
        try:
            j = json.loads(subprocess.check_output(["yt-dlp", "--dump-json", stream], timeout=20))
            live = j.get("is_live", False)
        except: live = False
        
        # Wait for stream to finish (if live)
        if live:
            for i in range(24):
                try:
                    j2 = json.loads(subprocess.check_output(["yt-dlp", "--dump-json", stream], timeout=20))
                    if not j2.get("is_live", True): break
                except: pass
                time.sleep(300)
        
        # Download VOD
        out = f"tmp/stream_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        subprocess.run(["yt-dlp", "-o", out, stream], check=False)
        print(f"Downloaded {out}")
        
        # --- DIGEST: Choose Gemini OR Groq hybrid ---
        USE_GEMINI = os.getenv("USE_GEMINI_VIDEO", "false").lower() == "true"
        
        if USE_GEMINI:
            from google import genai
            client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
            f = client.files.upload(file=out)
            while f.state.name == "PROCESSING": time.sleep(10); f = client.files.get(name=f.name)
            if f.state.name == "ACTIVE":
                prompt = """You are documenting a TinkerHub event VERY DETAILED.
Extract HOST NAME from intro/slides/speech. Watch full 1.5hr video and output markdown:
# Title
Date, Duration, Host: <name>, Event type
## Detailed Summary (10-12 lines)
## Timeline with timestamps [mm:ss] - every major segment
## Demos/Projects shown in detail
## Tools / tech / code mentioned
## Host & speakers
## Audience Q&A
## Key takeaways & Next steps"""
                resp = client.models.generate_content(model="gemini-2.5-flash", contents=[f, prompt])
                output_md = resp.text
        else:
            from groq import Groq
            client_g = Groq(api_key=os.getenv("GROQ_API_KEY"))
            subprocess.run(["ffmpeg", "-y", "-i", out, "-vn", "tmp/audio.wav"], check=False)
            with open("tmp/audio.wav", "rb") as f:
                t = client_g.audio.transcriptions.create(model="whisper-large-v3", file=("audio.wav", f.read()))
            frames = []
            for ts in [300, 900, 1500, 2100, 2700, 3300, 3900, 4500, 5100]:
                outf = f"tmp/frame_{ts}.jpg"
                subprocess.run(["ffmpeg", "-y", "-ss", str(ts), "-i", out, "-frames:v", "1", outf], check=False)
                if os.path.exists(outf): frames.append(outf)
            descs = []
            for f_path in frames[:5]:
                with open(f_path, "rb") as img:
                    b64 = base64.b64encode(open(f_path, "rb").read()).decode()
                r = client_g.chat.completions.create(model="qwen/qwen3.6-27b",
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": "Describe this TinkerHub event frame"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]})
                )
                descs.append(r.choices[0].message.content)
            import requests as reqs
            prompt = f"Transcript:{t.text[:6000]}\nVision:{descs}\nCreate markdown with title, summary, timestamps, tools, next steps, host name."
            rr = reqs.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
                json={"model": "qwen/qwen-2.5-7b-instruct:free", "messages": [{"role": "user", "content": prompt}]}, timeout=60)
            output_md = rr.json()["choices"][0]["message"]["content"]
        
        # Write documentation
        out_date = datetime.now().strftime("%Y-%m-%d")
        final_md = f"# {out_date} TinkerHub Event Documentation\n\n"
        final_md += f"**Source:** {stream}\n\n"
        final_md += f"{output_md}\n\n"
        final_md += "*Auto-generated with host name + timestamps*\n"
        os.makedirs("docs", exist_ok=True)
        with open(f"docs/{out_date}-event.md", "w") as f:
            f.write(final_md)
        print(f"Wrote docs/{out_date}-event.md")
        
        # Push to repo
        subprocess.run(["git", "config", "user.name", "tinkerbot"], check=False)
        subprocess.run(["git", "config", "user.email", "bot@tinkerhub.auto"], check=False)
        subprocess.run(["git", "add", f"docs/{out_date}-event.md"], check=False)
        subprocess.run(["git", "commit", "-m", f"auto-doc {datetime.now().date()} digest+"], check=False)
        subprocess.run(["git", "push"], check=False)
        print("Pushed to repo")
        
else:
    print("No event today")
