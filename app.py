from __future__ import annotations
from graphviz import Digraph
import re

import base64
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from urllib import error, request
from uuid import uuid4

import streamlit as st


APP_DIR = Path(__file__).resolve().parent
KEYS_PATH = APP_DIR / "keys.json"
OBSERVATION_DIR = APP_DIR / "outputs" / "observations"
IMAGE_DIR = APP_DIR / "static" / "morphology_images"
DEFAULT_KEY = "Key_to_Kingdoms_of_Life"
IMAGE_TYPES = ["png", "jpg", "jpeg", "webp"]
NEXT_TIER_MAP = {
    "Kingdom": "Phyla",
    "Phylum": "Classes",
    "Class": "Subclasses",
    "Subclass": "Superorders",
    "Superorder": "Orders",
    "Order": "Suborders",
    "Suborder": "Families",
    "Cohort": "Families",
    "Superfamily": "Families",
    "Family": "Subfamilies",
    "Subfamily": "Tribes",
    "Tribe": "Genera",
    "Genus": "Species",
}
TIER_FALLBACKS = {
    "Kingdom": ["Phyla"],
    "Phylum": ["Classes"],
    "Class": ["Subclasses", "Orders"],
    "Subclass": ["Superorders", "Orders"],
    "Superorder": ["Orders"],
    "Order": ["Suborders", "Families"],
    "Suborder": ["Families"],
    "Cohort": ["Families"],
    "Superfamily": ["Families"],
    "Family": ["Subfamilies", "Genera"],
    "Subfamily": ["Tribes", "Genera"],
    "Tribe": ["Genera"],
    "Genus": ["Species"],
}
NEXT_KEY_ALIASES = {
    "Class: Arachnida": ["Key_to_Subclasses_of_Arachnida", "Key_to_Orders_of_Arachnida"],
    "Subclass: Acari": ["Key_to_Superorders_of_Acari", "Key_to_Orders_of_Subclass_Acari"],
    "Suborder: Oribatida (Cohort Astigmatina)": ["Key_to_Families_of_Astigmatina"],
    "Suborder: Oribatida (excluding Astigmatina)": [
        "Key_to_Families_of_Oribatida_excluding_Astigmatina"
    ],
    "Suborder: Prostigmata": ["Key_to_Families_of_Prostigmata_excluding_Parasitengonina"],
    "Cohort: Parasitengonina": [
        "Key_to_Families_of_Parasitengonina_Adults",
        "Key_to_Families_of_Parasitengonina_Larvae",
    ],
}


@st.cache_data
def load_keys() -> dict:
    if not KEYS_PATH.exists():
        st.error("keys.json was not found. Keep it in the same folder as app.py.")
        return {}

    try:
        with KEYS_PATH.open("r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        st.error(f"keys.json has invalid JSON near line {error.lineno}: {error.msg}")
        return {}


def save_keys(keys_db: dict) -> None:
    KEYS_PATH.write_text(json.dumps(keys_db, indent=2), encoding="utf-8")
    load_keys.clear()


def format_key_name(key_name: str) -> str:
    return key_name.replace("Key_to_", "").replace("_", " ")


def parse_result(result: str) -> tuple[str | None, str | None]:
    if ": " not in result:
        return None, None
    rank, name = result.split(": ", 1)
    return rank.strip(), name.strip()


def key_fragment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in value)
    return "_".join(part for part in cleaned.split("_") if part)


def append_candidate(candidates: list[str], key_name: str, keys_db: dict) -> None:
    if key_name in keys_db and key_name not in candidates:
        candidates.append(key_name)


def get_next_key_candidates(result: str, keys_db: dict) -> list[str]:
    rank, name = parse_result(result)
    if not rank or not name:
        return []

    candidates = []
    for key_name in NEXT_KEY_ALIASES.get(result, []):
        append_candidate(candidates, key_name, keys_db)

    taxon = key_fragment(name)
    for tier in TIER_FALLBACKS.get(rank, []):
        append_candidate(candidates, f"Key_to_{tier}_of_{taxon}", keys_db)

    suffix = f"_of_{taxon}"
    for key_name in keys_db:
        if key_name.endswith(suffix):
            append_candidate(candidates, key_name, keys_db)

    return candidates


def secret_value(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except Exception:
        return default
    return str(value) if value is not None else default


def github_config() -> dict:
    return {
        "token": secret_value("github_token"),
        "repo": secret_value("github_repo"),
        "branch": secret_value("github_branch", "main"),
    }


def github_enabled() -> bool:
    config = github_config()
    return bool(config["token"] and config["repo"])


def github_api(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    config = github_config()
    url = f"https://api.github.com/repos/{config['repo']}/contents/{path}"
    if method == "GET":
        url = f"{url}?ref={config['branch']}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    api_request = request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {config['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with request.urlopen(api_request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def github_file_sha(repo_path: str) -> str | None:
    try:
        return github_api(repo_path).get("sha")
    except error.HTTPError as api_error:
        if api_error.code == 404:
            return None
        raise


def commit_file_to_github(repo_path: str, content: bytes, message: str) -> None:
    config = github_config()
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": config["branch"],
    }
    sha = github_file_sha(repo_path)
    if sha:
        payload["sha"] = sha
    github_api(repo_path, method="PUT", payload=payload)


def persist_keys_to_github(message: str) -> None:
    commit_file_to_github(KEYS_PATH.as_posix(), KEYS_PATH.read_bytes(), message)


def slugify(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in slug.split("-") if part) or "item"


def safe_uploaded_filename(uploaded_file, key_name: str, node_id: str, option_name: str) -> str:
    extension = Path(uploaded_file.name).suffix.lower()
    if extension.lstrip(".") not in IMAGE_TYPES:
        extension = ".png"
    return (
        f"{slugify(key_name)}-node-{slugify(node_id)}-"
        f"{option_name}-{uuid4().hex[:8]}{extension}"
    )


def save_uploaded_image(uploaded_file, key_name: str, node_id: str, option_name: str, caption: str) -> dict:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = safe_uploaded_filename(uploaded_file, key_name, node_id, option_name)
    path = IMAGE_DIR / filename
    content = uploaded_file.getvalue()
    path.write_bytes(content)

    image_record = {
        "path": path.as_posix(),
        "caption": caption.strip(),
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "original_name": uploaded_file.name,
        "mime_type": uploaded_file.type or mimetypes.guess_type(filename)[0] or "image/png",
    }

    if github_enabled():
        commit_file_to_github(
            path.as_posix(),
            content,
            f"Add morphology image for {format_key_name(key_name)} node {node_id} {option_name}",
        )
        image_record["persisted_to_github"] = True

    return image_record


def option_images(option: dict) -> list[dict]:
    images = option.get("images", [])
    return images if isinstance(images, list) else []


def render_option_images(option: dict) -> None:
    images = option_images(option)
    if not images:
        st.caption("No morphology reference photo is attached yet.")
        return

    for image in images:
        caption = image.get("caption") or None
        url = image.get("url", "").strip()
        path = image.get("path", "").strip()
        if url:
            st.image(url, caption=caption, use_column_width=True)
        elif path and Path(path).exists():
            st.image(path, caption=caption, use_column_width=True)
        elif path:
            st.warning(f"Image is listed but missing from app files: {path}")


def validate_keys(keys_db: dict) -> list[str]:
    warnings = []
    for key_name, couplets in keys_db.items():
        if not isinstance(couplets, dict):
            warnings.append(f"{key_name} must contain couplet nodes.")
            continue

        for node_id, couplet in couplets.items():
            for option_name in ("option_a", "option_b"):
                option = couplet.get(option_name)
                if not option:
                    warnings.append(f"{key_name} node {node_id} is missing {option_name}.")
                    continue

                target = option.get("advances_to", "")
                if isinstance(target, str) and target.startswith("Node "):
                    next_node = target.replace("Node ", "").strip()
                    if next_node not in couplets:
                        warnings.append(
                            f"{key_name} node {node_id} points to missing node {next_node}."
                        )
    return warnings


def initialize_state(keys_db: dict) -> None:
    default_key = DEFAULT_KEY if DEFAULT_KEY in keys_db else next(iter(keys_db), "")
    st.session_state.setdefault("current_key", default_key)
    st.session_state.setdefault("current_node", "1")
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("diagnosis_complete", False)
    st.session_state.setdefault("final_result", "")
    st.session_state.setdefault("specimen_code", "")
    st.session_state.setdefault("observer_notes", "")
    st.session_state.setdefault("app_mode", "Identify")

    if st.session_state.current_key not in keys_db:
        restart(default_key)


def restart(key_name: str, clear_notes: bool = False) -> None:
    st.session_state.current_key = key_name
    st.session_state.current_node = "1"
    st.session_state.history = []
    st.session_state.diagnosis_complete = False
    st.session_state.final_result = ""
    if clear_notes:
        st.session_state.specimen_code = ""
        st.session_state.observer_notes = ""


def advance(option_label: str, morphology: str, target: str, images: list[dict] | None = None) -> None:
    st.session_state.history.append(
        {
            "key": st.session_state.current_key,
            "node": st.session_state.current_node,
            "option": option_label,
            "morphology": morphology,
            "advanced_to": target,
            "images": images or [],
        }
    )

    if isinstance(target, str) and target.startswith("Node "):
        st.session_state.current_node = target.replace("Node ", "").strip()
    else:
        st.session_state.diagnosis_complete = True
        st.session_state.final_result = target


def undo() -> None:
    if not st.session_state.history:
        return

    previous = st.session_state.history.pop()
    st.session_state.current_key = previous["key"]
    st.session_state.current_node = previous["node"]
    st.session_state.diagnosis_complete = False
    st.session_state.final_result = ""


def observation_record() -> dict:
    return {
        "specimen_code": st.session_state.specimen_code,
        "observer_notes": st.session_state.observer_notes,
        "current_key": st.session_state.current_key,
        "final_result": st.session_state.final_result,
        "path": st.session_state.history,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }


def save_record(record: dict) -> Path:
    OBSERVATION_DIR.mkdir(parents=True, exist_ok=True)
    safe_code = "".join(
        char for char in record["specimen_code"] if char.isalnum() or char in ("-", "_")
    ).strip()
    filename_code = safe_code or "specimen"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OBSERVATION_DIR / f"{filename_code}-{timestamp}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def render_path() -> None:
    st.subheader("Diagnostic Path")
    if not st.session_state.history:
        st.caption("No choices selected yet.")
        return

    for index, step in enumerate(st.session_state.history, start=1):
        st.markdown(
            f"**{index}. {format_key_name(step['key'])}, couplet {step['node']}**  \n"
            f"{step['option']}: {step['morphology']}"
        )


def admin_is_allowed() -> bool:
    password = secret_value("admin_password")
    if not password:
        st.warning("Admin password is not configured. Add admin_password in Streamlit secrets before public use.")
        return True

    entered = st.text_input("Admin password", type="password")
    if entered != password:
        st.info("Enter the admin password to manage morphology photos.")
        return False
    return True


def save_admin_changes(keys_db: dict, message: str) -> None:
    save_keys(keys_db)
    if github_enabled():
        persist_keys_to_github(message)


def render_admin(keys_db: dict) -> None:
    st.subheader("Admin Morphology Photos")
    st.caption("Attach photos to each key, couplet, and A/B morphology choice.")

    if github_enabled():
        config = github_config()
        st.success(f"GitHub persistence is enabled for {config['repo']} on branch {config['branch']}.")
    else:
        st.warning(
            "GitHub persistence is not configured. Uploads work locally, but Streamlit Cloud can lose them after sleep or restart."
        )

    if not admin_is_allowed():
        return

    key_names = list(keys_db.keys())
    selected_key = st.selectbox("Step 1: select key", key_names, format_func=format_key_name)
    couplets = keys_db.get(selected_key, {})
    if not couplets:
        st.warning("This key has no couplet nodes.")
        return

    node_ids = sorted(
        couplets.keys(),
        key=lambda value: (0, int(value)) if str(value).isdigit() else (1, str(value)),
    )
    selected_node = st.selectbox("Step 2: select couplet", node_ids)
    selected_option = st.radio(
        "Step 3: select morphology option",
        ["option_a", "option_b"],
        format_func=lambda value: "A" if value == "option_a" else "B",
        horizontal=True,
    )

    option = couplets[selected_node].setdefault(selected_option, {})
    st.markdown(f"**Morphology shown to users**  \n{option.get('morphology', '')}")
    st.caption(f"Advances to: {option.get('advances_to', '')}")

    st.markdown("#### Step 4: attach photo")
    caption = st.text_input("Photo caption", placeholder="Example: Pedipalp thumb-claw process")
    uploaded_files = st.file_uploader(
        "Upload image from computer",
        type=IMAGE_TYPES,
        accept_multiple_files=True,
    )
    external_url = st.text_input("Or paste an external image URL")

    if st.button("Save photo reference", type="primary"):
        images = option.setdefault("images", [])
        saved_count = 0
        for uploaded_file in uploaded_files or []:
            images.append(save_uploaded_image(uploaded_file, selected_key, selected_node, selected_option, caption))
            saved_count += 1

        if external_url.strip():
            images.append(
                {
                    "url": external_url.strip(),
                    "caption": caption.strip(),
                    "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            saved_count += 1

        if saved_count:
            save_admin_changes(
                keys_db,
                f"Update morphology photos for {format_key_name(selected_key)} node {selected_node} {selected_option}",
            )
            st.success("Photo reference saved.")
            st.rerun()
        else:
            st.error("Upload a photo or paste an image URL first.")

    images = option_images(option)
    st.markdown("#### Existing photos")
    if not images:
        st.info("No photos attached to this morphology option yet.")
        return

    for index, image in enumerate(images):
        with st.container(border=True):
            render_option_images({"images": [image]})
            new_caption = st.text_input(
                "Caption",
                value=image.get("caption", ""),
                key=f"caption-{selected_key}-{selected_node}-{selected_option}-{index}",
            )
            col_save, col_remove = st.columns(2)
            if col_save.button("Update caption", key=f"caption-save-{index}", use_container_width=True):
                image["caption"] = new_caption
                save_admin_changes(keys_db, "Update morphology image caption")
                st.success("Caption updated.")
                st.rerun()
            if col_remove.button("Remove from key", key=f"image-remove-{index}", use_container_width=True):
                images.pop(index)
                save_admin_changes(keys_db, "Remove morphology image reference")
                st.warning("Photo reference removed from keys.json.")
                st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Acarology Taxonomy Key",
        page_icon=":microscope:",
        layout="wide",
    )

    keys_db = load_keys()
    if not keys_db:
        return

    initialize_state(keys_db)
    warnings = validate_keys(keys_db)

    st.title("Acarology Taxonomy Key")
    st.caption("Interactive dichotomous key for mite identification from morphology.")

    with st.sidebar:
        st.radio("Mode", ["Identify", "Admin"], key="app_mode")

        if st.session_state.app_mode == "Admin":
            st.info("Admin photos are saved to keys.json. GitHub secrets make them permanent on Streamlit Cloud.")
            if warnings:
                with st.expander("Data checks"):
                    for warning in warnings:
                        st.warning(warning)
        else:
            st.header("Specimen")
            st.text_input("Specimen code", key="specimen_code", placeholder="Slide, vial, or field number")
            st.text_area(
                "Morphology notes",
                key="observer_notes",
                placeholder="Record visible characters, host plant, mount quality, and uncertainty.",
                height=140,
            )

            st.header("Key")
            selected_key = st.selectbox(
                "Start or jump to key",
                options=list(keys_db.keys()),
                format_func=format_key_name,
                index=list(keys_db.keys()).index(st.session_state.current_key)
                if st.session_state.current_key in keys_db
                else 0,
            )

            if selected_key != st.session_state.current_key:
                restart(selected_key)
                st.rerun()

            col_restart, col_undo = st.columns(2)
            if col_restart.button("Restart", use_container_width=True):
                restart(st.session_state.current_key)
                st.rerun()
            if col_undo.button("Undo", use_container_width=True, disabled=not st.session_state.history):
                undo()
                st.rerun()

            if warnings:
                with st.expander("Data checks"):
                    for warning in warnings:
                        st.warning(warning)

    if st.session_state.app_mode == "Admin":
        render_admin(keys_db)
        return

    left, right = st.columns([1.7, 1])

    with right:
        render_path()

    with left:
        st.subheader(format_key_name(st.session_state.current_key))

        if st.session_state.diagnosis_complete:
            st.success("Diagnostic checkpoint reached")
            st.markdown(f"## {st.session_state.final_result}")

            next_key_names = get_next_key_candidates(st.session_state.final_result, keys_db)
            if next_key_names:
                for index, next_key_name in enumerate(next_key_names):
                    if st.button(
                        f"Continue to {format_key_name(next_key_name)}",
                        type="primary" if index == 0 else "secondary",
                        key=f"continue-{next_key_name}",
                    ):
                        restart(next_key_name)
                        st.rerun()
            elif parse_result(st.session_state.final_result)[0] in NEXT_TIER_MAP:
                st.info("A deeper key for this taxon is not loaded yet.")

            record = observation_record()
            record_json = json.dumps(record, indent=2)
            col_save, col_download = st.columns(2)
            if col_save.button("Save record", use_container_width=True):
                saved_path = save_record(record)
                st.toast(f"Saved {saved_path.name}")
            col_download.download_button(
                "Download record",
                data=record_json,
                file_name="mite-identification-record.json",
                mime="application/json",
                use_container_width=True,
            )
            return

        current_key = keys_db.get(st.session_state.current_key, {})
        couplet = current_key.get(st.session_state.current_node)
        if not couplet:
            st.error("This key is missing the current couplet node. Use Undo or check keys.json.")
            return

        st.markdown(
            f"**Couplet {st.session_state.current_node}:** Examine the specimen and choose the matching character state."
        )

        option_a = couplet.get("option_a", {})
        option_b = couplet.get("option_b", {})
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("#### A")
            st.info(option_a.get("morphology", "Missing morphology text."))
            render_option_images(option_a)
            if st.button("Select A", key=f"a-{st.session_state.current_key}-{st.session_state.current_node}", use_container_width=True):
                advance(
                    "A",
                    option_a.get("morphology", ""),
                    option_a.get("advances_to", ""),
                    option_images(option_a),
                )
                st.rerun()

        with col_b:
            st.markdown("#### B")
            st.info(option_b.get("morphology", "Missing morphology text."))
            render_option_images(option_b)
            if st.button("Select B", key=f"b-{st.session_state.current_key}-{st.session_state.current_node}", use_container_width=True):
                advance(
                    "B",
                    option_b.get("morphology", ""),
                    option_b.get("advances_to", ""),
                    option_images(option_b),
                )
                st.rerun()


if __name__ == "__main__":
    main()
