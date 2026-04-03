import argparse
from collections import OrderedDict
import copy
from importlib import reload
import logging
from pathlib import Path
import re
import sys
import time

import stanza
from stanza import DownloadMethod

import config as cf
from data import stress_info as sti
import grapheme2syllable as g2s
from normalization import lm_manager as lmg
from normalization import normalizer
from normalization import normconfig as ncf

import utils as ut


PUNCT_TO_REMOVE = ".,;?!¿¡:«»()”“„"
# last character is 'box drawings light horizontal' U+2500
PUNCT_TO_SPACE = "—─"
PUNCT_RE = re.compile(f"([{PUNCT_TO_REMOVE}]+)", re.UNICODE)
PUNCT_TO_SPACE_RE = re.compile(f"([{PUNCT_TO_SPACE}]+)", re.UNICODE)


def parse_args():
    """
    Parse command line arguments.
    """
    parser = argparse.ArgumentParser(description="Grapheme to syllable client for running text.")
    parser.add_argument("input_file", type=Path, help="Path to the input file containing text.")
    parser.add_argument("--preprocess", "-p", action="store_true",
                        help="Preprocess the input text modernizing some sequences (without altering metrically relevant content).")
    parser.add_argument("--stress_marks", type=str, choices=["acute", "circumflex", "allupper", "apos"], default="acute",
                        help="How to mark the stressed syllable in the output. The 'circumflex' and 'acute' options prefix a stress mark to the stressed syllable, while 'allupper' makes it all caps.")
    parser.add_argument("--destress", "-d", action="store_true",
                        help="In syllabified output, remove stress marking if the syllable is lexically unstressed.")
    parser.add_argument("--normalize", "-n", action="store_true",
                        help="Replace out of vocabulary by in-vocabulary forms from an inflected forms dictionary.")
    parser.add_argument("--spanishfy", "-s", action="store_true",
                        help="Make text closer to Spanish orthographic stress rules to see if Gumper improves. Applies only if --normalize is set.")
    parser.add_argument("--possessives", "-m", action="store_true",
                        help="Whether to add a stress mark or not to preposed possessives. This helps Gumper, but need not be justified for other types of preprocessing")
    parser.add_argument("--possessives_restore_vowel", "-v", action="store_true",
                        help="In preposed possessives, keep the stress mark on the *syllable* but"
                             "not on the vowel. Useful to get lexical syllabification input for"
                             "lexical to metrical conversion, not useful for Gumper.")
    parser.add_argument("--batch_id", "-b", type=str, default="")
    parser.add_argument("--batch_comment", type=str, default="")
    parser.add_argument("--debug_g2s", "-g", action="store_true", help="Print to console debug infos from grapheme to syllable converter")
    return parser.parse_args()


def preprocess_orthography(txt: str, ignore_flagged=True) -> str:
    """
    Preprocess the input text to modernize some sequences without altering metrically relevant content.

    Args:
        txt (str): The input text to preprocess.
        ignore_flagged (bool): Whether to ignore expressions tagged with 'skip'
            in the replacement data.

    Returns:
        str: The preprocessed text.
    """
    pat2rep = ut.load_text_replacements(cf, ignore_flagged=ignore_flagged)
    for pat, rep in pat2rep.items():
        txt = re.sub(pat, rep, txt)
    return txt


def postprocess_syllable_str(syllable_str: str) -> str:
    """
    Postprocess the syllable sequence (as a string), according to the rules in
    :obj:`cf.syllable_replacements`.

    Args:
        syllable_str (str): The input syllable string.

    Returns:
        str: The post-processed syllable string.
    """
    # Remove unwanted characters and format the syllable string
    pat2rep = ut.load_syllable_replacements_for_norm(cf)
    for pat, (rep, postpro_info) in pat2rep.items():
        # only apply postprocessing instructions if the pattern matches
        if re.search(pat, syllable_str):
            if postpro_info == "unstressed":
                syllable_str = syllable_str.lower()
        # apply the replacement (it's case insensitive so lowercasing above
        # does not affect the replacement)
        syllable_str = re.sub(pat, rep, syllable_str)

    return syllable_str


def apply_syllabification(line_list: list[str], stparse) -> tuple[list[tuple], list[str]]:
    """
    Apply :func:`g2s.silabeo` to a list of lines, syllabifying each word in the lines.
    
    Args:
        line_list (list[str]): A list of lines to syllabify.
        stparse (stanza.Pipeline): A Stanza POS tagger for disambiguating interrogative/relative words.
    
    Returns:
        tuple: A tuple containing two lists:
            - A list of tuples, containing syllabified words with stress marks, without,
              and the stressed syllable position.
            - A list of strings with the syllabified words without stress marks.
    """
    # load data for preprocessing (word or regex lists)
    hyphens_to_keep = ut.load_words_with_hyphen_to_keep(cf) # unused so far
    g2s_exceptions = ut.load_syllable_replacements_for_g2s(cf)
    out_lines = [] # syllabification after orthographic preprocessing
    out_lines_running_text = [] # orthographic preprocessing
    
    #poem_text = " ".join(line_list)
    do_postags = False
    ambiguity_type = None
    line_offsets = []
    total_amb = 0
    amb_not_found = 0
    amb_uniq_in_line = 0
    for line_idx, line in enumerate(line_list):
        #line_start = poem_text.find(line)        
        #line_offsets.append((line_start, line_start + len(line)))
        text = line.strip()
        # replacements that may affect a sequence of words
        text = re.sub(PUNCT_TO_SPACE_RE, " ", text)
        if args.preprocess:
            #text = preprocess_orthography(text, ignore_flagged=args.possessives)
            text = preprocess_orthography(text, ignore_flagged=not(args.possessives))
            text = re.sub(PUNCT_RE, r" \1 ", text)
        words = [tok for tok in re.split(r"\s+", text) if tok.strip() != ""]
        if bool(set(words) & set(ut.ambiguous_interr_rel)):
            # need to do pos-tagging
            do_postags = True
            ambiguity_type = "interr_rel"
        
            
        out_line = []
        out_line_running_text = []        

        # handle apostrophes

        #updated_words = copy.deepcopy(words)

        updated_words = []

        for widx, word in enumerate(words):
            has_apos = re.search(r"(\w+)['‘’](\w*)", word)
            if not has_apos:
                updated_words.append(word)
                continue

            word_orig = word
            base = has_apos.group(1)
            suffix = has_apos.group(2)

            split_parts = [base]
            if suffix:
                split_parts.append(suffix.strip())

            # Generate vowel edits for base
            edits_noapos = [base + v for v in ['a', 'e', 'o']]
            ed_scos = []

            # Prepare a simulated token list for context computation
            simulated_toklist = updated_words + [base] + words[widx + 1:]
            simulated_idx = len(updated_words)  # index where base would be inserted
            wlc, wrc = nglm.find_context_for_token(base, simulated_idx, simulated_toklist)

            for ed in edits_noapos:
                ed_sco = nglm.find_logprob_in_context(ed, (wlc, wrc))
                ed_scos.append((ed, ed_sco))

            best_ed_cand = sorted(ed_scos, key=lambda x: -x[1])

            if not best_ed_cand:
                updated_words.append(word_orig)
            else:
                new_word = best_ed_cand[0][0]
                updated_words.append(new_word)
                if len(split_parts) > 1:
                    updated_words.extend(split_parts[1:])

                logger.debug("Replace Apostrophe: [%s] to [%s]+[%s] context [%s]", word_orig, new_word, split_parts[1:] if len(split_parts) > 1 else '', ' '.join(updated_words))

        # handle other normalization cases than apostrophes
        for widx, word in enumerate(updated_words):
            if re.search(PUNCT_RE, word):
                out_line.append((word, word, word, -1))  # no syllabification
                out_line_running_text.append(re.sub(PUNCT_TO_SPACE_RE, " ", word).replace("-", ""))
                continue
            # remove punctuation (but hypen) from words
            word = re.sub(PUNCT_TO_SPACE_RE, " ", word)
            word = word.replace("-", "")
            if word.strip() == "":
                continue
            # check if needs diacritic stress
            #   if in list, line with unaccented and accented variants are scored
            #   with n-gram lm and the best is chosen
            if word in sti.diacritic_stress:
                updated_words = words[0:widx] + [word] + words[widx+1:]
                wlc, wrc = nglm.find_context_for_token(word, widx, updated_words)                                                       
                sco_unstressed = nglm.find_logprob_in_context(word, (wlc, wrc))
                sco_stressed = nglm.find_logprob_in_context(sti.diacritic_stress[word], (wlc, wrc))
                if sco_stressed > sco_unstressed:
                    word_orig = word
                    word = sti.diacritic_stress[word]
                    logger.debug("LM Dia Stress: [%s] to [%s] context [%s]", word_orig, sti.diacritic_stress[word_orig], " ".join(updated_words))
            # do token normalization before syllabification
            # normalizer is `nmlzr` instantiated in main block
            if args.normalize and word not in nmlzr.vocab:                
                # version of word with initial caps may be in vocabulary, neutralize
                if word.lower() not in nmlzr.vocab:
                    # test if exact match in Spanish (castellanismo)
                    if False and (word in nmlzr_es.vocab or word.lower() in nmlzr_es.vocab):
                        logger.debug("Accept castellanismo [%s]", word)
                    else:
                        wcands = nmlzr.collect_candidates(word)
                        best_cand = nmlzr.rank_candidates(word, updated_words, widx, wcands, nglm)
                        if best_cand is not None:
                            logger.debug("LM Ed Norm: [%s] to [%s]", word, best_cand.form)
                        else:
                            logger.debug("No Norm: [%s]", word)
                        word = best_cand.form if best_cand is not None else word
                        # respect case in orig text, using a case mask
                        case_mask_norm = nmlzr.create_case_mask(word)
                        word_cased = "".join([cha.upper() if cm == 1 else cha for (cha, cm) in zip(list(word), case_mask_norm)])
                        word = word_cased
            words_before_pos = copy.deepcopy(updated_words)
            
            # sylllabification only after preprocessing each line as above
            syllab_kwargs = {"exceptions": g2s_exceptions, "spanishfy": args.spanishfy, "debug_g2s": args.debug_g2s}
            syllables = g2s.syllabify_full(re.sub(PUNCT_TO_SPACE_RE, " ", word), **syllab_kwargs)
            syllables_orig = syllables
            # several representations of the syllabified word are stored,
            # along with the stressed syllable position
            syllables = [postprocess_syllable_str(syllables[0]), # stressed syllable in uppercase
                         postprocess_syllable_str(syllables[1]), # stressed syllable preceded by a diacritic
                         postprocess_syllable_str(syllables[2]), # no extra indication of stress
                         syllables[3]] # stressed syllable position

            # POSTROCESSING OF SYLLABIFICATION OUTPUT ============================================

            # I saw this exception
            if syllables[1] == "´─":
                syllables[1] = "─"

            # possessive preceded by article: remove stress mark from vowel but keep it on syllable
            if args.possessives_restore_vowel:
                if syllables[1] in ut.stressed_possessives:
                    for syll_idx in range(len(syllables)-2):
                        # removes vowel stress mark in formats at positions 0 through 2
                        word_no_syll_stress = syllables[syll_idx].replace("´", "")
                        syllables[syll_idx] = ut.destress_word(
                            syllables[syll_idx], case_mask=[1 if c.isupper() else 0 for c in word_no_syll_stress])
                        # in position 1, need to prefix back syllable stress mark
                        if syll_idx == 1:
                            syllables[syll_idx] = "´" + syllables[syll_idx]
                            syllables += [{"disambiguated": True}]

            # túa/súa ==============================================================================
            # can't be handled with other mechanisms because
            # stress mark will make g2s assign stress even when not tonic
            # so only keep stress if preceding word is article
            if syllables[1].lower() in ut.ambiguous_possessives: #("´tú-a", "´tú-as", "´sú-a", "´sú-as"):
                # See if preceding ([-1][-2]) is an article, destress otherwise
                if len(out_line) > 0 and out_line[-1][-2].lower() not in ut.possessive_disambiguators:
                    syllables[1] = syllables[1].replace("´", "")
                    # also destress the segment? problem if modifies preceding noun
                    #    ex. cántigas súas cheas de fogo, propios i estraños da beira súa
                    #syllables = [s.replace("ú", "u") if type(s) is str else s for s in syllables]
                    #syllables = [s.replace("Ú", "U") if type(s) is str else s for s in syllables]
                    syllables += [{"disambiguated": True}]
                    logger.debug("Removed túa/súa stress from [%s]", line_list[line_idx].strip())
            
            # túa/túa change onset so it stays unstressed for gumper (added changed-onset variant =====
            # to gumper unstressed list)
            # looks OK on dev but not on test, can deactivate
            if True and syllables[1].lower().replace("´", "") in ut.changed_onset_possessives:
                if len(out_line) == 0:
                    rewritten_form = ut.changed_onset_possessives[syllables[1].lower().replace("´", "")]
                    syllables[2] = rewritten_form
                    logger.debug("Changed onset for line-initial unstressed túa/súa [%s]", line_list[line_idx].strip())
                else:
                    field_to_check = -2 if type(out_line[-1][-2]) is str else -3
                    if out_line[-1][field_to_check].lower() not in ut.possessive_disambiguators:
                        rewritten_form = ut.changed_onset_possessives[syllables[1].lower().replace("´", "")]
                        syllables[2] = rewritten_form
                        logger.debug("Changed onset for unstressed túa/súa [%s]", line_list[line_idx].strip())

            # 'miña' case =============================================================================
            # add stress if preceded by article
            if len(out_line) > 0 and syllables[1].lower() in ut.possessives_to_stress:
                field_to_check = -2 if type(out_line[-1][-2]) is str else -3
                # See if preceding ([-1][-2]) is an article, destress otherwise
                if len(out_line) > 0 and out_line[-1][field_to_check].lower() in ut.possessive_disambiguators:
                    # stress the segment based on ut
                    syllables[1] = ut.possessives_to_stress[syllables[1]]
                    syllables[2] = syllables[1].replace("-", "").replace('´', '')
                    syllables += [{"disambiguated": True}]
                    logger.debug("Added possessive stress in [%s]", line_list[line_idx].strip())
            
            # pola contra =============================================================================
            if syllables[2].lower() == "con-tra":
                if len(out_line) > 0:
                    field_to_check = -2 if type(out_line[-1][-2]) is str else -3
                else:
                    field_to_check = None
                if len(out_line) > 0 and out_line[-1][field_to_check].lower() in ("po-la", "pó-la"):
                    syllables[1] = "´" + syllables[1] if not syllables[1].startswith("´") else syllables[1]
                    # Add the lexical stress mark so downstream processing keeps it tonic.
                    syllables = [s.replace("o", "ó") if type(s) is str else s for s in syllables]
                    syllables = [s.replace("O", "Ó") if type(s) is str else s for s in syllables]
                    syllables += [{"disambiguated": True}]
                    logger.debug("Added stress to contra after pola [%s]", line_list[line_idx].strip())

            # un/cun/dun/nun/e logo ==============================================================
            if syllables[2].lower() == "lo-go":
                if len(out_line) > 0:
                    field_to_check = -2 if type(out_line[-1][-2]) is str else -3
                else:
                    field_to_check = None
                if len(out_line) > 0 and out_line[-1][field_to_check].lower() in ("un", "cun", "dun", "nun", "e"):
                    syllables[0] = "LÓ-go"
                    syllables[1] = "´ló-go"
                    syllables[2] = "ló-go"
                    syllables += [{"disambiguated": True}]
                    logger.debug("Added stress to logo after un/cun/dun/nun/e [%s]", line_list[line_idx].strip())

            # pra/para si ========================================================================
            if syllables[2].lower() == "si":
                if len(out_line) > 0:
                    field_to_check = -2 if type(out_line[-1][-2]) is str else -3
                else:
                    field_to_check = None
                if len(out_line) > 0 and out_line[-1][field_to_check].lower() in ("pra", "pa-ra"):
                    syllables[0] = "SÍ"
                    syllables[1] = "´sí"
                    syllables[2] = "sí"
                    syllables += [{"disambiguated": True}]
                    logger.debug("Added stress to si after pra/para [%s]", line_list[line_idx].strip())

            # ambiguous tonicity words that can only be stressed at line end -> add stress
            remaining_lexical_words = [
                tok for tok in updated_words[widx+1:]
                if not re.search(PUNCT_RE, tok)
                and re.sub(PUNCT_TO_SPACE_RE, " ", tok).replace("-", "").strip() != ""
            ]
            if (not remaining_lexical_words
                    and syllables[2].lower().replace("-", "") in ut.stressed_at_line_end):
                stressed_form = ut.stressed_at_line_end_syll.get(syllables[1].lower().lstrip("´"))
                if stressed_form is not None:
                    stressed_plain = stressed_form.replace("´", "")
                    if "-" in stressed_plain:
                        stressed_syll, unstressed_rest = stressed_plain.split("-", 1)
                        syllables[0] = stressed_syll.upper() + "-" + unstressed_rest
                    else:
                        syllables[0] = stressed_plain.upper()
                    syllables[1] = stressed_form
                    syllables[2] = stressed_plain
                    syllables += [{"disambiguated": True}]
                    logger.debug("Added line-end stress to [%s] in [%s]", stressed_plain, line_list[line_idx].strip())
            

            # "cabo" unstressed if "cabo de" but not "a?o cabo de" (accents for 19th stuff) ==========
            if syllables[1].lower() == "´ca-bo":
                if len(out_line) > 0 and out_line[-1][-2].lower() not in ("o", "ó", "ô", "ò", "ao"):
                    cabo_start = line.find("cabo")
                    if re.match(r"de|d[oa]s?", line[cabo_start+5:]):
                        syllables[1] = syllables[1][1:]
                        syllables[0] = syllables[0].lower()
                        syllables += [{"disambiguated": True}]
                    logger.debug("Removed cabo stress from [%s]", line.strip())

            # form of address (so far found 'dona') ===============================================
            if syllables[1].lower() in ut.ambiguous_address: 
                # Stress if preceding ([-1][-2]) is a disambiguator
                field_to_check = -2 if type(out_line[-1][-2]) is str else -3
                if len(out_line) > 0 and out_line[-1][field_to_check].lower() in ut.address_disambiguators:
                    syllables[1] = "´" + syllables[1] if not syllables[1].startswith("´") else syllables[1]
                    # add stress also on the segment for gumper to not consider it unstressed
                    syllables = [s.replace("o", "ó") if type(s) is str else s for s in syllables]
                    syllables = [s.replace("O", "Ó") if type(s) is str else s for s in syllables]
                    syllables += [{"disambiguated": True}]
                    logger.debug("Added stress to dona [%s]", line_list[line_idx].strip())


            # Interrogative / relative desambiguation with POS tags ===============================
            if do_postags:
                interr_desambiguated = False
                text_to_postag = line_list[line_idx-1] + " " + line_list[line_idx] +  " " + line_list[line_idx+1] if line_idx > 0 and line_idx < len(line_list)-1 else line_list[line_idx]
                tagger_output = stparse(text_to_postag)

                if syllables[2].lower() in ut.ambiguous_interr_rel:
                    total_amb += 1

                    # `amb_word` is a str, surface form of ambig word in running text
                    amb_word = syllables[2].lower().replace("-", "")
                    
                    # assume stressed if line-initial following question/exclam mark
                    # "por que" also safe as stressed (unstressed one is "porque")
                    # gives erros with wishes (¡Que ....!) but not with actual interrogative/exclamative
                    # if line.startswith(("¿", "¡")):
                    #     breakpoint()
                    if re.match(r"^[¿¡]( ?[Pp]or )?", line.strip()):
                        # look for amb_word and its version with apostrophe
                        if (line.strip().lower().find(amb_word) in (1, 5, 6) or line.strip().lower().find(amb_word[:-1]+"'") in (1, 5, 6)):
                            if '´' not in syllables[1]:
                                syllables[1] = '´' + syllables[1]
                                syllables[0] = syllables[0].upper()
                                # `destress_function` below checks for this to not destress if set
                                syllables += [{"desambiguated": True}]
                                logger.debug("Line-initial amb interr/rel after ¿/¡: added stress to [%s] in line: [%s]" % (syllables[2], line.strip()))
                            interr_desambiguated = True
                    
                    # pos-tagging required for other cases
                    if not interr_desambiguated:
                        amb_start = tagger_output.text.lower().find(amb_word)
                        amb_word_stok_l = []
    
                        # if ambiguous word only once in line, take it
                        if text_to_postag.lower().count(amb_word) == 1:
                            amb_uniq_in_line += 1
    
                            # specific for 'cal' misanalyzed as multiword
                            use_tokobjs_cal = False  
                            amb_word_stok_l_cal = []
    
                            # collect pos-tagger tokens: general case
                            amb_word_stok_l = [stok for stok in tagger_output.iter_words() if stok.text.lower() == amb_word.lower()]
    
                            # collect pos-tagger tokens, special case: 'cal' misanalyzed as mw 'ca ll'
                            if not amb_word_stok_l:
                                use_tokobjs_cal = True
                                amb_word_stok_l_cal = [stok for stok in tagger_output.iter_tokens() if
                                                   stok.text.lower() in ("cal", "´cal")]
                            
                            # treat 'cal'
                            if syllables[2].lower() in ("cal", "´cal") and (amb_word_stok_l or amb_word_stok_l_cal):
                                obj_list_for_cal = amb_word_stok_l if amb_word_stok_l else amb_word_stok_l_cal
                                logger.debug("Unique amb 'cal' found at idx [%s] in line: [%s]", obj_list_for_cal[0].start_char, line.strip())
                                # use Word or Token objects depending on how 'cal' was analyzed
                                all_words_as_list = all_toks_as_list = None
                                if not use_tokobjs_cal:
                                    all_words_as_list = list(tagger_output.iter_words())
                                else:
                                    all_toks_as_list = list(tagger_output.iter_tokens())
                                if not use_tokobjs_cal:
                                    cal_stress = ut.desambiguate_cal_word(obj_list_for_cal[0], all_words_as_list)
                                else:
                                    cal_stress = ut.desambiguate_cal_tok(obj_list_for_cal[0], all_toks_as_list)
                                if cal_stress == "unstressed":
                                    if syllables[1].startswith("´"):
                                        syllables[1] = syllables[1][1:]
                                        syllables[0] = syllables[0].lower()
                                        interr_desambiguated = True

                        # If ambiguous rel/interr more than once in line , look for matching indices.

                        # Only work with Word objs (stanza.models.common.doc.Word), not doing
                        # the special case for 'cal' here with doc.Token objs.
                        # I called variables below *tok*_infos etc. but it's Word objs.
                        elif text_to_postag.lower().count(amb_word) > 1:
                            tok_infos = [stok for stok in tagger_output.iter_words()]
                            amb_word_stok_l = [stok for stok in tok_infos if stok.text.lower() == amb_word and stok.start_char >= amb_start]

                        # Stress disambiguation once amb_word_stok_l is ready
                        if amb_word_stok_l and not interr_desambiguated:
                            amb_word_stok = amb_word_stok_l[0]
                            logger.debug("Amb word:[%s] PoS: [%s] Feats [%s] Line: [%s]", amb_word, amb_word_stok.upos, amb_word_stok.feats, line.strip())
                            if (amb_word_stok.feats is not None and "Type=Int" not in amb_word_stok.feats and "¿" not in amb_word_stok.upos):
                                # remove stress if not interrogative
                                for syll_idx in range(len(syllables)-2):
                                    word_no_syll_stress = syllables[syll_idx].replace("´", "")
                                    syllables[syll_idx] = ut.destress_word_simple(syllables[syll_idx], case_mask=[1 if c.isupper() else 0 for c in word_no_syll_stress])
                                logger.debug("    Not Interr/Rel: Removed stress/Left unst on [%s] with PoS [%s] and Feats [%s]\n    Line: [%s]", syllables[2], amb_word_stok.upos, amb_word_stok.feats, line.strip())
                            elif (amb_word_stok.feats is not None and ("Type=Int" in amb_word_stok.feats or "¿" in amb_word_stok.upos)):
                                # It is interrogative: add stress if not present
                                logger.debug("Interr/Rel kept/added stress on [%s] with PoS [%s] and Feats [%s]", syllables[2], amb_word_stok.upos, amb_word_stok.feats)
                                if '´' not in syllables[1]:
                                    syllables[1] = '´' + syllables[1]
                                    syllables[0] = syllables[0].upper()
                                    syllables += [{"desambiguated": True}]
                            else:
                                logger.debug("    No stress update on amb [%s] with PoS [%s] and Feats [%s]", syllables[2], amb_word_stok.upos, amb_word_stok.feats)
                        else:
                            amb_not_found += 1
                            # Note: most of these are fine to ignore, they come from contractions,
                            # so they are unstressed (as they can be contracted; they tend to be enclitic prosodically)
                            logger.debug("    No Stanza token found for amb [%s]\n    Line: [%s]", syllables[2], line.strip())

            # END OF SYLLABLE POSTPROCESSING ===========================================================================

            # store output
            out_line.append(syllables)
            out_line_running_text.append(syllables[2].replace("-", ""))
        if len(out_line) > 0:
            out_lines.append(out_line)
        if len(out_line_running_text) > 0:
            out_lines_running_text.append(out_line_running_text)

    logger.debug("Total amb not found (index mismatch) [%s] of [%s]", amb_not_found, total_amb)
    logger.debug("Total amb unique in line [%s] of [%s]", amb_uniq_in_line, total_amb)

    return out_lines, out_lines_running_text


if __name__ == "__main__":
    reload(cf)
    reload(g2s)
    reload(ncf)
    reload(normalizer)
    reload(sti)
    reload(ut)
    
    log_prod = True
    log_level = logging.ERROR if log_prod else logging.DEBUG

    args = parse_args()
    input_file = args.input_file
    
    # prepare logging
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logger = logging.getLogger("main")
    logging.getLogger("stanza").setLevel(logging.ERROR)
    logger.handlers.clear()
    logger.setLevel(log_level)
    lfh = logging.FileHandler(Path(cf.log_dir) / cf.log_fn_template.format(batch_id=str.zfill(args.batch_id, 3), mode="w"))
    lch  = logging.StreamHandler(sys.stdout)
    lfh.setLevel(log_level)
    lch.setLevel(log_level)
    log_format_file = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log_format_console = logging.Formatter('%(message)s')
    lfh.setFormatter(log_format_file)
    lch.setFormatter(log_format_console)
    logger.addHandler(lfh)
    logger.addHandler(lch)

    start_time = time.time()
    print("- Start: ", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

    with open(input_file, "r", encoding="utf8") as f:
        lines_to_syllabify = f.readlines()
    
    # Prepare normalization, called by apply_syllabification
    if args.normalize:
        nmlzr = normalizer.Normalizer(ncf)
        nmlzr_es = normalizer.Normalizer(ncf, lang="es")
        #pos_tagger = 
    else:
        print("Normalization off, run with --normalize to enable.")
    
    # Prepare n-gram language model
    nglm = lmg.KenLMManager()

    # Stanza
    nlp = stanza.Pipeline(lang="gl", package="treegal", processors="tokenize,pos",
                          download_method=DownloadMethod.REUSE_RESOURCES)


    # Syllabification
    out_lines, out_lines_running_text = apply_syllabification(lines_to_syllabify, nlp)
    
    # Destressing
    if args.destress:
        destress_function = ut.destress_word_simple
        # destress in running text
        out_lines_running_text_destressed = copy.deepcopy(out_lines_running_text)
        for lidx, olrt in enumerate(out_lines_running_text):
            for widx, syll_info in enumerate(olrt):
                if syll_info.lower() in sti.atonas_gl:
                    # if the word is unstressed, remove the stress mark
                    out_lines_running_text_destressed[lidx][widx] = destress_function(syll_info.replace("´", ""))
        # destress in syllabified output
        # create a mutable copy of the syllabified output
        out_lines_destressed = []
        for ol in out_lines:
            out_lines_destressed.append(list(ol))
        for slidx, ol in enumerate(out_lines):
            for swidx, syll_info in enumerate(ol):
                # only destress if not disambiguated
                if isinstance(syll_info[-1], dict) and syll_info[-1].get("desambiguated", False):
                    logger.debug("Skipping destress for disambiguated word [%s], line [%s]", syll_info[2], ol)
                    continue
                if syll_info[2].lower().replace("-", "") in sti.atonas_gl:
                    case_mask = [1 if char.isupper() else 0 for char in syll_info[2]]
                    # if the word is unstressed, remove the stress mark
                    out_lines_destressed[slidx][swidx] = (destress_function(syll_info[0], case_mask),
                                                          destress_function(syll_info[1], case_mask),
                                                          destress_function(syll_info[2], case_mask),
                                                          syll_info[3])

    # Outputs
    out_batch_id = f"_{str.zfill(args.batch_id, 3)}" if args.batch_id else ""
    out_dir_id = f"out{out_batch_id}"
    if not Path(input_file.parent / out_dir_id).exists():
        Path(input_file.parent / out_dir_id).mkdir(parents=True)

    #   Syllabification
    infix_pp_syll = "_pp_syll_out" if args.preprocess else "_syll_out"
    infix_pp_syll += out_batch_id
    output_file_syll = input_file.parent / out_dir_id / Path(input_file.stem + infix_pp_syll + input_file.suffix)
    with output_file_syll.open(mode="w", encoding="utf8") as outf:
        for line in out_lines:
            syll_index_to_output = 0 if args.stress_marks == "allupper" else 1
            outf.write(" ".join([tu[syll_index_to_output] for tu in line]) + "\n")

    #   Running text (always preprocessed)
    if args.preprocess and not args.normalize:
        infix_pp_text = "_pp_out"
        infix_pp_text += out_batch_id
        output_file_pp = input_file.parent / out_dir_id / Path(input_file.stem + infix_pp_text + input_file.suffix)
        with output_file_pp.open(mode="w", encoding="utf8") as outf:
            for line in out_lines_running_text:
                outf.write(ut.detokenize(line) + "\n")
    
    #   Destressed syllabification
    if args.destress and not args.normalize:
        infix_destressed = "_pp_syll_out_des" if args.preprocess else "_syll_out_des"
        infix_destressed += out_batch_id
        output_file_syll_destressed = input_file.parent / out_dir_id / Path(input_file.stem + infix_destressed + input_file.suffix)
        with output_file_syll_destressed.open(mode="w", encoding="utf8") as outf:
            for line in out_lines_destressed:
                syll_index_to_output = 0 if args.stress_marks == "allupper" else 1
                outf.write(" ".join([tu[syll_index_to_output] for tu in line]) + "\n")

    #   Destressed running text
    if args.destress and args.preprocess and not args.normalize:
        infix_pp_destressed = "_pp_out_des"
        infix_pp_destressed += out_batch_id
        output_file_pp_destressed = input_file.parent / out_dir_id / Path(input_file.stem + infix_pp_destressed + input_file.suffix)
        with output_file_pp_destressed.open(mode="w", encoding="utf8") as outf:
            for line in out_lines_running_text_destressed:
                outf.write(ut.detokenize(line) + "\n")
    
    #   With normalization
    #      Syllabification
    #         This normalized but not destressed output is only for debugging (you need destressed output for Gumper)
    if args.normalize and not args.destress:
        infix_syll_norm = "_pp_syll_out_norm" if args.preprocess else "_syll_out_norm"
        if args.spanishfy:
            infix_syll_norm += "_spa"
        infix_syll_norm += out_batch_id
        output_file_syll_destressed = input_file.parent / out_dir_id / Path(input_file.stem + infix_syll_norm + input_file.suffix)
        with output_file_syll_destressed.open(mode="w", encoding="utf8") as outf:
            for line in out_lines:
                syll_index_to_output = 0 if args.stress_marks == "allupper" else 1
                outf.write(" ".join([tu[syll_index_to_output] for tu in line]) + "\n")
    if args.destress and args.normalize:
        infix_destressed = "_pp_syll_out_des_norm" if args.preprocess else "_syll_out_des_norm"
        if args.spanishfy:
            infix_destressed += "_spa"
        infix_destressed += out_batch_id
        output_file_syll_destressed = input_file.parent / out_dir_id / Path(input_file.stem + infix_destressed + input_file.suffix)
        with output_file_syll_destressed.open(mode="w", encoding="utf8") as outf:
            for line in out_lines_destressed:
                syll_index_to_output = 0 if args.stress_marks == "allupper" else 1
                outf.write(" ".join([tu[syll_index_to_output] for tu in line]) + "\n")
    #      Running text: Forgetting about "des" infix cos for running text it's the same as "pp_out"
    if args.destress and args.preprocess and args.normalize:
        infix_pp_destressed = "_pp_out_norm"
        if args.spanishfy:
            infix_pp_destressed += "_spa"
        infix_pp_destressed += out_batch_id
        output_file_pp_destressed = input_file.parent / out_dir_id / Path(input_file.stem + infix_pp_destressed + input_file.suffix)
        with output_file_pp_destressed.open(mode="w", encoding="utf8") as outf:
            for line in out_lines_running_text_destressed:
                #outf.write(" ".join(line) + "\n")
                outf.write(ut.detokenize(line) + "\n")
    print("- End: ", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
    total_min, total_secs = divmod(time.time() - start_time, 60)
    #print(f"- Duration:  {total_min} m {total_secs:.2f} s")
    logger.info(f"- Duration:  {total_min}m {total_secs:.2f}s")
    
    if args.batch_comment:
        batch_info_path = Path(input_file.parent) / cf.batch_cumulog 
        with open(batch_info_path, mode="a") as batch_info:
            if batch_info_path.exists() and batch_info_path.stat().st_size == 0:
                batch_info.write(f"Batch ID\tComment\n")
            batch_info.write(f"{args.batch_id}\t{args.batch_comment}\n")
