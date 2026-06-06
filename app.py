from __future__ import annotations
from graphviz import Digraph
import re

import base64
import json
import mimetypes
from datetime import datetime
from html import escape
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
    "Tribe": "Subtribes",
    "Subtribe": "Genera",
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
    "Tribe": ["Subtribes", "Genera"],
    "Subtribe": ["Genera"],
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
def load_keys(file_mtime_ns: int = 0) -> dict:
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
    "Cohort",
    "Superfamily",
    "Family",
    "Subfamily",
    "Tribe",
    "Subtribe",
    "Genus",
    "Species",
]
PATH_LEVELS = [rank for rank in TAXONOMIC_LEVELS if rank != "Kingdom"]
BLANK_OPTION = "— (leave blank)"
ALL_LEVELS_OPTION = "All available levels"
ALL_GROUPS_OPTION = "All taxa"
KEY_NAME_RE = re.compile(r"^Key_to_(?P<target>.+?)_of_(?P<parent>.+)$")
RANK_ORDER = {rank: index for index, rank in enumerate(TAXONOMIC_LEVELS)}
PLURAL_TO_RANK = {
    "Phyla": "Phylum",
    "Classes": "Class",
    "Subclasses": "Subclass",
    "Superorders": "Superorder",
    "Orders": "Order",
    "Suborders": "Suborder",
    "Cohorts": "Cohort",
    "Superfamilies": "Superfamily",
    "Families": "Family",
    "Subfamilies": "Subfamily",
    "Tribes": "Tribe",
    "Subtribes": "Subtribe",
    "Genera": "Genus",
    "Species": "Species",
}
FAMILY_GROUP_SUFFIXES = [
    ("Superfamily", ("oidea",)),
    ("Family", ("idae",)),
    ("Subfamily", ("inae",)),
    ("Tribe", ("ini",)),
    ("Subtribe", ("ina",)),
]
FAMILY_GROUP_RANKS = {rank for rank, _ in FAMILY_GROUP_SUFFIXES}


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
    Create Graphviz hierarchy — compact horizontal layout.
    Green  = identified AND a deeper key exists.
    Yellow = identified but no deeper key loaded.
    Grey   = not selected / blank.
    """
    dot = Digraph()
    dot.attr(rankdir="LR")   # left-to-right = much more compact vertically
    dot.attr("graph", bgcolor="white", pad="0.15", ranksep="0.3", nodesep="0.2")
    dot.attr(
        "node",
        fontname="Helvetica",
        fontsize="8",
        shape="box",
        style="filled,rounded",
        width="0.9",
        height="0.35",
        fixedsize="false",
        margin="0.06,0.04",
    )
    dot.attr("edge", arrowsize="0.5", color="#aaaaaa")

    previous_node = None
    keys_db = keys_db or {}

    for rank in TAXONOMIC_LEVELS:
        value = taxonomy.get(rank, "")
        node_id = rank

        if value:
            taxon_frag = key_fragment(value)
            has_key = any(
                k.endswith(f"_{taxon_frag}") or k.endswith(f"of_{taxon_frag}")
                for k in keys_db
            )
            fillcolor, color, fontcolor = (
                ("#74c476", "#238b45", "black") if has_key
                else ("#fdd835", "#f57f17", "black")
            )
            label = f"{rank}: {value}"
            dot.node(node_id, label,
                     fillcolor=fillcolor, color=color, fontcolor=fontcolor)
        else:
            dot.node(node_id, rank,
                     fillcolor="#eeeeee", color="#bdbdbd", fontcolor="#9e9e9e")

        if previous_node:
            dot.edge(previous_node, node_id)

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


def rank_sort_key(rank: str) -> int:
    return RANK_ORDER.get(rank, len(TAXONOMIC_LEVELS) + 1)


def clean_taxon_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"\s+", " ", name.split("(")[0]).strip()


def normalize_taxon_name(name: str) -> str:
    return clean_taxon_name(name).casefold()


def infer_family_group_rank_from_suffix(name: str) -> str | None:
    clean = clean_taxon_name(name)
    if not clean:
        return None

    # Suffix rules are applied only as inference/fallback. Explicit ranks in
    # keys.json still win, which protects non-family-group names like
    # Cohort: Parasitengonina.
    for rank, suffixes in FAMILY_GROUP_SUFFIXES:
        if any(clean.endswith(suffix) for suffix in suffixes):
            return rank
    return None


def key_fragment_label(fragment: str, strip_context: bool = False) -> tuple[str, str | None]:
    label = fragment.replace("_", " ").strip()
    explicit_rank = None
    for rank in TAXONOMIC_LEVELS:
        prefix = f"{rank} "
        if label.startswith(prefix):
            explicit_rank = rank
            label = label[len(prefix):].strip()
            break

    if strip_context:
        label = re.sub(r"\s+excluding\s+.*$", "", label, flags=re.IGNORECASE).strip()
        label = re.sub(r"\s+(Adults|Larvae)$", "", label, flags=re.IGNORECASE).strip()

    return clean_taxon_name(label), explicit_rank


def parse_key_name(key_name: str) -> dict:
    match = KEY_NAME_RE.match(key_name)
    if not match:
        return {
            "target_part": "",
            "parent_fragment": "",
            "parent_label": "",
            "parent_taxon": "",
            "explicit_parent_rank": None,
        }

    parent_fragment = match.group("parent")
    parent_label, explicit_rank = key_fragment_label(parent_fragment)
    parent_taxon, stripped_rank = key_fragment_label(parent_fragment, strip_context=True)
    return {
        "target_part": match.group("target"),
        "parent_fragment": parent_fragment,
        "parent_label": parent_label,
        "parent_taxon": parent_taxon,
        "explicit_parent_rank": explicit_rank or stripped_rank,
    }


def implied_child_ranks_from_target_part(target_part: str) -> list[str]:
    matches = []
    for plural, rank in PLURAL_TO_RANK.items():
        match = re.search(rf"(^|_){re.escape(plural)}($|_)", target_part)
        if match:
            matches.append((match.start(), rank))
    return [rank for _, rank in sorted(matches)]


def merge_rank_lists(*rank_lists: list[str]) -> list[str]:
    merged = {
        rank
        for ranks in rank_lists
        for rank in ranks
        if rank in TAXONOMIC_LEVELS
    }
    return sorted(merged, key=rank_sort_key)


def implied_parent_rank_from_target_part(target_part: str) -> str | None:
    implied_child_ranks = implied_child_ranks_from_target_part(target_part)
    if not implied_child_ranks:
        return None
    first_child_index = rank_sort_key(implied_child_ranks[0])
    if first_child_index > 0:
        return TAXONOMIC_LEVELS[first_child_index - 1]
    return None


def fallback_parent_rank_from_child_ranks(child_ranks: list[str]) -> str | None:
    if not child_ranks:
        return None
    first_child_rank = min(child_ranks, key=rank_sort_key)
    child_index = rank_sort_key(first_child_rank)
    if child_index > 0:
        return TAXONOMIC_LEVELS[child_index - 1]
    return None


def collect_key_targets(couplets: dict) -> tuple[list[str], dict[str, list[str]]]:
    targets_by_rank: dict[str, set[str]] = {rank: set() for rank in TAXONOMIC_LEVELS}
    if not isinstance(couplets, dict):
        return [], {rank: [] for rank in TAXONOMIC_LEVELS}

    for couplet in couplets.values():
        if not isinstance(couplet, dict):
            continue
        for option_name in ("option_a", "option_b"):
            target = couplet.get(option_name, {}).get("advances_to", "")
            rank, name = parse_taxonomic_result(target)
            name = clean_taxon_name(name)
            if rank in targets_by_rank and name:
                targets_by_rank[rank].add(name)

    ranks = [rank for rank in TAXONOMIC_LEVELS if targets_by_rank[rank]]
    return ranks, {rank: sorted(names) for rank, names in targets_by_rank.items()}


def collect_known_taxon_ranks(keys_db: dict) -> dict[str, set[str]]:
    lookup: dict[str, set[str]] = {}

    def add(rank: str | None, name: str) -> None:
        if not rank or rank not in TAXONOMIC_LEVELS or not name:
            return
        lookup.setdefault(normalize_taxon_name(name), set()).add(rank)

    for rank, names in collect_taxa_by_rank(keys_db).items():
        for name in names:
            add(rank, name)

    for key_name, couplets in keys_db.items():
        parts = parse_key_name(key_name)
        child_ranks, _ = collect_key_targets(couplets)
        parent_taxon = parts["parent_taxon"]
        parent_key = normalize_taxon_name(parent_taxon)
        if not parent_taxon:
            continue

        if parts["explicit_parent_rank"]:
            add(parts["explicit_parent_rank"], parent_taxon)
        elif parent_key not in lookup:
            add(
                infer_family_group_rank_from_suffix(parent_taxon)
                or implied_parent_rank_from_target_part(parts["target_part"])
                or fallback_parent_rank_from_child_ranks(child_ranks),
                parent_taxon,
            )

    return lookup


def infer_parent_rank(
    parent_name: str,
    explicit_rank: str | None,
    implied_rank: str | None,
    child_ranks: list[str],
    rank_lookup: dict[str, set[str]],
) -> str | None:
    if explicit_rank:
        return explicit_rank

    known_ranks = rank_lookup.get(normalize_taxon_name(parent_name), set())
    suffix_rank = infer_family_group_rank_from_suffix(parent_name)
    if known_ranks:
        if len(known_ranks) == 1:
            return next(iter(known_ranks))
        if suffix_rank in known_ranks:
            return suffix_rank
        fallback_rank = fallback_parent_rank_from_child_ranks(child_ranks)
        if fallback_rank in known_ranks:
            return fallback_rank
        if implied_rank in known_ranks:
            return implied_rank
        return max(known_ranks, key=rank_sort_key)

    if suffix_rank:
        return suffix_rank

    if implied_rank:
        return implied_rank

    return fallback_parent_rank_from_child_ranks(child_ranks)


def collect_key_metadata(keys_db: dict) -> list[dict]:
    rank_lookup = collect_known_taxon_ranks(keys_db)
    metadata = []
    for key_name, couplets in keys_db.items():
        parts = parse_key_name(key_name)
        child_ranks, targets_by_rank = collect_key_targets(couplets)

        if not child_ranks and parts["target_part"] in PLURAL_TO_RANK:
            child_ranks = [PLURAL_TO_RANK[parts["target_part"]]]

        title_ranks = implied_child_ranks_from_target_part(parts["target_part"])
        display_ranks = merge_rank_lists(title_ranks, child_ranks)
        parent_rank = infer_parent_rank(
            parts["parent_taxon"],
            parts["explicit_parent_rank"],
            implied_parent_rank_from_target_part(parts["target_part"]),
            child_ranks,
            rank_lookup,
        )
        primary_rank = max(child_ranks, key=rank_sort_key) if child_ranks else ""
        metadata.append(
            {
                "key": key_name,
                "label": format_key_name(key_name),
                "target_part": parts["target_part"],
                "output_ranks": child_ranks,
                "display_ranks": display_ranks,
                "primary_rank": primary_rank,
                "parent_rank": parent_rank,
                "parent_label": parts["parent_label"],
                "parent_taxon": parts["parent_taxon"],
                "targets_by_rank": targets_by_rank,
            }
        )

    return metadata


def key_level_label(rank: str) -> str:
    return f"{rank} level" if rank else "Unknown level"


def metadata_for_key(metadata: list[dict], key_name: str) -> dict | None:
    for item in metadata:
        if item["key"] == key_name:
            return item
    return None


def metadata_display_ranks(item: dict) -> list[str]:
    return item.get("display_ranks") or item.get("output_ranks", [])


def key_scope_label(item: dict) -> str:
    target_part = item.get("target_part", "")
    if re.search(r"(^|_)from_Kerala($|_)", target_part):
        return "Kerala regional paper"
    return "General"


def home_group_key(item: dict) -> tuple[str, str, str]:
    display_ranks = metadata_display_ranks(item)
    first_rank = display_ranks[0] if display_ranks else item.get("primary_rank", "")
    return (
        item.get("parent_rank") or "",
        item.get("parent_taxon") or item.get("parent_label") or "",
        first_rank,
    )


def build_home_start_rows(metadata: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for item in metadata:
        grouped.setdefault(home_group_key(item), []).append(item)

    rows = []
    for items in grouped.values():
        has_regional = any(key_scope_label(item) != "General" for item in items)
        display_items = items if has_regional else []
        if not display_items:
            display_items = items

        if has_regional:
            row_items = display_items
            parent_label = row_items[0].get("parent_label", "")
            key_group = f"{parent_label} keys" if parent_label else row_items[0]["label"]
        else:
            row_items = [display_items[0]]
            parent_label = row_items[0].get("parent_label", "")
            key_group = row_items[0]["label"]

        ranks = merge_rank_lists(*[metadata_display_ranks(item) for item in row_items])
        scopes = sorted({key_scope_label(item) for item in row_items})
        scope_text = "; ".join(scopes)
        included_keys = "; ".join(item["label"] for item in row_items)

        rows.append(
            {
                "Classification step": key_group,
                "Identifies": ", ".join(ranks) or "Unknown",
                "Taxon covered": parent_label,
                "Scope": scope_text,
                "Keys included": included_keys,
            }
        )

        if not has_regional and len(display_items) > 1:
            for item in display_items[1:]:
                rows.append(
                    {
                        "Classification step": item["label"],
                        "Identifies": ", ".join(metadata_display_ranks(item)) or "Unknown",
                        "Taxon covered": item.get("parent_label", ""),
                        "Scope": key_scope_label(item),
                        "Keys included": item["label"],
                    }
                )

    return sorted(rows, key=lambda row: (row["Taxon covered"], row["Classification step"]))


def build_taxonomy_relationships(keys_db: dict) -> dict[tuple[str, str], dict[str, set[str]]]:
    relationships: dict[tuple[str, str], dict[str, set[str]]] = {}
    for meta in collect_key_metadata(keys_db):
        parent_rank = meta.get("parent_rank")
        parent_name = meta.get("parent_taxon")
        if not parent_rank or not parent_name:
            continue

        parent = (parent_rank, parent_name)
        child_map = relationships.setdefault(parent, {})
        for child_rank, names in meta["targets_by_rank"].items():
            for name in names:
                if name:
                    child_map.setdefault(child_rank, set()).add(name)

    return relationships


def collect_descendants_for_rank(
    relationships: dict[tuple[str, str], dict[str, set[str]]],
    start_rank: str,
    start_name: str,
    target_rank: str,
) -> list[str]:
    queue = [(start_rank, start_name)]
    seen: set[tuple[str, str]] = set()
    descendants: set[str] = set()

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)

        for child_rank, names in relationships.get(current, {}).items():
            for name in names:
                if child_rank == target_rank:
                    descendants.add(name)
                if rank_sort_key(child_rank) < rank_sort_key(target_rank):
                    queue.append((child_rank, name))

    return sorted(descendants)


def collect_all_descendants(
    relationships: dict[tuple[str, str], dict[str, set[str]]],
    start_rank: str,
    start_name: str,
) -> dict[str, set[str]]:
    queue = [(start_rank, start_name)]
    seen: set[tuple[str, str]] = set()
    descendants: dict[str, set[str]] = {}

    while queue:
        current_rank, current_name = queue.pop(0)
        current = (current_rank, current_name)
        if current in seen:
            continue
        seen.add(current)

        for child_rank, names in relationships.get(current, {}).items():
            for name in names:
                if not name:
                    continue
                descendants.setdefault(child_rank, set()).add(name)
                if rank_sort_key(child_rank) > rank_sort_key(current_rank):
                    queue.append((child_rank, name))

    return descendants


def collect_intermediate_family_group_options(
    relationships: dict[tuple[str, str], dict[str, set[str]]],
    start_rank: str,
    start_name: str,
    target_rank: str,
) -> list[str]:
    if target_rank not in FAMILY_GROUP_RANKS:
        return []

    start_descendants = collect_all_descendants(relationships, start_rank, start_name)
    if not start_descendants:
        return []

    options: set[str] = set()
    for candidate_rank, candidate_name in relationships:
        if candidate_rank != target_rank:
            continue

        candidate_descendants = collect_all_descendants(relationships, candidate_rank, candidate_name)
        for descendant_rank, descendant_names in candidate_descendants.items():
            if rank_sort_key(descendant_rank) <= rank_sort_key(target_rank):
                continue
            if descendant_names & start_descendants.get(descendant_rank, set()):
                options.add(candidate_name)
                break

    return sorted(options)


def infer_species_for_genus(genus: str, all_species: list[str]) -> list[str]:
    genus = clean_taxon_name(genus)
    if not genus:
        return []
    abbreviation = f"{genus[0]}." if genus else ""
    return sorted(
        species
        for species in all_species
        if species.startswith(f"{genus} ") or species.startswith(f"{abbreviation} ")
    )


def mind_map_options_for_rank(
    rank: str,
    selected_taxonomy: dict[str, str],
    taxa_by_rank: dict[str, list[str]],
    relationships: dict[tuple[str, str], dict[str, set[str]]],
) -> list[str]:
    prior_selected = [
        (prior_rank, selected_taxonomy[prior_rank])
        for prior_rank in TAXONOMIC_LEVELS
        if rank_sort_key(prior_rank) < rank_sort_key(rank) and selected_taxonomy.get(prior_rank)
    ]

    for prior_rank, prior_name in reversed(prior_selected):
        options = collect_descendants_for_rank(relationships, prior_rank, prior_name, rank)
        if not options:
            options = collect_intermediate_family_group_options(
                relationships,
                prior_rank,
                prior_name,
                rank,
            )
        if options:
            return options

    if rank == "Species" and selected_taxonomy.get("Genus"):
        return infer_species_for_genus(selected_taxonomy["Genus"], taxa_by_rank.get("Species", []))

    if prior_selected:
        return []

    return taxa_by_rank.get(rank, [])


def key_counts_by_rank(metadata: list[dict]) -> dict[str, int]:
    counts = {rank: 0 for rank in TAXONOMIC_LEVELS}
    for item in metadata:
        for rank in metadata_display_ranks(item):
            if rank in counts:
                counts[rank] += 1
    return counts


def clear_mind_map_state() -> None:
    for rank in TAXONOMIC_LEVELS:
        st.session_state[f"mm_{rank}"] = BLANK_OPTION
    st.session_state["mm_show_map"] = False


def parse_result(result: str) -> tuple[str | None, str | None]:
    return parse_taxonomic_result(result)


def key_fragment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in value)
    return "_".join(part for part in cleaned.split("_") if part)


def append_candidate(candidates: list[str], key_name: str, keys_db: dict) -> None:
    if key_name in keys_db and key_name not in candidates:
        candidates.append(key_name)


def append_taxon_join_candidates(candidates: list[str], rank: str, name: str, keys_db: dict) -> None:
    target_name = normalize_taxon_name(name)
    for item in collect_key_metadata(keys_db):
        if item.get("parent_rank") != rank:
            continue
        if normalize_taxon_name(item.get("parent_taxon", "")) != target_name:
            continue
        append_candidate(candidates, item["key"], keys_db)


def get_next_key_candidates(result: str, keys_db: dict) -> list[str]:
    rank, name = parse_result(result)
    if not rank or not name:
        return []

    candidates = []
    for key_name in NEXT_KEY_ALIASES.get(result, []):
        append_candidate(candidates, key_name, keys_db)

    append_taxon_join_candidates(candidates, rank, name, keys_db)

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
            st.image(url, caption=caption, width="stretch")
        elif path and Path(path).exists():
            st.image(path, caption=caption, width="stretch")
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


def taxonomy_from_stack(stack: list[dict]) -> dict[str, str]:
    taxonomy: dict[str, str] = {}
    for item in stack:
        rank = item.get("rank")
        name = item.get("name")
        if rank in TAXONOMIC_LEVELS and name:
            taxonomy[rank] = name
    return taxonomy


def sync_taxonomy_path() -> None:
    st.session_state.taxonomy_path = taxonomy_from_stack(st.session_state.get("taxonomy_stack", []))


def remember_taxonomic_checkpoint(result: str) -> None:
    rank, name = parse_taxonomic_result(result)
    name = clean_taxon_name(name)
    if rank in TAXONOMIC_LEVELS and name:
        st.session_state.taxonomy_stack.append({"rank": rank, "name": name, "result": result})
        sync_taxonomy_path()


def forget_taxonomic_checkpoint(result: str) -> None:
    stack = st.session_state.get("taxonomy_stack", [])
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].get("result") == result:
            stack.pop(index)
            break
    sync_taxonomy_path()


def render_taxonomy_ribbon(taxonomy: dict[str, str], title: str = "Taxonomy path") -> None:
    st.markdown(f"**{title}**")
    chips = []
    for rank in PATH_LEVELS:
        value = taxonomy.get(rank, "")
        rank_html = escape(rank)
        if value:
            chips.append(
                "<div style='min-width:116px;flex:1 1 116px;"
                "border:1px solid #b7d7bd;background:#edf8ef;border-radius:8px;"
                "padding:8px 10px;'>"
                f"<div style='font-size:11px;color:#4b5563;'>{rank_html}</div>"
                f"<div style='font-weight:650;color:#111827;'>{escape(value)}</div>"
                "</div>"
            )
        else:
            chips.append(
                "<div style='min-width:116px;flex:1 1 116px;"
                "border:1px solid #d7dbe2;background:#f6f7f9;border-radius:8px;"
                "padding:8px 10px;'>"
                f"<div style='font-size:11px;color:#6b7280;'>{rank_html}</div>"
                "<div style='color:#9ca3af;'>Not reached</div>"
                "</div>"
            )

    st.markdown(
        "<div style='display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 16px 0;'>"
        + "".join(chips)
        + "</div>",
        unsafe_allow_html=True,
    )


def initialize_state(keys_db: dict) -> None:
    default_key = DEFAULT_KEY if DEFAULT_KEY in keys_db else next(iter(keys_db), "")
    st.session_state.setdefault("current_key", default_key)
    st.session_state.setdefault("current_node", "1")
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("taxonomy_stack", [])
    st.session_state.setdefault("taxonomy_path", taxonomy_from_stack(st.session_state.taxonomy_stack))
    st.session_state.setdefault("diagnosis_complete", False)
    st.session_state.setdefault("final_result", "")
    st.session_state.setdefault("specimen_code", "")
    st.session_state.setdefault("observer_notes", "")
    st.session_state.setdefault("mm_show_map", False)

    if st.session_state.current_key not in keys_db:
        restart(default_key)


def restart(key_name: str, clear_notes: bool = False, reset_taxonomy: bool = True) -> None:
    st.session_state.current_key = key_name
    st.session_state.current_node = "1"
    st.session_state.history = []
    st.session_state.diagnosis_complete = False
    st.session_state.final_result = ""
    if reset_taxonomy:
        st.session_state.taxonomy_stack = []
        st.session_state.taxonomy_path = {}
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
        remember_taxonomic_checkpoint(target)


def undo() -> None:
    if not st.session_state.history:
        return

    previous = st.session_state.history.pop()
    forget_taxonomic_checkpoint(previous.get("advanced_to", ""))
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
        "taxonomy": st.session_state.get("taxonomy_path", {}),
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
    taxonomy = st.session_state.get("taxonomy_path", {})
    if taxonomy:
        render_taxonomy_ribbon(taxonomy, "Phylum to species path")

    if not st.session_state.history:
        st.caption("No choices selected yet.")
        return

    st.markdown("**Current key choices**")
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
            if col_save.button("Update caption", key=f"caption-save-{index}", width="stretch"):
                image["caption"] = new_caption
                save_admin_changes(keys_db, "Update morphology image caption")
                st.success("Caption updated.")
                st.rerun()
            if col_remove.button("Remove from key", key=f"image-remove-{index}", width="stretch"):
                images.pop(index)
                save_admin_changes(keys_db, "Remove morphology image reference")
                st.warning("Photo reference removed from keys.json.")
                st.rerun()


def render_home(keys_db: dict, metadata: list[dict], warnings: list[str]) -> None:
    taxa_by_rank = collect_taxa_by_rank(keys_db)
    key_counts = key_counts_by_rank(metadata)

    st.header("Key Coverage")
    focus_ranks = ["Order", "Family", "Genus", "Species"]
    metric_cols = st.columns(len(focus_ranks))
    for col, rank in zip(metric_cols, focus_ranks):
        col.metric(f"{rank} keys", key_counts.get(rank, 0))
        col.caption(f"{len(taxa_by_rank.get(rank, []))} {rank.lower()} names")

    st.subheader("Available levels")
    rows = []
    for rank in PATH_LEVELS:
        keys_at_rank = [item["label"] for item in metadata if rank in metadata_display_ranks(item)]
        taxa_count = len(taxa_by_rank.get(rank, []))
        if not keys_at_rank and not taxa_count:
            continue
        rows.append(
            {
                "Level": rank,
                "Keys available": len(keys_at_rank),
                "Taxa reachable": taxa_count,
                "Example key": keys_at_rank[0] if keys_at_rank else "",
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)

    st.subheader("Start points")
    start_rows = build_home_start_rows(metadata)
    st.dataframe(start_rows, width="stretch", hide_index=True)

    if warnings:
        with st.expander("Data checks"):
            for warning in warnings:
                st.warning(warning)


def main() -> None:
    st.set_page_config(
        page_title="Acarology Taxonomy Key",
        page_icon=":microscope:",
        layout="wide",
    )

    keys_mtime_ns = KEYS_PATH.stat().st_mtime_ns if KEYS_PATH.exists() else 0
    keys_db = load_keys(keys_mtime_ns)
    if not keys_db:
        return

    initialize_state(keys_db)
    warnings = validate_keys(keys_db)
    metadata = collect_key_metadata(keys_db)

    st.title("Acarology Taxonomy Key")
    st.caption("Interactive dichotomous key for mite identification from morphology.")

    tab_home, tab_identify, tab_mind_map, tab_admin = st.tabs([
        "Home",
        "Identify",
        "Mind Map",
        "Admin",
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
        level_options = [ALL_LEVELS_OPTION] + [
            rank
            for rank in PATH_LEVELS
            if any(rank in metadata_display_ranks(item) for item in metadata)
        ]
        if st.session_state.get("key_level_filter") not in level_options:
            st.session_state.key_level_filter = ALL_LEVELS_OPTION

        level_filter = st.selectbox(
            "Key level",
            options=level_options,
            key="key_level_filter",
            format_func=lambda value: value if value == ALL_LEVELS_OPTION else key_level_label(value),
        )

        filtered_by_level = [
            item for item in metadata
            if level_filter == ALL_LEVELS_OPTION or level_filter in metadata_display_ranks(item)
        ]
        parent_options = [ALL_GROUPS_OPTION] + sorted(
            {
                item["parent_label"]
                for item in filtered_by_level
                if item.get("parent_label")
            }
        )
        if st.session_state.get("key_parent_filter") not in parent_options:
            st.session_state.key_parent_filter = ALL_GROUPS_OPTION

        parent_filter = st.selectbox(
            "Taxon covered",
            options=parent_options,
            key="key_parent_filter",
        )

        filtered_metadata = [
            item for item in filtered_by_level
            if parent_filter == ALL_GROUPS_OPTION or item.get("parent_label") == parent_filter
        ]
        if not filtered_metadata:
            st.warning("No key is available for this level and taxon.")
        else:
            key_options = [item["key"] for item in filtered_metadata]
            label_by_key = {item["key"]: item["label"] for item in filtered_metadata}
            selected_key = st.selectbox(
                "Start or jump to key",
                options=key_options,
                format_func=lambda key_name: label_by_key.get(key_name, format_key_name(key_name)),
                index=key_options.index(st.session_state.current_key)
                if st.session_state.current_key in key_options
                else 0,
            )

            if selected_key != st.session_state.current_key:
                restart(selected_key)
                st.rerun()

        col_restart, col_undo = st.columns(2)
        if col_restart.button("Restart", width="stretch"):
            restart(st.session_state.current_key)
            st.rerun()
        if col_undo.button("Undo", width="stretch", disabled=not st.session_state.history):
            undo()
            st.rerun()

        if warnings:
            with st.expander("Data checks"):
                for warning in warnings:
                    st.warning(warning)

    with tab_home:
        render_home(keys_db, metadata, warnings)

    with tab_identify:
        left, right = st.columns([1.7, 1])

        with right:
            render_path()

        with left:
            st.subheader(format_key_name(st.session_state.current_key))

            if st.session_state.diagnosis_complete:
                next_key_names = get_next_key_candidates(st.session_state.final_result, keys_db)
                if next_key_names:
                    st.success("Classification checkpoint reached - lower key is loaded")
                    st.markdown(f"## {st.session_state.final_result}")
                    st.caption("This is not a dead end. Continue below with the next loaded key for this taxon.")
                    for index, next_key_name in enumerate(next_key_names):
                        if st.button(
                            f"Continue below: {format_key_name(next_key_name)}",
                            type="primary" if index == 0 else "secondary",
                            key=f"continue-{next_key_name}",
                        ):
                            restart(next_key_name, reset_taxonomy=False)
                            st.rerun()
                elif parse_result(st.session_state.final_result)[0] in NEXT_TIER_MAP:
                    st.warning("No lower key is loaded yet for this exact taxon.")
                    st.markdown(f"## {st.session_state.final_result}")
                else:
                    st.success("Identification endpoint reached")
                    st.markdown(f"## {st.session_state.final_result}")

                record = observation_record()
                record_json = json.dumps(record, indent=2)
                col_save, col_download = st.columns(2)
                if col_save.button("Save record", width="stretch"):
                    saved_path = save_record(record)
                    st.toast(f"Saved {saved_path.name}")
                col_download.download_button(
                    "Download record",
                    data=record_json,
                    file_name="mite-identification-record.json",
                    mime="application/json",
                    width="stretch",
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
                        if st.button("Select A", key=f"a-{st.session_state.current_key}-{st.session_state.current_node}", width="stretch"):
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
                        if st.button("Select B", key=f"b-{st.session_state.current_key}-{st.session_state.current_node}", width="stretch"):
                            advance(
                                "B",
                                option_b.get("morphology", ""),
                                option_b.get("advances_to", ""),
                                option_images(option_b),
                            )
                            st.rerun()

    with tab_mind_map:
        st.header("Taxonomic Mind Map")
        st.caption(
            "Choose values for any ranks you want to highlight, then click **Generate Mind Map**. "
            "Leave a rank blank to show it as an empty node."
        )

        taxa_by_rank = collect_taxa_by_rank(keys_db)
        relationships = build_taxonomy_relationships(keys_db)

        st.subheader("Select parameters")
        selector_cols = st.columns(3)
        selected_taxonomy: dict[str, str] = {}
        for index, rank in enumerate(TAXONOMIC_LEVELS):
            key = f"mm_{rank}"
            options = [BLANK_OPTION] + mind_map_options_for_rank(
                rank,
                selected_taxonomy,
                taxa_by_rank,
                relationships,
            )
            if st.session_state.get(key, BLANK_OPTION) not in options:
                st.session_state[key] = BLANK_OPTION

            selected_value = selector_cols[index % 3].selectbox(rank, options, key=key)
            if selected_value != BLANK_OPTION:
                selected_taxonomy[rank] = selected_value

        st.divider()

        col_gen, col_clr = st.columns([1, 1])
        generate = col_gen.button("Generate Mind Map", type="primary", width="stretch")
        col_clr.button("Clear all", width="stretch", on_click=clear_mind_map_state)

        if generate:
            st.session_state["mm_show_map"] = True

        if st.session_state.get("mm_show_map"):
            custom_taxonomy = selected_taxonomy.copy()

            st.subheader("Mind Map")

            lcol1, lcol2, lcol3 = st.columns(3)
            lcol1.success("Selected - key exists")
            lcol2.warning("Selected - no key loaded")
            lcol3.info("Not selected")

            graph = create_mind_map(custom_taxonomy, keys_db)
            st.graphviz_chart(graph, width="stretch")

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
                    status = "Key available" if has_key else "No deeper key"
                else:
                    status = "Not selected"
                rows.append({"Rank": rank, "Taxon": value or BLANK_OPTION, "Status": status})
            st.dataframe(rows, width="stretch", hide_index=True)

    with tab_admin:
        render_admin(keys_db)


if __name__ == "__main__":
    main()
