"""Group instruments by operator task while retaining their queries and identities."""

import copy
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

NAMES = {
    "overview": ("findme-photo-deployment-overview", "FindMe Photo — обзор"),
    "upload": ("findme-photo-upload", "FindMe Photo — загрузка и распознавание"),
    "search": ("findme-photo-search", "FindMe Photo — поиск и выдача"),
    "commerce": ("findme-photo-commerce", "FindMe Photo — коммерция"),
    "web": ("findme-photo-web", "FindMe Photo — Web"),
    "infrastructure": ("findme-photo-infrastructure", "FindMe Photo — инфраструктура"),
}
QUEUE_GROUPS = {
    "upload": (
        "disk_import",
        "photo_capture_metadata",
        "photo_generate_preview",
        "photo_generate_watermarked_preview",
        "photo_face_embedding",
        "photo_person_appearance",
        "photo_bib_recognition",
    ),
    "search": ("selfie_recognition", "selfie_matching", "gallery_matching", "photo_archive"),
    "commerce": ("commerce_email", "commerce_payment"),
}
SECTION_GROUPS = {
    "Загрузка и обработка фотографий": "upload",
    "Пользовательский путь — поиск по фотографии галереи": "search",
    "Пользовательский путь — поиск по селфи": "search",
    "Django / HTTP": "web",
    "Web Vitals": "web",
    "VM": "infrastructure",
    "PostgreSQL": "infrastructure",
    "CDN и Object Storage": "infrastructure",
}


def render(config, queues, panels):
    original = json.loads(
        (Path(__file__).parent / "single-vm-dashboard.json")
        .read_text()
        .replace("{{workspace_id}}", config["workspace_id"])
    )
    result = {
        key: {
            **copy.deepcopy(original),
            "name": name,
            "title": title,
            "widgets": [],
            "links": [
                {
                    "link": {
                        "title": other_title,
                        "url": (
                            "https://monium.yandex.cloud/projects/"
                            f"folder__{config['folder_id']}/dashboards/{other_name}"
                        ),
                    }
                }
                for other_key, (other_name, other_title) in NAMES.items()
                if other_key != key
            ],
        }
        for key, (name, title) in NAMES.items()
    }
    by_key = {queue.key: queue for queue in queues}
    assigned = [key for keys in QUEUE_GROUPS.values() for key in keys]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(by_key):
        raise ValueError("Every queue must belong to exactly one dashboard")
    for key, keys in QUEUE_GROUPS.items():
        result[key]["widgets"] = panels(
            tuple(by_key[name] for name in keys), config["workspace_id"]
        )
    for widget in original["widgets"]:
        result[SECTION_GROUPS[widget["group"]["title"]]]["widgets"].append(copy.deepcopy(widget))
    # A small landing page; detailed charts remain in their thematic dashboards.
    selected = []
    for widget in original["widgets"]:
        title = widget["group"]["title"]
        indexes = {"Django / HTTP": (0, 1), "PostgreSQL": (0,), "VM": (0, 2, 3)}.get(title, ())
        selected.extend(copy.deepcopy(widget["group"]["widgets"][i]) for i in indexes)
    for index, widget in enumerate(selected):
        identity = str(uuid5(NAMESPACE_URL, "findme-photo/overview/" + widget["id"]))
        widget["id"] = widget["multiSourceChart"]["id"] = identity
        widget["position"] = {
            k: v
            for k, v in {
                "x": str((index % 2) * 12),
                "y": str((index // 2) * 8),
                "w": "12",
                "h": "8",
            }.items()
            if v != "0"
        }
    result["overview"]["widgets"] = [
        {
            "position": {"w": "24", "h": "24"},
            "group": {
                "id": str(uuid5(NAMESPACE_URL, "findme-photo/overview")),
                "title": "Состояние сервиса",
                "widgets": selected,
            },
        }
    ]
    for dashboard in result.values():
        y = 0
        for widget in dashboard["widgets"]:
            widget["position"].pop("y", None)
            if y:
                widget["position"]["y"] = str(y)
            y += int(widget["position"]["h"])
    return result
