"""youtube_upload.py — YouTube Data API v3 se upload / thumbnail / playlist.

Guide (Section 2.1, 4.5) ke niyam:
  * Teen cheezein SIRF environment variables se aati hain:
    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (GitHub Secrets se env me).
    Code/file/chat me plaintext key kabhi nahi.
  * Har run me refresh token se naya access token banta hai.
  * OAuth app ko Google Cloud me PUBLISH karna zaroori hai — Testing mode me
    refresh token 7 din me mar jata hai.
  * Title <= 100 akshar, description <= 5000 akshar — upload se PEHLE gin lo,
    warna YouTube `invalidTitle` dekar fail karta hai.
  * Safalta ka daava run ke status par mat karo — upload ke baad video ko
    API/page se verify karo ki sahi title/playlist ke saath live hai.
"""

from __future__ import annotations

import os

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]
MAX_TITLE_LEN = 100
MAX_DESC_LEN = 5000


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} environment variable set nahi hai. "
            "Ise GitHub Secret me rakho aur workflow env se pass karo."
        )
    return value


def refresh_access_token() -> str:
    """Refresh token se naya access token banao."""
    import requests

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": _env("YT_CLIENT_ID"),
            "client_secret": _env("YT_CLIENT_SECRET"),
            "refresh_token": _env("YT_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if not resp.ok:
        # Error body me secret nahi hota — asal wajah (invalid_grant /
        # invalid_client) log me dikhao taaki debug ho sake.
        raise RuntimeError(
            f"Token refresh fail ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json()["access_token"]


def get_youtube_service():
    """Access token se YouTube Data API v3 service object banao."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(token=refresh_access_token(), scopes=SCOPES)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def check_lengths(title: str, description: str) -> None:
    """Title/description ki lambai upload se pehle jaancho."""
    if len(title) > MAX_TITLE_LEN:
        raise ValueError(
            f"Title {len(title)} akshar ka hai — {MAX_TITLE_LEN} se zyada par "
            "YouTube upload fail hoga (invalidTitle)."
        )
    if len(description) > MAX_DESC_LEN:
        raise ValueError(
            f"Description {len(description)} akshar ka hai — "
            f"{MAX_DESC_LEN} ki seema se zyada hai."
        )


def upload_video(
    video_file: str,
    title: str,
    description: str,
    privacy: str = "private",
    thumbnail_file: str | None = None,
    playlist_id: str | None = None,
    category_id: str = "27",  # 27 = Education
    tags: list[str] | None = None,
) -> str:
    """Video upload karo; thumbnail/playlist diya ho to woh bhi set karo.

    Return: naya video ID. Upload ke baad caller ko video verify karna chahiye.
    """
    check_lengths(title, description)
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError(f"Galat privacy value: {privacy!r}")

    from googleapiclient.http import MediaFileUpload

    youtube = get_youtube_service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": category_id,
            "tags": tags or [],
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_file, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _status, response = request.next_chunk()
    video_id = response["id"]

    if thumbnail_file:
        set_thumbnail(video_id, thumbnail_file, youtube=youtube)
    if playlist_id:
        try:
            add_to_playlist(video_id, playlist_id, youtube=youtube)
        except Exception as exc:
            # Playlist fail ho to bhi video upload safal mana jayega — warna
            # state update rukta aur agli run me wahi video duplicate banti.
            print(f"WARNING: video playlist me nahi juda ({playlist_id}): {exc}")
    return video_id


def set_thumbnail(video_id: str, thumbnail_file: str, youtube=None) -> None:
    """videos ke liye custom thumbnail set karo (Shorts ke liye bhi zaroori)."""
    from googleapiclient.http import MediaFileUpload

    yt = youtube or get_youtube_service()
    yt.thumbnails().set(
        videoId=video_id, media_body=MediaFileUpload(thumbnail_file)
    ).execute()


def add_to_playlist(video_id: str, playlist_id: str, youtube=None) -> None:
    """Video ko playlist me jodo."""
    yt = youtube or get_youtube_service()
    yt.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="YouTube par video upload karo")
    parser.add_argument("video_file")
    parser.add_argument("--title", required=True)
    parser.add_argument("--desc", default="")
    parser.add_argument(
        "--privacy", default="private", choices=["private", "unlisted", "public"]
    )
    parser.add_argument("--thumb", default=None)
    parser.add_argument("--playlist", default=None)
    args = parser.parse_args()

    vid = upload_video(
        args.video_file,
        args.title,
        args.desc,
        privacy=args.privacy,
        thumbnail_file=args.thumb,
        playlist_id=args.playlist,
    )
    print(f"Uploaded: {video_url(vid)} (ID: {vid})")
    print("Note: safalta ki pushti video page/API se khud verify karo.")
