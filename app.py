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
# Set the baseline starting checkpoint at the Subclass level
if 'current_key' not in st.session_state:
    st.session_state.current_key = "Key_to_Orders_of_Subclass_Acari"
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
    # Save the current state to history for backtracking capabilities
    st.session_state.history.append((st.session_state.current_key, st.session_state.current_node))
    
    if "Node" in next_step:
        # Move to the next couplet in the current key (e.g., "Node 2" -> "2")
        st.session_state.current_node = next_step.replace("Node ", "").strip()
    else:
        # A final taxon rank has been reached for this specific key segment
        st.session_state.diagnosis_complete = True
        st.session_state.final_result = next_step

def reset_to_start():
    """Resets the state machine completely back to the Subclass Acari root."""
    st.session_state.current_key = "Key_to_Orders_of_Subclass_Acari"
    st.session_state.current_node = "1"
    st.session_state.history = []
    st.session_state.diagnosis_complete = False
    st.session_state.final_result = ""

# --- 3. Build the User Interface ---
st.set_page_config(page_title="Acarology Diagnostic Engine", page_icon="🔬", layout="centered")

st.title("🔬 Acarology Diagnostic Engine")
st.markdown("**Interactive Identification System for M.Sc. & Ph.D. Researchers**")

# Sidebar for controls and trace history
with st.sidebar:
    st.header("Navigation Controls")
    if st.button("🔄 Restart From Subclass Root", use_container_width=True):
        reset_to_start()
        st.rerun()
        
    if st.session_state.history:
        if st.button("⬅️ Step Backward (Undo)", use_container_width=True):
            prev_key, prev_node = st.session_state.history.pop()
            st.session_state.current_key = prev_key
            st.session_state.current_node = prev_node
            st.session_state.diagnosis_complete = False
            st.session_state.final_result = ""
            st.rerun()

# Main Application Logic
if keys_db:
    if st.session_state.diagnosis_complete:
        st.success("### Diagnostic Checkpoint Reached:")
        st.markdown(f"## **{st.session_state.final_result}**")
        st.info("Log this result in your tracking data before continuing down the hierarchy.")
        
        # Determine potential downstream keys inside keys.json based on the current milestone
        if ": " in st.session_state.final_result:
            taxon_rank, taxon_name = st.session_state.final_result.split(": ")
            
            # Map out possible naming combinations for keys present in the keys.json file
            possible_next_keys = [
                f"Key_to_Families_of_{taxon_name}",
                f"Key_to_Families_of_{taxon_name}_I",
                f"Key_to_Superfamilies_of_Phytophagous_Mites" if "Trombidiformes" in taxon_name else "",
                f"Key_to_Subfamilies_of_{taxon_name}"
            ]
            
            # Find the first matching sub-key loaded in your database
            target_key = next((k for k in possible_next_keys if k in keys_db), None)
            
            if target_key:
                formatted_next_target = target_key.replace("_", " ")
                st.write(f"Deeper keys found. Proceed to: **{formatted_next_target}**?")
                if st.button(f"Drill Down Into {taxon_name} Clade ➡️", use_container_width=True):
                    st.session_state.current_key = target_key
                    st.session_state.current_node = "1"
                    st.session_state.diagnosis_complete = False
                    st.rerun()
            else:
                st.warning(f"No further lower-tier keys for **{taxon_name}** are implemented in the keys.json file yet.")
                
    else:
        # Fetch data parameters for the current position in the matrix
        current_key_dict = keys_db.get(st.session_state.current_key, {})
        couplet = current_key_dict.get(st.session_state.current_node)

        if couplet:
            display_title = st.session_state.current_key.replace("_", " ")
            st.subheader(f"Current Matrix: {display_title}")
            st.markdown(f"**Couplet {st.session_state.current_node}:** Assess structural and diagnostic characteristics under magnification.")
            
            st.divider()

            # Present choices in balanced structural segments
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("### Choice A")
                st.info(couplet['option_a']['morphology'])
                if st.button("Select Option A", key=f"action_a_{st.session_state.current_node}", use_container_width=True):
                    process_next_step(couplet['option_a']['advances_to'])
                    st.rerun()

            with col2:
                st.markdown("### Choice B")
                st.info(couplet['option_b']['morphology'])
                if st.button("Select Option B", key=f"action_b_{st.session_state.current_node}", use_container_width=True):
                    process_next_step(couplet['option_b']['advances_to'])
                    st.rerun()
        else:
            st.error("Structure Error: The diagnostic pointer targeted a node variant that does not exist in keys.json.")
