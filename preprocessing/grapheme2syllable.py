"""
Simple hyphenation of Galician words, based on onset maximization.
It does not handle foreign prefixes, e.g. pa-ra-psi-co-lo-xía is 
hyphenated as in cáp-su-la.

Added fixes: Resyllabification for some issues found when processing.

Copyright (C) 2007  Rafael C. Carrasco for the initial Java implementation,
see https://www.dlsi.ua.es/%7Ecarrasco/progs/Hyphenator.java
This program is free software; you can redistribute it and/or
modify it under the terms of   the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.
Adapted from José A. Mañas in Communications of the ACM 30(7), 1987.

Initial Python implementation (for Spanish) by Javier Sober
Current modifications by Pablo Ruiz.
"""


from copy import copy
from collections import OrderedDict
import logging
import re
import utils as ut

DBG = True

g2s_logger = logging.getLogger("main.g2s")
#g2s_logger.setLevel(logging.DEBUG)

V = "[aáeéiíoóuúüôâèëï]"         # vowels
A = "[aáeéíoóú]"                # open vowels and accented closed
I = "[iuü]"                     # closed unaccented vowels and u-dieresis
C = "[bcdfghjklmnñpqrstvxyzç]"  # consonants
B = "[bcdfgjkmnñpqstvxyz]"      # non-liquid consonants
R = "[hlr]"                     # liquid and mute consonants

# patterns for syllabification
PATS = []
PATS.append("(_)")                              # 1: exceptions: resyllabification mark
PATS.append("(" + I + "h" + I + ")")            # 2: ~ closed vowel diphthong (gives errors with í)
PATS.append("(" + A + "h" + I + ")")            # 3: ~ current char starts falling diphthong
PATS.append("(" + I + "h" + A + ")")            # 4: ~ current char starts rising diphthong
PATS.append("(" + "." + C + R + V + ")")        # 5: syl that starts with CRV follows current character (errors in RRV 
PATS.append("(" + C + R + V + ")")              # 6: current char starts CRV
PATS.append("(" + "." + C + V + ")")            # 7: syl that starts with CV follows current character
PATS.append("(" + A + A + ")")                  # 8: split vowel seqs that are not diphthong
PATS.append("(" + "." + ")")

# main regex combining all patterns
ALLPATS = PATS[0] + "|" + PATS[1] + "|" + PATS[2] + "|" + PATS[3] + "|" + PATS[4] + "|" + PATS[5] + "|" + PATS[6] + "|" + PATS[7] + "|" + PATS[8]
SCN_RE = re.compile(ALLPATS, re.I | re.U)

# In Galician, falling diphthongs do not get a stress mark in a stressed final syllable, list them here
UNACCENTED_DIPHTHONGS_GL = {"ai", "au", "ei", "ey", "eu", "oi", "ou"}

# initial syllabification regexes
CLSQ_RE = re.compile(r"^(.*?([iu]))(\2.*?)$", re.I)
UNST_RE = re.compile(r"(([aeiou])|(n)|([aeiou]s))\Z", re.I|re.U)
STV_RE = re.compile(r"[áéíóúôâè]", re.I|re.U)

# regexes for resyllabification
CCO_RE = re.compile(r"([^\n]*(?<![qg])[iu])([iu])([aeo][^\n]*)$", re.I)         # close close open
COC_RE = re.compile(r"([^\n]*(?<![qg])[iu])([aeo])([iyu][^\n]*)$", re.I)        # close open close (y in criey)
HOMDI_RE = re.compile(r"^(.*?[^gq])([iu])([íú])(.*?)$")                                # homogeneous diphthong with stress mark
DIE_RE = re.compile(r"^([^ïëqg]*[ui]{1,2})([ïë])([^ïë]*)$", re.I)               # dieresis vowels
DIEQ_RE = re.compile(r"^([qg]*[ui]{1,2})([ïë])([^ïë]*)$", re.I)                 # [qg] + dieresis vowels
DIEGRL_RE = re.compile(r"^([^ïëqg]{1,3})([ïë])([aeiou]{1}[^\n]{0,2})$", re.I)   # [qg] + dieresis vowels
STLI_RE = re.compile(r"^[pbftdkcg]$", re.I)                                     # for stop-liquid (see uses)
SL_RE = re.compile(r"^(s)(l)", re.I)                                          # syllable starts with 'sl 
SSG_RE = re.compile(r"([^\n]*[aeo])([i])([aeoáéó][^\n]*)$", re.I)       # sonority sequence bad in vowels
SGL_RE = re.compile(r"^l$", re.I)                                       # single l
LSY_RE = re.compile(r"^(?:lr|rl|nr|nl)", re.I)                          # liquids in same syllable
DIG_RE = re.compile(r"^[cn]$", re.I)                                    # to unsplit 'ch' and 'nh'
APQ_RE = re.compile(r"^[’‘]s$", re.I)                                   # rsquo|lsquo s


def get_matching_pat(pat_nbr: int) -> str:
    """
    Returns the separator (dash) if the matching pattern in `ALLPATS` above
    corresponds to certain patterns. Basically maximizes onsets.
    """
    switcher = {
        1: '-',
        5: '-',
        7: '-',
        8: '-',
    }
    return switcher.get(pat_nbr, "")


def syllabify_core(input: str)-> str:
    """
    Syllabifies a word based on regex patterns. At each character position,
    the patterns in `ALLPATS` are matched against the remaining part of the word,
    assigned to `input`. If certain patterns match, it is considered that what follows
    the current character is the beginning of a new syllable, so a dash is added to the
    `output` part, which contains the syllabified word being built.
    
    Args:
        input (str): The word to be syllabified.
    
    Returns:
        str: The syllabified word with dashes between syllables.
    """
    output = ""
    while len(input) > 0:
        DBG and print(f"0 output: {output} input: {input}")

        output += input[0]

        DBG and print(f"1 output: {output} input: {input}")

        m = SCN_RE.match(input)
        
        # remove resyllabification marks when found
        if m.lastindex == 1:
            # output is part already syllabified (input is part to do)
            # do not remove the mark at beginning of input cos we slice [1:] below
            output = re.sub("_$", "", output)

        output += get_matching_pat(m.lastindex)   # `lastindex` is int index of last matched group

        DBG and print(f"2 output: {output} input: {input} idx [{m.lastindex}]")

        input = input[1:]

        DBG and print(f"3 output: {output} input: {input}\n")
    DBG and print(f"F output {output} input: {input}")
    return output


def search_stress_mark(silabas: list) -> int:
    """
    Given a list of strings where each string represents a syllable,
    return position in the list of a syllable bearing orthographic stress
    (the one with the acute accent mark in Galician or Spanish)

    Args:
        silabas (list): list of syllables as str
    Returns:
        int: position of the syllable with orthographic stress or -1 if none found
    """
    for idx, syl in enumerate(silabas):
        if STV_RE.search(syl):
            return idx
    return -1


def search_stressed_syll(silabas: list) -> bool:
    """
    The patterns in `unstressed_re` are searched in the last member of a list
    of strings each of which represents a syllable. If it matches, it means that
    the word has antepenult stress, because words whose final syllable matches
    the pattern do not have final stress, and antepenult or earlier stress are
    already detected by :func:`search_stress_mark`.
    
    Args:
        silabas (list): list of syllables as str

    Returns:
        bool: True if the last syllable matches the unstressed pattern (i.e.
              word has penult stress), False otherwise
    """    
    if UNST_RE.search(silabas[-1]):
        return True
    else:
        return False


def _has_unaccented_diphthong(syll: str) -> bool:
    """
    Check whether a falling diphthong (without a stress mark) is in the final syllable.

    Args:
        syll (str): The syllable to check.

    Returns:
        bool: True if the syllable contains an unaccented diphthong, False otherwise.
    """
    for di in UNACCENTED_DIPHTHONGS_GL:
        if di in syll.lower():
            return True
    return False


def mark_stress(sylls: list[str], diacritic: str = "´", spanishfy: bool = False) -> tuple[str, str, str, int]:
    """
    Given a list of syllables for a word, marks the stressed syllable position
    in several ways.
    
    Args:
        sylls (list[str]): List of syllables as strings.
        diacritic (str): Diacritic to prefix the stressed syllable in the output.
            Default is "´" (acute accent).
        spanishfy (bool): If True, adds a stress mark to final syllables with a falling
            diphthong. These bear no stress mark in Galician, but in Spanish they do. Since
            some of our tools are meant for Spanish, this option is useful
    
    Returns:
        tuple: The first member contains the stressed syllable in allcaps,
               the second has the stressed syllable prefixed with a diacritic,
               the third one is the original syllabification without extra stress marks,
               the last one is the position of the stressed syllable, indexed from the end of the word
    """
    orig_syll = copy(sylls)
    # in `sylls_diac` the stressed syllable will be marked with the value of `diacritic`
    sylls_diac = copy(sylls)
    stressposi = None
    if len(sylls) == 1:
        sylls[0] = sylls[0].upper()
        sylls_diac[0] = diacritic + sylls_diac[0]
        stressposi = 0
    else:
        last = len(sylls) - 1
        penult = len(sylls) - 2
        stress_mark = search_stress_mark(sylls)
        stressposi = stress_mark
        if stress_mark != -1:
            sylls[stress_mark] = sylls[stress_mark].upper()
            sylls_diac[stress_mark] = diacritic + sylls_diac[stress_mark]
        # exception for Galician's falling diphthongs
        # (get no stress mark in final stressed syllable)
        elif _has_unaccented_diphthong(sylls[-1]):
            if spanishfy and len(sylls) > 1:
                sylls[last] = ut._spanishfy(sylls[last], sylls)
                sylls_diac = copy(sylls) # to update after spanishfy
            sylls[last] = sylls[last].upper()
            sylls_diac[last] = diacritic + sylls_diac[last]
            stressposi = len(sylls) - 1
        elif search_stressed_syll(sylls):
            sylls[penult] = sylls[penult].upper()
            sylls_diac[penult] = diacritic + sylls_diac[penult]
            stressposi = len(sylls) - 2
        else:
            sylls[last] = sylls[last].upper()
            sylls_diac[last] = diacritic + sylls_diac[last]
            stressposi = len(sylls) - 1

    # normalize stressed position to a negative index
    # (since we speak of final, penult, or antepenult etc. stress)
    stressposi = 0 - (len(sylls) - stressposi)

    word = "-".join(x for x in sylls)
    word_diac = "-".join(x for x in sylls_diac)
    orig_word = "-".join(x for x in sylls_diac).replace(diacritic, "")
    return word, word_diac, orig_word, stressposi


def _resyllabify_close_sequence(sl: list) -> list:
    """
    Makes sure that sequences like uu, ii, UU, II are be syllabified
    in two different syllables.
    
    Args:
        sl (list): list of syllables as strings
    
    Returns:
        list: a copy of the syllable list with close vowerl sequences
              resyllabified correctly
    """
    for idx, sy in enumerate(sl):
        symatch = re.match(CLSQ_RE, sy)
        if symatch:
            sl[idx] = symatch.group(1)
            sl.insert(idx+1, symatch.group(3))
    return sl


def _resyllabify_homogeneous_diphthong_(sl: list)-> list:
    """
    Resyllabifies  closed vowels the second of which bears a stress mark
    (e.g. Galician "muíño" goes to "mu-í-ño")
    
    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list with homogeneous diphthongs
              resyllabified correctly
    """
    for idx, sy in enumerate(sl):
        symatch = re.match(HOMDI_RE, sy)
        if symatch:
            # print sl, sy
            sl[idx] = symatch.group(1) + symatch.group(2)
            sl.insert(idx+1, symatch.group(3))
            if symatch.group(4):
                sl.insert(idx+2, symatch.group(4))
    return sl


def _resyllabify_dieresis_ui(sl: list) -> list:
    """
    Makes sure that ï, ë go in a syllable of their own.
    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list after resyllabifying dieresis vowels
    """
    for idx, sy in enumerate(sl):
        if "ï" not in sy and "ë" not in sy:
            continue
        symatch = re.match(DIE_RE, sy)
        if symatch:
            # print sl, sy
            sl[idx] = symatch.group(1)
            sl.insert(idx + 1, symatch.group(2))
            if symatch.group(3):
                # ru-ï-do but ru-ïn
                if symatch.group(3) in C + R:
                    sl[idx+1] += symatch.group(3)
                else:
                    sl.insert(idx + 2, symatch.group(3))
    return sl


def _resyllabify_dieresis_qui(sl: list) -> list:
    """
    Makes sure that sequences like quëV, guëV are split after the dieresis vowel.
    Examples: boquëadas -> bo-quë-a-das, leave lingüística -> lin-güís-ti-ca.

    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list after resyllabifying dieresis vowels
    """
    for idx, sy in enumerate(sl):
        if "ï" not in sy and "ë" not in sy:
            continue
        symatch = re.match(DIEQ_RE, sy)
        if symatch:
            # print sl, sy
            sl[idx] = symatch.group(1) + symatch.group(2)
            sl.insert(idx + 1, symatch.group(3))
    return sl


def _resyllabify_dieresis_general(sl: list) -> list:
    """
    Split a syllable at the dieresis vowel if another vowel follows. Needs
    to be applied after other dieresis rules. 

    Examples: vichëis -> vi-chë-is,

    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list after resyllabifying.
    """
    for idx, sy in enumerate(sl):
        if "ï" not in sy and "ë" not in sy:
            continue
        symatch = re.match(DIEGRL_RE, sy)
        if symatch:
            # print sl, sy
            sl[idx] = symatch.group(1) +symatch.group(2)
            sl.insert(idx + 1, symatch.group(3))
    return sl


def _resyllabify_stop_liquid(sl: list) -> list:
    """
    Obstruent-liquid onsets were sometimes syllabified wrongly when applied
    `syllabify_core` to a large corpus. This is fixed here.
    
    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list with obstruent-liquid onsets
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            if (re.match(STLI_RE, sy)
                and sl[idx+1][0].lower() in {"l", "r"}):
                sl_copy[idx+1] = "".join((sl[idx][-1], sl[idx+1]))
                del sl_copy[idx]
        except IndexError:
            pass
    return sl_copy

def _resyllabify_s_liquid(sl: list) -> list:
    """
    For cases of missyllabification as a syllable starting with 'sl', the 
    's' should be a coda for preceding syllable if any.
    Example: de-slum-brar -> des-lum-brar

    Args:
        sl (list): list of syllables as strings
    Returns:
        list: updated syllable list with 'sl' resyllabified
    """
    for idx, sy in enumerate(sl):
        starts_sl = re.match(SL_RE, sy) 
        if starts_sl and idx > 0:
            sl[idx - 1] += starts_sl.group(1)
            sl[idx] = sl[idx][1:]
    return sl


def _resyllabify_double_l(sl: list) -> list:
    """
    The "ll" digraph for the lateral palatal were sometimes syllabified
    into two syllables when applied `syllabify_core` to a large corpus.
    This is fixed here, adding it as onset to the second one.

    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list with obstruent-liquid onsets
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            # I'm not sure why did it this way (back in 2017). 
            # From the rgx, what seems to be happening is that 
            # the first "syllable" is just a single "l", perhaps
            # there were missyllabifications with such (incorrect) "syllables"
            # and this function was meant to fix them.
            if (re.match(SGL_RE, sy)
                and sl[idx+1][0].lower() == "l"):
                sl_copy[idx+1] = "".join((sl[idx][-1], sl[idx+1]))
                del sl_copy[idx]
        except IndexError:
            pass
    return sl_copy


def _resyllabify_liquids(sl: list) -> list:
    """
    This fixes cases where words like "burla" or "bulra" are 
    syllabified as "bu-rla" and "bu-lra" instead of "bur-la" and "bul-ra".

    Args:
        sl (list): list of syllables as strings

    Returns:
        list: a copy of the syllable list with the liquids
              resyllabified correctly
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            if re.search(LSY_RE, sy):
                sl_copy[idx] = sy[1:]
                sl_copy[idx-1] = sl_copy[idx-1] + sy[0]
        except IndexError:
            pass
    return sl_copy


def _resyllabify_digraph(sl: list) -> list:
    """
    The "ch" digraph for the postalveolar affricate was sometimes syllabified
    into two syllables when applied `syllabify_core` to a large corpus.
    The "nh" digraph for the palatal nasal too.
    This is fixed here, adding it as onset to the second one.

    Args:
        sl (list): list of syllables as strings
    Returns:
        list: a copy of the syllable list with digraphs resyllabified
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            if (re.match(DIG_RE, sy)
                and sl[idx+1][0].lower() == "h"):
                sl_copy[idx+1] = "".join((sl[idx][-1], sl[idx+1]))
                del sl_copy[idx]
        except IndexError:
            pass
    return sl_copy


def _e_apheresis(sl: list) -> list:
    """
    Resyllabifies apostrophe + s + obstruent resulting
    from e apheresis (’s-tan -> ’stan).
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            if (re.match(APQ_RE, sy)
                and sl[idx+1][0].lower().startswith(("t","p","c","b","v","f"))):
                sl_copy[idx] = sl[idx] + sl[idx+1]
                del sl_copy[idx+1]
        except IndexError:
            pass
    return sl_copy


def _restore_ssg(sl: list) -> list:
    """
    Resyllabify vowel sequences that do not respect sonority sequencing.
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            op_co_op = re.match(SSG_RE, sy) 
            if op_co_op:
                sl_copy[idx] = op_co_op.group(1)
                sl_copy.insert(idx+1, op_co_op.group(2) + op_co_op.group(3)) 
        except IndexError:
            pass
    return sl_copy


def _treat_coc_seqs(sl: list) -> list:
    """
    Resyllabify wrong close-open-close vowel sequences: e.g. "piei-ro" -> , "pi-ei-ro",
    but don't touch sequences where the first close vowel is preceded by 'q|g',
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            co_op_co = re.match(COC_RE, sy) 
            if co_op_co:
                sl_copy[idx] = co_op_co.group(1)
                sl_copy.insert(idx+1, co_op_co.group(2) + co_op_co.group(3)) 
        except IndexError:
            pass
    return sl_copy


def _treat_cco_seqs(sl: list) -> list:
    """
    Resyllabify wrong close-close-open vowel sequences
    (which may be non-normative): e.g. "dis-tri-bui-a" -> , "dis-tri-bu-ia"
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            co_op_co = re.match(CCO_RE, sy) 
            if co_op_co:
                sl_copy[idx] = co_op_co.group(1)
                sl_copy.insert(idx+1, co_op_co.group(2) + co_op_co.group(3)) 
        except IndexError:
            pass
    return sl_copy


def _merge_ao_contraction(sl: list) -> list:
    """
    Resyllabify 'ao' wrongly syllabified as 'a-o' into 'ao'.
    """
    sl_copy = copy(sl)
    for idx, sy in enumerate(sl):
        try:
            if sy == "a" and sl[idx+1] == "o":
                sl_copy[idx] = "ao"
                assert len(sl) == 2
                del sl_copy[idx+1] 
        except IndexError:
            pass
    return sl_copy


def _apply_fixes(sl):
    sl = _resyllabify_close_sequence(sl) # this applies
    sl = _resyllabify_homogeneous_diphthong_(sl) # this applies
    sl = _resyllabify_dieresis_ui(sl) # applies
    sl = _resyllabify_dieresis_qui(sl) # applies
    sl = _resyllabify_dieresis_general(sl) # applies
    sl = _resyllabify_stop_liquid(sl)
    sl = _resyllabify_s_liquid(sl)
    sl = _resyllabify_double_l(sl) # this works
    sl = _resyllabify_liquids(sl) # this one is relevant
    sl = _resyllabify_digraph(sl) # this applies
    sl = _restore_ssg(sl) # applies
    sl = _treat_coc_seqs(sl) # applies
    sl = _treat_cco_seqs(sl) # applies
    sl = _merge_ao_contraction(sl) # applies
    sl = _e_apheresis(sl) # applies
    return sl


def syllabify_full(word: str, diacritic:str="´", exceptions:OrderedDict=None, spanishfy=False, debug_g2s=False) -> tuple[str, str, str, int]:
    """
    Syllabification with the main algorithm plus stress marking and some
    postprocessing fixes.
    
    Args:
        word (str): The word to be syllabified, can also be multiple words
        diacritic (str): Diacritic to prefix the stressed syllable in the output.
            Default is "´" (acute accent).
        exceptions (dict): A dictionary of exceptions where keys are words and
            values are their syllabified forms. Default is None.
        spanishfy (bool): If True, adds a stress mark to final syllables with a falling
            diphthong; these bear no stress mark in Galician, but in Spanish.
            Useful since some of our tools are meant for Spanish.
    """
    global DBG ; DBG = debug_g2s
    out = ''
    # avoid variables to be not assigned if wordre is empty
    wdiac = worig = stressposi = None
    wordre = word.split(" ")
    for wr in wordre:
        wr_orig = wr
        # mark string so that exceptions to syllabification are recognized
        if exceptions:
            modif = False
            for exidx, (pat, rep) in enumerate(exceptions.items(), start=1):
                wr = re.sub(pat, rep, wr)
                if wr != wr_orig and not modif:
                    print(f"Applied exception [{exidx}]:", pat, "->", rep, "on", wr_orig, "resulting in", wr)
                    modif = True
        # syllabification
        sylls_pre = syllabify_core(wr).split("-")
        sylls_post = _apply_fixes(sylls_pre)
        # stress assignment
        wupper, wdiac, worig, stressposi = mark_stress(sylls_post, diacritic=diacritic, spanishfy=spanishfy)
        out += wupper + " "
    # TODO: only the 'wupper' version makes it to `out`, should add a check that no spaces
    # in input string actually, to parse only one word at a time and have all output variants
    out = out[:-1]
    return out, wdiac, worig, stressposi


if __name__ == "__main__":
    for x in syllabify_full("Ángel"): print(x, "|", )
