"""Normalization configuration file for Galician XIX c. texts"""

from pathlib import Path

# resources -------------------------------------

config_dir = Path(__file__).parent
#IVDICO = Path("../data/apertium-glg-expanded-uniq.txt.gz").resolve()
#IVDICO = (config_dir.parent / "data" / "apertium-glg-expanded-uniq.txt.gz").resolve()
IVDICO = (config_dir.parent / "data" / "new_vocab_less_clitics.pkl").resolve()
#IVDICO_ES = (config_dir.parent / "data" / "aspell-es-expanded.txt.gz").resolve()
IVDICO_ES = (config_dir.parent / "data" / "aspell-es-expanded.txt.pkl").resolve()
#LMPATH= (config_dir.parent.parent / "nlm" / "nos-127.klm.bin").resolve()
LMPATH= (config_dir.parent / "data" / "nos-127.klm.bin").resolve()
LANGUAGES = ("gl", "es")




# candidate generation --------------------------
 
# should no longer be needed to have a list for accented characters,
# this was ok for python 2 (TweetNorm task) as they needed to be treated differently to the rest
alphabet = ('bcdfghjklmnpqrstvwxyzaeiou', ['á', 'é', 'í', 'ó', 'ú', 'ü', 'ñ', 'ç'])
accent_check_in_regexes = bool(1)

# To penalize edits that delete a vowel accent
# (consonants with diacritics like ç and ñ are not considered,
# they are not in the list of accented characters in editor.py)
# If set this to 0, it will not penalize
acc_ins_penalty = -0.5 # for now negative values penalize. 

# lm scoring ------------------------------------

lm_window = 4

