from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st


KEYS_PATH = Path("keys.json")
OBSERVATION_DIR = Path("outputs/observations")
DEFAULT_KEY = "Key_to_Superfamilies_of_Phytophagous_Mites"
NEXT_TIER_MAP = {
    "Superfamily": "Families",
    "Family": "Subfamilies",
    "Subfamily": "Tribes",
    "Tribe": "Genera",
    "Genus": "Species",
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


def format_key_name(key_name: str) -> str:
    return key_name.replace("Key_to_", "").replace("_", " ")


def parse_result(result: str) -> tuple[str | None, str | None]:
    if ": " not in result:
        return None, None
    rank, name = result.split(": ", 1)
    return rank.strip(), name.strip()


def get_next_key_name(result: str) -> str | None:
    rank, name = parse_result(result)
    if not rank or rank not in NEXT_TIER_MAP:
        return None
    return f"Key_to_{NEXT_TIER_MAP[rank]}_of_{name}"


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
                if target.startswith("Node "):
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


def restart(key_name: str, clear_notes: bool = False) -> None:
    st.session_state.current_key = key_name
    st.session_state.current_node = "1"
    st.session_state.history = []
    st.session_state.diagnosis_complete = False
    st.session_state.final_result = ""
    if clear_notes:
        st.session_state.specimen_code = ""
        st.session_state.observer_notes = ""


def advance(option_label: str, morphology: str, target: str) -> None:
    st.session_state.history.append(
        {
            "key": st.session_state.current_key,
            "node": st.session_state.current_node,
            "option": option_label,
            "morphology": morphology,
            "advanced_to": target,
        }
    )

    if target.startswith("Node "):
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


def main() -> None:
    st.set_page_config(
        page_title="Acarology Taxonomy Key",
        page_icon="microscope",
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

    left, right = st.columns([1.7, 1])

    with right:
        render_path()

    with left:
        st.subheader(format_key_name(st.session_state.current_key))

        if st.session_state.diagnosis_complete:
            st.success("Diagnostic checkpoint reached")
            st.markdown(f"## {st.session_state.final_result}")

            next_key_name = get_next_key_name(st.session_state.final_result)
            if next_key_name and next_key_name in keys_db:
                if st.button(f"Continue to {format_key_name(next_key_name)}", type="primary"):
                    restart(next_key_name)
                    st.rerun()
            elif next_key_name:
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

        option_a = couplet["option_a"]
        option_b = couplet["option_b"]
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("#### A")
            st.info(option_a["morphology"])
            if st.button("Select A", key=f"a-{st.session_state.current_key}-{st.session_state.current_node}", use_container_width=True):
                advance("A", option_a["morphology"], option_a["advances_to"])
                st.rerun()

        with col_b:
            st.markdown("#### B")
            st.info(option_b["morphology"])
            if st.button("Select B", key=f"b-{st.session_state.current_key}-{st.session_state.current_node}", use_container_width=True):
                advance("B", option_b["morphology"], option_b["advances_to"])
                st.rerun()


if __name__ == "__main__":
    main()
