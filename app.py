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

TAXONOMIC_LEVELS = [
    "Kingdom",
    "Phylum",
    "Class",
    "Subclass",
    "Superorder",
    "Order",
    "Suborder",
    "Family",
    "Subfamily",
    "Tribe",
    "Genus",
    "Species",
]


def parse_taxonomic_result(text):
    """
    Convert:
    'Phylum: Arthropoda'
    to
    ('Phylum', 'Arthropoda')
    """
    if not isinstance(text, str):
        return None, None

    if ":" not in text:
        return None, None

    rank, name = text.split(":", 1)

    return rank.strip(), name.strip()


def build_taxonomic_path(history, final_result):
    """
    Extract taxonomy from diagnostic history.
    """
    taxonomy = {}

    for step in history:
        result = step.get("advanced_to", "")
        rank, name = parse_taxonomic_result(result)

        if rank and name:
            taxonomy[rank] = name

    rank, name = parse_taxonomic_result(final_result)

    if rank and name:
        taxonomy[rank] = name

    return taxonomy


def create_mind_map(taxonomy, keys_db=None):
    """
    Create Graphviz hierarchy.
    Green  = identified in session AND a deeper key exists.
    Yellow = identified in session but no deeper key loaded.
    Grey   = not yet reached / blank.
    """
    dot = Digraph()
    dot.attr(rankdir="TB")
    dot.attr("graph", bgcolor="white", pad="0.4", ranksep="0.5")
    dot.attr("node", fontname="Helvetica", fontsize="11")

    previous_node = None
    keys_db = keys_db or {}

    for rank in TAXONOMIC_LEVELS:
        value = taxonomy.get(rank, "")
        node_id = rank

        if value:
            # Check whether a deeper key exists for this taxon
            taxon_frag = key_fragment(value)
            has_key = any(
                k.endswith(f"_{taxon_frag}") or k.endswith(f"of_{taxon_frag}")
                for k in keys_db
            )
            if has_key:
                fillcolor, color, fontcolor = "#74c476", "#238b45", "black"   # green
            else:
                fillcolor, color, fontcolor = "#fdd835", "#f57f17", "black"   # yellow

            dot.node(
                node_id,
                f"{rank}\n{value}",
                style="filled,rounded",
                fillcolor=fillcolor,
                color=color,
                fontcolor=fontcolor,
                shape="box",
            )
        else:
            dot.node(
                node_id,
                f"{rank}\n—",
                style="filled,rounded",
                fillcolor="#eeeeee",
                color="#bdbdbd",
                fontcolor="#9e9e9e",
                shape="box",
            )

        if previous_node:
            dot.edge(previous_node, node_id, color="#aaaaaa")

        previous_node = node_id

    return dot


def build_full_key_tree(keys_db: dict) -> Digraph:
    """
    Build a mind-map of ALL keys loaded in keys.json.
    Each key becomes a node.  Edges follow the NEXT_KEY_ALIASES links
    and the tier-fallback naming convention.
    Nodes with a key file are green; nodes that are referenced but have
    no key file are shown in orange.
    """
    dot = Digraph()
    dot.attr(rankdir="TB")
    dot.attr("graph", bgcolor="white", pad="0.5", ranksep="0.6", nodesep="0.4")
    dot.attr("node", fontname="Helvetica", fontsize="10", shape="box", style="filled,rounded")

    all_key_names = set(keys_db.keys())

    # Collect every key name referenced anywhere (alias targets + tier matches)
    referenced: set[str] = set()
    for targets in NEXT_KEY_ALIASES.values():
        referenced.update(targets)

    # Add keys derived from tier-fallback naming
    for key_name in list(all_key_names):
        for targets in NEXT_KEY_ALIASES.values():
            referenced.update(targets)

    all_nodes = all_key_names | (referenced & all_key_names)

    # Draw nodes
    for key_name in sorted(all_nodes):
        label = format_key_name(key_name).replace(" ", "\n", 2)  # wrap long names
        if key_name in all_key_names:
            dot.node(key_name, label, fillcolor="#74c476", color="#238b45", fontcolor="black")
        else:
            dot.node(key_name, label, fillcolor="#ffb74d", color="#e65100", fontcolor="black")

    # Draw edges: for each key, find what keys it can lead to
    for result_label, target_keys in NEXT_KEY_ALIASES.items():
        # Find which source key(s) produce this result
        for source_key in all_key_names:
            for target_key in target_keys:
                if target_key in all_key_names:
                    dot.edge(source_key, target_key, label=result_label.split(": ")[-1],
                             fontsize="8", color="#555555")

    # Also link keys via tier fallback: Key_to_X → Key_to_Y_of_Z
    for key_name in sorted(all_key_names):
        suffix_match = re.search(r"of_(.+)$", key_name)
        if not suffix_match:
            continue
        taxon = suffix_match.group(1)
        for other_key in sorted(all_key_names):
            if other_key == key_name:
                continue
            if other_key.endswith(f"_of_{taxon}") or other_key.endswith(f"_{taxon}"):
                continue
            # link if the other key covers a parent tier
            for tier, children in TIER_FALLBACKS.items():
                expected = [f"Key_to_{child}_of_{taxon}" for child in children]
                if key_name in expected and other_key.endswith(f"_{taxon}"):
                    dot.edge(other_key, key_name, color="#aaaaaa")

    return dot

def collect_taxa_by_rank(keys_db: dict) -> dict[str, list[str]]:
    """
    Scan every advances_to value in keys_db and group unique taxon names
    by their rank.  Returns e.g. {"Class": ["Arachnida", ...], ...}
    """
    by_rank: dict[str, set[str]] = {rank: set() for rank in TAXONOMIC_LEVELS}
    for couplets in keys_db.values():
        if not isinstance(couplets, dict):
            continue
        for couplet in couplets.values():
            for opt in ("option_a", "option_b"):
                target = couplet.get(opt, {}).get("advances_to", "")
                if not target or target.startswith("Node "):
                    continue
                rank, name = parse_taxonomic_result(target)
                if rank and name and rank in by_rank:
                    # strip parenthetical qualifiers for cleaner dropdown labels
                    clean = name.split("(")[0].strip()
                    if clean:
                        by_rank[rank].add(clean)
    return {rank: sorted(names) for rank, names in by_rank.items()}


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
            st.image(url, caption=caption, use_container_width=True)
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
    st.session_state.setdefault("mm_show_map", False)

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

    tab1, tab2, tab3 = st.tabs([
        "Identify",
        "Admin",
        "Mind Map",
    ])
    with st.sidebar:
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

    with tab1:
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

            else:
                current_key = keys_db.get(st.session_state.current_key, {})
                couplet = current_key.get(st.session_state.current_node)
                if not couplet:
                    st.error("This key is missing the current couplet node. Use Undo or check keys.json.")
                else:
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

    with tab2:
        render_admin(keys_db)

    with tab3:
        st.header("Taxonomic Mind Map")
        st.caption(
            "Choose values for any ranks you want to highlight, then click **Generate Mind Map**. "
            "Leave a rank blank to show it as an empty node."
        )

        # Gather all known taxa from keys.json for dropdown options
        taxa_by_rank = collect_taxa_by_rank(keys_db)

        # ── Parameter selectors ───────────────────────────────────────────
        st.subheader("Select parameters")

        col1, col2, col3 = st.columns(3)
        col4, col5, col6 = st.columns(3)
        col7, col8, col9 = st.columns(3)
        col10, col11, col12 = st.columns(3)

        def rank_selector(col, rank: str, key: str):
            options = ["— (leave blank)"] + taxa_by_rank.get(rank, [])
            col.selectbox(rank, options, key=key)

        rank_selector(col1,  "Kingdom",    "mm_Kingdom")
        rank_selector(col2,  "Phylum",     "mm_Phylum")
        rank_selector(col3,  "Class",      "mm_Class")
        rank_selector(col4,  "Subclass",   "mm_Subclass")
        rank_selector(col5,  "Superorder", "mm_Superorder")
        rank_selector(col6,  "Order",      "mm_Order")
        rank_selector(col7,  "Suborder",   "mm_Suborder")
        rank_selector(col8,  "Family",     "mm_Family")
        rank_selector(col9,  "Subfamily",  "mm_Subfamily")
        rank_selector(col10, "Tribe",      "mm_Tribe")
        rank_selector(col11, "Genus",      "mm_Genus")
        rank_selector(col12, "Species",    "mm_Species")

        st.divider()

        col_gen, col_clr = st.columns([1, 1])
        generate = col_gen.button("🗺️ Generate Mind Map", type="primary", use_container_width=True)
        clear    = col_clr.button("🔄 Clear all",         use_container_width=True)

        if clear:
            for rank in TAXONOMIC_LEVELS:
                st.session_state[f"mm_{rank}"] = "— (leave blank)"
            st.rerun()

        if generate:
            st.session_state["mm_show_map"] = True

        if st.session_state.get("mm_show_map"):
            # Build taxonomy dict from user selections
            custom_taxonomy: dict[str, str] = {}
            for rank in TAXONOMIC_LEVELS:
                val = st.session_state.get(f"mm_{rank}", "— (leave blank)")
                if val and val != "— (leave blank)":
                    custom_taxonomy[rank] = val

            st.subheader("Mind Map")

            # Colour legend
            lcol1, lcol2, lcol3 = st.columns(3)
            lcol1.success("🟢  Selected — key exists")
            lcol2.warning("🟡  Selected — no key loaded")
            lcol3.info("⬜  Not selected (blank)")

            graph = create_mind_map(custom_taxonomy, keys_db)
            st.graphviz_chart(graph, use_container_width=True)

            # Hierarchy table
            st.subheader("Hierarchy table")
            rows = []
            for rank in TAXONOMIC_LEVELS:
                value = custom_taxonomy.get(rank, "")
                if value:
                    taxon_frag = key_fragment(value)
                    has_key = any(
                        k.endswith(f"_{taxon_frag}") or k.endswith(f"of_{taxon_frag}")
                        for k in keys_db
                    )
                    status = "✅ Key available" if has_key else "🟡 No deeper key"
                else:
                    status = "⬜ Not selected"
                rows.append({"Rank": rank, "Taxon": value or "—", "Status": status})
            st.dataframe(rows, use_container_width=True)


if __name__ == "__main__":
    main()
