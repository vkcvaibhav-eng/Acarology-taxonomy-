import streamlit as st
import json

# --- 1. Load the Taxonomic Database ---
@st.cache_data
def load_keys():
    """Loads the structured dichotomous keys from the JSON file."""
    try:
        with open('keys.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        st.error("Error: 'keys.json' not found. Please ensure it is in the same folder as app.py.")
        return {}

keys_db = load_keys()

# --- 2. Initialize Session State (Memory) ---
# This ensures the app remembers where the student is in the key between clicks
if 'current_key' not in st.session_state:
    st.session_state.current_key = "Key_to_Superfamilies_of_Phytophagous_Mites"
if 'current_node' not in st.session_state:
    st.session_state.current_node = "1"
if 'history' not in st.session_state:
    st.session_state.history = []
if 'diagnosis_complete' not in st.session_state:
    st.session_state.diagnosis_complete = False
if 'final_result' not in st.session_state:
    st.session_state.final_result = ""

# --- Helper Function for Navigation ---
def process_next_step(next_step):
    """Processes the student's choice and moves to the next node or final diagnosis."""
    # Save the current state to history so we can implement an "Undo" later if needed
    st.session_state.history.append((st.session_state.current_key, st.session_state.current_node))
    
    if "Node" in next_step:
        # Move to the next couplet in the current key (e.g., "Node 2" -> "2")
        st.session_state.current_node = next_step.replace("Node ", "").strip()
    else:
        # A final taxon has been reached for this specific key
        st.session_state.diagnosis_complete = True
        st.session_state.final_result = next_step

# --- 3. Build the User Interface ---
st.set_page_config(page_title="Acarology Diagnostic Engine", page_icon="🔬", layout="centered")

st.title("🔬 Acarology Diagnostic Engine")
st.markdown("**Strict Dichotomous Key for M.Sc. & Ph.D. Researchers**")

# Sidebar for Resetting the app
with st.sidebar:
    st.header("Navigation")
    if st.button("🔄 Restart Diagnosis"):
        st.session_state.current_key = "Key_to_Superfamilies_of_Phytophagous_Mites"
        st.session_state.current_node = "1"
        st.session_state.history = []
        st.session_state.diagnosis_complete = False
        st.session_state.final_result = ""
        st.rerun()

# Main Application Logic
if keys_db:
    if st.session_state.diagnosis_complete:
        st.success("### Diagnostic Checkpoint Reached:")
        st.markdown(f"## **{st.session_state.final_result}**")
        st.info("Please record this result in your laboratory notebook.")
        
        # Check if we can drill down further (e.g., from Superfamily down to Family)
        # We deduce the next key name based on the result. 
        # Example: "Superfamily: Eriophyoidea" -> looks for "Key_to_Families_of_Eriophyoidea"
        taxon_rank, taxon_name = st.session_state.final_result.split(": ")
        next_tier_map = {
            "Superfamily": "Families",
            "Family": "Subfamilies",
            "Subfamily": "Tribes",
            "Tribe": "Genera"
        }
        
        if taxon_rank in next_tier_map:
            next_key_name = f"Key_to_{next_tier_map[taxon_rank]}_of_{taxon_name}"
            
            # If the deeper key exists in our JSON, offer to continue
            if next_key_name in keys_db:
                st.write(f"Would you like to continue keying out the {next_tier_map[taxon_rank].lower()} of **{taxon_name}**?")
                if st.button(f"Proceed to {taxon_name} Key ➡️"):
                    st.session_state.current_key = next_key_name
                    st.session_state.current_node = "1"
                    st.session_state.diagnosis_complete = False
                    st.rerun()
            else:
                st.warning("Deeper taxonomic keys for this clade have not been loaded into the database yet.")

    else:
        # Display the current dichotomous couplet
        current_key_dict = keys_db.get(st.session_state.current_key, {})
        couplet = current_key_dict.get(st.session_state.current_node)

        if couplet:
            formatted_key_name = st.session_state.current_key.replace("_", " ")
            st.subheader(f"Current Scope: {formatted_key_name}")
            st.markdown(f"**Couplet {st.session_state.current_node}:** Examine the morphological features under the microscope and select the matching state.")
            
            st.divider()

            # Create side-by-side columns for the options
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("### Option A")
                st.info(couplet['option_a']['morphology'])
                if st.button("Select Option A", key=f"btn_a_{st.session_state.current_node}", use_container_width=True):
                    process_next_step(couplet['option_a']['advances_to'])
                    st.rerun()

            with col2:
                st.markdown("### Option B")
                st.info(couplet['option_b']['morphology'])
                if st.button("Select Option B", key=f"btn_b_{st.session_state.current_node}", use_container_width=True):
                    process_next_step(couplet['option_b']['advances_to'])
                    st.rerun()
        else:
            st.error("Critical Error: Couplet logic broken or missing from JSON.")
