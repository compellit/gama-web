# Syllabification configuration file

from pathlib import Path

# Orthography processing

APOS = r"['‘’]"

#IO

data_dir = Path("data")
text_level_replacements = data_dir / "replacements_text.tsv"
syllable_replacements = data_dir / "syllabification_postprocessing.tsv"
syllable_replacements_for_g2s = data_dir / "syllabification-exceptions.lst"
words_with_hyphen_to_keep = data_dir / "hyphens_to_keep.txt" # unused

log_dir = "logs"
log_fn_template = "log_{batch_id}.txt"
licenses_log_fn_template = "log_licenses_{batch_id}.txt"
if not Path(log_dir).exists():
    Path(log_dir).mkdir(parents=True)

batch_cumulog = "batch_log.txt"
batch_cumulog_licenses = "batch_log_licenses.txt"

# pos-tagging
pos_model_path = data_dir / "galician-treegal-ud-2.5-191206.udpipe"

# df sheet namme for numberling lines
testset_sheet = "sel_20250623"
# gonna be created in the same directory as the script using this (scripts/add_poem_number_and_example_number.py)
line_group_log = Path("logs") / "line_group_log.tsv"
line_group_size = 6