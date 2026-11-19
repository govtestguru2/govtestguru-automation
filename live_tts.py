"""live_tts.py — Gemini Live API se Hindi awaaz (voice: Leda) banane wala helper.

Guide (Section 4.3) ke niyam:
  * Primary raasta Live API hai — model `gemini-3.1-flash-live-preview`,
    fallback `gemini-2.5-flash-native-audio-preview-12-2025`.
    (Aam generateContent TTS ka free quota bahut kam hai, us par mat bano.)
  * Config: response_modalities=["AUDIO"], voice_name="Leda",
    language_code="hi-IN". Awaaz raw PCM 24 kHz me aati hai.
  * Har scene ka ALAG session; scene ek-ek karke, beech me faasla (delay)
    dekar chalao — ek saath dabav par 429 aata hai.
  * Backup awaaz: Edge-TTS (Swara, rate +8%, pitch -8Hz) — stub neeche hai.
    Poora video EK hi awaaz-raaste se bane; aadha isse aadha usse nahi.
  * API key SIRF environment variable GEMINI_API_KEY se — code/file me kabhi nahi.
"""

from __future__ import annotations

import asyncio
import os
import wave

# 15 Nov 2026 fix: guide wale dono preview model Live API par 404 dene lage
# (unka shutdown 17 Nov 2026 ghoshit tha; replacement stable model
# `gemini-3.8-live` hai). Isiliye pehla model 3.8-live, uske baad purane
# preview models backup kram me.
MODELS = [
    "gemini-3.8-live",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-3.1-flash-live-preview",
]
# Live API ke do version — kuch models v1beta par, kuch sirf v1alpha par
# milte hain; dono try karte hain aur log me asal wajah chhapti hai.
API_VERSIONS = ["v1beta", "v1alpha"]
PRIMARY_MODEL = MODELS[0]
FALLBACK_MODEL = MODELS[1]
VOICE_NAME = "Leda"
LANGUAGE_CODE = "hi-IN"
SAMPLE_RATE = 24000  # Live API PCM output: 24 kHz, 16-bit, mono

# 15 Nov 2026 — ASLI FIX: bina is nirdesh ke Live model bheje gaye text ko
# shabd-shah padhta hi nahi tha; wo us par charcha karta tha (saar, tippani,
# prashn) — lesson_003 ke captions me script ke sirf ~28% shabd mile.
# Isiliye sakht system-instruction: sirf padho, kuch jodo-ghatao mat.
SYSTEM_INSTRUCTION = (
    "You are a professional text-to-speech narrator for Hindi educational "
    "lessons. The user will give you a passage of Hindi text. Read it aloud "
    "EXACTLY word for word, in the given order, from the first word to the "
    "last word. Do NOT summarize, shorten, rephrase, explain, comment, "
    "greet, introduce, conclude, or ask any question. Do NOT say anything "
    "other than the given text itself. Just read the passage completely."
)
# Lambe text par model saar bolne lagta hai — isiliye har scene ka text
# chhote tukdo (lagbhag 70 shabd) me baant kar padhwate hain.
CHUNK_MAX_WORDS = 70


def _get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable set nahi hai. "
            "Key ko GitHub Secret / env me rakho, code me kabhi mat likho."
        )
    return key


async def _synthesize_once(
    model: str, text: str, out_pcm_path: str, api_version: str = "v1beta"
) -> None:
    """Ek model se ek scene ka PCM audio banao (ek fresh Live session)."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=_get_api_key(),
        http_options=types.HttpOptions(api_version=api_version),
    )
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_INSTRUCTION)]
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=VOICE_NAME
                )
            ),
            language_code=LANGUAGE_CODE,
        ),
    )
    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]},
            turn_complete=True,
        )
        with open(out_pcm_path, "wb") as fh:
            async for response in session.receive():
                if response.data:
                    fh.write(response.data)


async def synthesize_scene(text: str, out_pcm_path: str) -> str:
    """Ek scene ki awaaz banao — har model x har API version try karo."""
    last_exc: Exception | None = None
    for model in MODELS:
        for version in API_VERSIONS:
            try:
                await _synthesize_once(model, text, out_pcm_path, version)
                print(
                    f"TTS: model={model} api={version} "
                    f"voice={VOICE_NAME} (Leda) se awaaz bani"
                )
                return out_pcm_path
            except Exception as exc:  # agla combination try karo
                print(
                    f"TTS: model={model} api={version} fail: "
                    f"{exc.__class__.__name__}: {str(exc)[:300]}"
                )
                last_exc = exc
    raise RuntimeError(f"Saare Live models fail ho gaye: {last_exc}")


def _chunk_text(text: str, max_words: int = CHUNK_MAX_WORDS) -> list[str]:
    """Text ko vaakya-seemaon par chhote tukdo me baanto (~max_words shabd).

    Live model lambe text ka saar bol deta hai; chhote tukde shabd-shah
    padhe jaate hain. Vaakya aadha kabhi nahi katta.
    """
    import re

    sentences = [s.strip() for s in re.split(r"(?<=[।?!])\s+", text) if s.strip()]
    if not sentences:
        return [text] if text.strip() else []
    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0
    for sent in sentences:
        w = len(sent.split())
        if cur and cur_words + w > max_words:
            chunks.append(" ".join(cur))
            cur, cur_words = [sent], w
        else:
            cur.append(sent)
            cur_words += w
    if cur:
        chunks.append(" ".join(cur))
    return chunks


async def synthesize_scenes_sequential(
    texts: list[str],
    out_dir: str,
    inter_scene_delay: float = 2.0,
) -> list[str]:
    """Saare scenes ek-ek karke (sequential), har scene ka alag session.

    Har scene ka text pehle chhote tukdo me bantta hai; har tukda apne
    session me shabd-shah padha jaata hai aur PCM jod kar scene_XX.pcm
    banti hai. Har tukde ki gati (shabd/min) log hoti hai — saar bolne
    par gati achaanak badh jaati hai, isliye WARNING dikhta hai.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths: list[str] = []
    try:
        for idx, text in enumerate(texts, start=1):
            out_path = os.path.join(out_dir, f"scene_{idx:02d}.pcm")
            chunks = _chunk_text(text)
            with open(out_path, "wb") as out_fh:
                for c_idx, chunk in enumerate(chunks, start=1):
                    part_path = os.path.join(
                        out_dir, f"scene_{idx:02d}_part{c_idx:02d}.pcm"
                    )
                    # 16 Nov 2026 — RETRY GUARD: kabhi-kabhi Live model ek
                    # tukde ka lagbhag khaali audio de deta hai (lesson_003
                    # scene 84: 66 shabdon par sirf 0.8s — prashn hi gayab
                    # ho gaya tha). Gati 300 shabd/min se zyada matlab
                    # audio bharosemand nahi; tukda dobara padhwao (3 try
                    # tak), sabse lamba audio rakho.
                    words = len(chunk.split())
                    best_data = b""
                    best_dur = -1.0
                    for attempt in range(1, 4):
                        await synthesize_scene(chunk, part_path)
                        with open(part_path, "rb") as part_fh:
                            data = part_fh.read()
                        dur = len(data) / (SAMPLE_RATE * 2)
                        if dur > best_dur:
                            best_data, best_dur = data, dur
                        wpm_try = words / (dur / 60) if dur > 0 else 9999
                        if wpm_try <= 300:
                            break
                        print(
                            f"TTS chunk RETRY: scene {idx} part {c_idx}/{len(chunks)} "
                            f"attempt {attempt}: {words} shabd par sirf {dur:.1f}s "
                            f"(~{wpm_try:.0f} shabd/min) — dobara padha raha hoon"
                        )
                        await asyncio.sleep(1.0)
                    out_fh.write(best_data)
                    dur = best_dur
                    wpm = words / (dur / 60) if dur > 0 else 0
                    flag = " WARNING: gati zyada — saar bola hoga?" if wpm > 300 else ""
                    print(
                        f"TTS chunk: scene {idx} part {c_idx}/{len(chunks)}: "
                        f"{words} shabd, {dur:.1f}s, ~{wpm:.0f} shabd/min{flag}"
                    )
                    os.remove(part_path)
                    if c_idx < len(chunks):
                        await asyncio.sleep(0.7)  # 429 se bachne ke liye faasla
            paths.append(out_path)
            if idx < len(texts) and inter_scene_delay > 0:
                await asyncio.sleep(inter_scene_delay)  # 429 se bachne ke liye faasla
        return paths
    except Exception:
        # Priya ka niyam (15 Nov 2026): video LEDA awaaz me hi bane.
        # Isiliye default me Edge-TTS (Swara) par chup-chaap girna BAND hai —
        # Live fail ho to run fail hoga aur wajah log me dikhegi.
        # Sirf TTS_ALLOW_EDGE=1 env ho tabhi backup awaaz lagegi (poora video
        # ek hi awaaz se, guide ka niyam).
        if os.environ.get("TTS_ALLOW_EDGE") == "1":
            print("TTS: Live fail — TTS_ALLOW_EDGE=1 ke kaaran Edge-TTS (Swara) backup lagega")
            return await _synthesize_scenes_edge(texts, out_dir)
        raise


async def _synthesize_scenes_edge(texts: list[str], out_dir: str) -> list[str]:
    """Backup raasta: saare scenes Edge-TTS se, phir PCM (24 kHz s16le) me
    badlo taaki engine ka aage ka flow (pcm_to_wav/duration) waise hi chale."""
    import subprocess

    paths: list[str] = []
    for idx, text in enumerate(texts, start=1):
        mp3_path = os.path.join(out_dir, f"scene_{idx:02d}.mp3")
        pcm_path = os.path.join(out_dir, f"scene_{idx:02d}.pcm")
        await synthesize_scene_edge_tts(text, mp3_path)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", mp3_path,
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", pcm_path,
            ],
            check=True,
            capture_output=True,
        )
        paths.append(pcm_path)
    return paths


def pcm_to_wav(pcm_path: str, wav_path: str, sample_rate: int = SAMPLE_RATE) -> str:
    """Raw PCM (24 kHz, 16-bit mono) ko WAV me badlo (ffmpeg bina bhi chalega)."""
    with open(pcm_path, "rb") as fh:
        raw = fh.read()
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return wav_path


def pcm_duration_seconds(pcm_path: str, sample_rate: int = SAMPLE_RATE) -> float:
    """PCM file ki lambaai (seconds) — slide ki duration tay karne ke liye."""
    size = os.path.getsize(pcm_path)
    return size / (sample_rate * 2)  # 16-bit = 2 bytes per sample


# ---------------------------------------------------------------------------
# Backup awaaz — Edge-TTS stub (Live API atke to hi use karo, poora video
# ek hi raaste se banao: ya sab Live, ya sab Edge)
# ---------------------------------------------------------------------------
async def synthesize_scene_edge_tts(text: str, out_mp3_path: str) -> str:
    """Edge-TTS backup: voice hi-IN-SwaraNeural, rate +8%, pitch -8Hz."""
    import edge_tts  # lazy import — backup raaste ke liye hi chahiye

    communicate = edge_tts.Communicate(
        text,
        voice="hi-IN-SwaraNeural",
        rate="+8%",
        pitch="-8Hz",
    )
    await communicate.save(out_mp3_path)
    return out_mp3_path
