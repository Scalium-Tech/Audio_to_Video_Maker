"""
channel_router.py
=================
Route songs to a default channel and playlist based on deity keywords.
"""

from __future__ import annotations


CHANNEL_MAP = {
    "shiva": {
        "deity": "shiva",
        "channel_key": "shiva-bhakti",
        "channel_name": "Shiva Bhakti",
        "playlist_title": "Shiva Bhajans",
    },
    "krishna": {
        "deity": "krishna",
        "channel_key": "krishna-bhakti",
        "channel_name": "Krishna Bhakti",
        "playlist_title": "Krishna Bhajans",
    },
    "ram": {
        "deity": "ram",
        "channel_key": "ram-bhakti",
        "channel_name": "Ram Bhakti",
        "playlist_title": "Ram Bhajans",
    },
    "ganesh": {
        "deity": "ganesh",
        "channel_key": "ganesh-bhakti",
        "channel_name": "Ganesh Bhakti",
        "playlist_title": "Ganesh Bhajans",
    },
    "durga": {
        "deity": "durga",
        "channel_key": "durga-bhakti",
        "channel_name": "Durga Bhakti",
        "playlist_title": "Durga Bhajans",
    },
    "default": {
        "deity": "general",
        "channel_key": "bhakti-sangeet",
        "channel_name": "Bhakti Sangeet",
        "playlist_title": "Devotional Bhajans",
    },
}

CHANNEL_ALIASES = {
    "shiva": ["shiva", "shiv", "mahadev", "mahakal", "bholenath", "shankar"],
    "krishna": ["krishna", "kanha", "kanhaiya", "govind", "gopal", "murlidhar"],
    "ram": ["ram", "siyaram", "raghav", "raghuveer"],
    "ganesh": ["ganesh", "ganpati", "gajanan", "vinayak"],
    "durga": ["durga", "ambe", "jagdambe", "bhavani", "sherawali"],
}


def route_channel(song_name: str) -> dict:
    name_lower = song_name.lower()
    for deity, aliases in CHANNEL_ALIASES.items():
        if any(alias in name_lower for alias in aliases):
            config = CHANNEL_MAP[deity]
            return dict(config)
    return dict(CHANNEL_MAP["default"])
