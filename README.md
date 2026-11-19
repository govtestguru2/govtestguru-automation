# GovTestGuru — YouTube Channel Automation

Script JSON se lekar YouTube upload tak ka poora system:
**slides (PIL) → awaaz (Gemini Live API, Leda) → video (ffmpeg) → upload (YouTube Data API v3)**,
GitHub Actions par roz schedule se chalta hai.

## Banawat

| File | Kaam |
|---|---|
| `lesson_video.py` | Series engine — script padhta hai, slides/awaaz/video banata hai, upload karke state update karta hai |
| `hindi_pronunciation.py` | `prepare_tts_text()` — awaaz se pehle text saaf karta hai (saal shabdon me, Roman ank, context-aware BC, BCE/CE) |
| `config/pronunciation.json` | Ucharan dictionary (UGC, NET, JTET, NTA…) — nayi entry jodo to yahi ek file badlo |
| `live_tts.py` | Gemini Live API helper (Leda, hi-IN, PCM 24 kHz) + Edge-TTS backup stub |
| `youtube_upload.py` | Upload + thumbnail + playlist (env se refresh token) |
| `scripts/test_01.json` | Pehla 2-scene private test script |
| `lesson_state.json` | `{"next": 1}` — agli video ka number |
| `lesson_next.txt` | Agli script ka pointer (abhi: `scripts/test_01.json`) |
| `lesson_latest.txt` | Aakhri bani video ka link (abhi khaali) |
| `.github/workflows/lesson.yml` | Roz 09:00 IST schedule + manual dispatch (privacy input) |

## Setup checklist

### 1. YouTube API keys (apni, sirf apni)
- [ ] Google Cloud Console me project banao, **YouTube Data API v3** enable karo
- [ ] OAuth consent screen banao aur app ko **Publish** karo
      ⚠️ Testing mode me refresh token **7 din me mar jata hai** — ye sabse aam galti hai
- [ ] Desktop-app OAuth client banao → Client ID + Client Secret
- [ ] Consent flow se **Refresh Token** nikalo (scope: `youtube.upload` + `youtube`)

### 2. Gemini API key
- [ ] aistudio.google.com se **free API key** banao (apne hi account se)

### 3. GitHub Secrets (Repo → Settings → Secrets and variables → Actions)
- [ ] `YT_CLIENT_ID`
- [ ] `YT_CLIENT_SECRET`
- [ ] `YT_REFRESH_TOKEN`
- [ ] `GEMINI_API_KEY`

### 4. Pehla test
- [ ] Local: `pip install -r requirements.txt` aur `python hindi_pronunciation.py`
      (pronunciation demo — generated narration text padh kar verify karo)
- [ ] Workflow ko **workflow_dispatch** se `private` privacy par chalao —
      `scripts/test_01.json` ka 2-scene test video banna chahiye
- [ ] Video ka link kholkar/API se **verify** karo: sahi title, sahi privacy

## Suraksha niyam (pakke)

1. **Keys sirf apni, sirf Secrets/env me.** Code, file, chat me plaintext key **kabhi nahi**.
2. **Verify kiye bina "live hai" mat bolo** — run ka "Success" status proof nahi hai;
   video page/API se title, privacy aur playlist teeno check karo.
3. **State-loss trap:** workflow state file aakhir me commit karta hai. Run chalte samay
   repo me koi aur commit ho jaye to state push chupchaap kho sakta hai — run Success
   dikhayega, video bhi banega, lekin agli baar wahi video dobara ban jayegi.
   Niyam: (1) run ke beech repo commit band, (2) har run ke baad state/latest raw se
   verify, (3) state kho jaye to asli video link se haath se sudharo.
4. **Title ≤ 100 akshar, description ≤ 5000** — engine upload se pehle gintā hai.
5. Har scene me **adhiktam 5 points**; slide poori ek saath dikhti hai, animation nahi.
6. Photo sirf **khud dekh kar verify** ki hui `img_file` se; thumbnail photo repeat nahi.
7. Awaaz ek hi raaste se (sab Live ya sab Edge backup) — aadha-aadha nahi.
   Gemini kaam ek-ek karke, faasla dekar — warna 429 limit.
8. Dispatch se pehle uploads list check karo — late aaya scheduled run wahi video
   bana raha ho to duplicate ban jayega.

## Local dry-run (bina kisi key ke)

```bash
python3 -m py_compile hindi_pronunciation.py live_tts.py youtube_upload.py lesson_video.py
python3 hindi_pronunciation.py   # saal/BC/Roman ank ke sample outputs
```

(ffmpeg zaroori hai video ke liye: `sudo apt-get install -y ffmpeg`)
