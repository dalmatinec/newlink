from emoji_utils import extract_html_text

MEDIA_GETTERS = {
    "photo": lambda m: m.photo[-1].file_id if m.photo else None,
    "video": lambda m: m.video.file_id if m.video else None,
    "animation": lambda m: m.animation.file_id if m.animation else None,
    "document": lambda m: m.document.file_id if m.document else None,
    "voice": lambda m: m.voice.file_id if m.voice else None,
    "video_note": lambda m: m.video_note.file_id if m.video_note else None,
}


def extract_content(message) -> dict:
    """Единый формат {"type", "text", "file_id"} из любого входящего сообщения —
    используется для приветствия, рекламы и рассылки."""
    text = extract_html_text(message)
    for mtype, getter in MEDIA_GETTERS.items():
        file_id = getter(message)
        if file_id:
            return {"type": mtype, "text": text, "file_id": file_id}
    return {"type": "none", "text": text, "file_id": None}
