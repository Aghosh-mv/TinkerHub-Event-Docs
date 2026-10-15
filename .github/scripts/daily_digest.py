#!/usr/bin/env python3
"""Daily TinkerHub event digest - runs via GitHub Actions"""
import os, json, subprocess, base64, re, time
from datetime import datetime

# 1. Check tinkerhub.org/events for today's event
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
        
        # Wait for stream to finish
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
        
        # Digest: Gemini 2.5-flash only (proven working; avoids OpenRouter bracket bug)
        USE_GEMINI = os.getenv("USE_GEMINI_VIDEO", "true").lower() == "true"
        
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
            print("Gemini disabled; skipping digest")
            output_md = ""
        
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
