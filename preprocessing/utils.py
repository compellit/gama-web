"""Utilities for syllabifiction scripts."""

from __future__ import annotations

from collections import OrderedDict
import logging
import re
import types
from typing import TYPE_CHECKING, Literal
import unicodedata


if TYPE_CHECKING:
    from stanza.models.common.doc import Token, Word

utils_logger = logging.getLogger("main.utils")


import grapheme2syllable as g2s

# stress disambiguation cases =======================================================

stressed_possessives = {
    "´méu", "´méus", "´téu", "´téus",
    "´mí-ña", "´mí-ñas",
    "´séu", "´séus",
    "´nó-sa", "´nó-sas", "´vó-sa", "´vó-sas",
    "´nó-so", "´nó-sos", "´vó-so", "´vó-sos",
    "'méu", "'méus", "'téu", "'téus",
    "'séu", "'séus",
    "'nó-sa", "'nó-sas", "'vó-sa", "'vó-sas",
    "'nó-so", "'nó-sos", "'vó-so", "'vó-sos"
}

ambiguous_possessives = {"´tú-a", "´tú-as", "´sú-a", "´sú-as"}
changed_onset_possessives = {
    "tú-a": "ttú-a",
    "tú-as": "ttú-as",
    "sú-a": "ssú-a",
    "sú-as": "ssú-as",
    "tu-a": "ttú-a",
    "tu-as": "ttú-as",
    "su-a": "ssú-a",
    "su-as": "ssú-as",
}
possessives_to_stress = {"mi-ña": "´mí-ña", "mi-ñas": "´mí-ñas",
                         "´mi-ña": "´mí-ña", "´mi-ñas": "´mí-ñas",
                         "teu": "´téu", "teus": "´téus",
                         "´teu": "´téu", "´teus": "´téus"}
# if the following precede ambiguous possessive, it is prosodically stressed
possessive_disambiguators = {"o", "os", "ó", "ós", "ô", "ao", "aos",
                             "co", "cos", "có", "cós", "do", "dos", "dó", "nó", "no", "nos",
                             "a", "as", "á", "ás", "â", "âs", "â", "âs",
                             "coa", "coas", "coá", "cóas", "da", "das", "dá", "dás", "na", "nas", "ná", "nás"}

ambiguous_interr_rel = {
    "como", "cando", "donde", "onde", "que", "quen", "cal",
    "cales", "canto", "cantos", "canta", "cantas"
}

ambigous_adv_conj = {"mais", "máis"}

ambiguous_address = {"´do-na", "´do-nas", "do-na", "do-nas"}
address_disambiguators = {"da", "das", "na", "nas", "coa", "coas"}

stressed_at_line_end = {"logo", "santa", "santas", "nosa", "vosa", "nosas", "vosas", "miña", "miñas"}
stressed_at_line_end_syll = {
    "lo-go": "´ló-go",
    "san-ta": "´sán-ta",
    "san-tas": "´sán-tas",
    "no-sa": "´nó-sa",
    "no-sas": "´nó-sas",
    "vo-sa": "´vó-sa",
    "vo-sas": "´vó-sas",
    "mi-ña": "´mí-ña",
    "mi-ñas": "´mí-ñas",
}


def load_text_replacements(config: types.ModuleType, ignore_flagged=False) -> OrderedDict[re.Pattern, str]:
    """
    Loads regex contexts and replacements from a file whose path is given at :obj:`config`.
    The replacements may affect multiple words.
    
    Args:
        config (types.ModuleType): The configuration to load.
        ignore_flagged (bool, optional): Whether to ignore expressions tagged with 'skip'
           in the data.
    Returns:
        OrderedDict[re.Pattern, str]: OrderedDict with expressions and their replacements 
    """
    replacements: OrderedDict[re.Pattern, str] = OrderedDict()
    with open(config.text_level_replacements, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            line = re.sub(" #.+", "", line)  # Remove comments
            # replacements may finish with a space, we strip all left, but right only newline
            if line.count("\t") == 2:
                key, value, action = line.lstrip().rstrip("\n\r").split("\t")
            else:
                assert line.count("\t") == 1, "Line should contain one or two tab-delimited columns."
                action = None
                key, value = line.lstrip().rstrip("\n\r").split("\t")
            if ignore_flagged and action == "skip":
                continue
            elif action == "cs":
                replacements[re.compile(fr"{key}", re.U)] = value
            else:
                replacements[re.compile(fr"{key}", re.I | re.U)] = value
    return replacements


def load_syllable_replacements_for_norm(config: types.ModuleType) -> OrderedDict[re.Pattern, tuple[str, str]]:
    """
    Loads replacements for a syllable sequence expressed as a string from a file,
    whose path is given at :obj:`config`. The replacements are used in *normalization*.

    Args:
        config: Configuration object containing the path to the syllable replacements file.

    Returns:
        OrderedDict: A dictionary with compiled regex patterns as keys and, as values,
                     a tuple with their replacements and a postprocessing instruction.
    """
    replacements: OrderedDict[re.Pattern, tuple[str, str]] = OrderedDict()
    with open(config.syllable_replacements, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            line = re.sub(" #.+", "", line)  # Remove comments
            # replacements may finish with a space, we strip all left, but right only newline
            # postpro refers to information to modify postprocessing, see the data file
            # at cf.syllable_replacements
            key, value, postpro = line.lstrip().rstrip("\n\r").split("\t")
            replacements[re.compile(fr"{key}", re.I|re.U)] = (value, postpro)
    return replacements


def load_syllable_replacements_for_g2s(config: types.ModuleType, sep=" ") -> OrderedDict[re.Pattern, tuple[str, str]]:
    """
    Loads replacements for a syllable sequence expressed as a string from a file,
    whose path is given at :obj:`config`. The replacements are used by the
    :obj:`grapheme2syllable` module for syllabification.

    Args:
        config: Configuration object containing the path to the syllable replacements file.
        sep (str): Separator used between syllables in the syllable sequences.

    Returns:
        OrderedDict: A dictionary with compiled regex patterns as keys and, as values,
                     a tuple with their replacements and a postprocessing instruction.
    """
    replacements: OrderedDict[re.Pattern, tuple[str, str]] = OrderedDict()
    with open(config.syllable_replacements_for_g2s, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            line = re.sub(" #.+", "", line)  # Remove comments
            key, value = line.lstrip().rstrip("\n\r").split(sep)
            replacements[re.compile(fr"{key}", re.I|re.U)] = value
    return replacements


def load_words_with_hyphen_to_keep(config: types.ModuleType) -> set[re.Pattern]:
    """
    Loads words with hyphen to keep from a file, whose path is given at :obj:`config`.

    Args:
        config: Configuration object containing the path to the file with words to keep.

    Returns:
        set: A set of regular expression patterns words that should be kept with hyphens.
    """
    words_to_keep: set[re.Pattern] = set()
    with open(config.words_with_hyphen_to_keep, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            line = re.sub(" #.+", "", line)  # Remove comments
            words_to_keep.add(re.compile(line.rstrip().lstrip("\n\r"), re.I | re.U))
    return words_to_keep


def destress_word(word: str, case_mask: list=None) -> str:
    """
    Remove stress marks from a word.

    Args:
        word (str): The word to destress.
        case_mask (list): A list with the same length as the word, with 1 if the character
            at that position should be uppercased, and 0 if it should be lowercased.

    Returns:
        str: The destressed word.
    """
    #TODO shouldn't the stress mark be dynamic c/o cli args?
    word = re.sub(r"[´]", "", word)  # Remove stress marks
    # keep 'ñ' position to restore it after diacritic removal (it's metrically irrelevant)
    enye_position = word.lower().find("ñ")
    normalized_word = unicodedata.normalize('NFD', word)
    unaccented_chars = [char for char in normalized_word if unicodedata.combining(char) == 0]
    if enye_position != -1:
        unaccented_chars[enye_position] = 'ñ'  # Restore 'ñ' if it was present
    if case_mask is not None:
        assert len(word) == len(case_mask), "Word and case mask must have the same length."
        final_word = []
        for cidx, cm in enumerate(case_mask):
            final_word.append(unaccented_chars[cidx].lower()) if cm == 0 else \
                final_word.append(unaccented_chars[cidx])
        return "".join(final_word)
    else:
        return "".join(unaccented_chars)


def destress_word_simple(word: str, case_mask: list=None) -> str:
    """
    Remove stress marks from a word, without case masking.
    Note: This is used with syllabified output, where the stress mark has been
    prefixed to the syllable (when applicable), that's why simply removing the stress mark
    is fine rather than removing the diacritic but keeping the vowel.

    Args:
        word (str): The word to destress.
        case_mask: (list): A list with the same length as the word, with 1 if the character
            at that position should be uppercased, and 0 if it should be lowercased.

    Returns:
        str: The destressed word.
    """
    #TODO shouldn't the stress mark be dynamic c/o cli args?
    word = re.sub(r"[´]", "", word)  # Remove stress marks
    if case_mask is not None:
        assert len(word) == len(case_mask), "Word and case mask must have the same length."
    final_word = word
    if case_mask is not None:
        final_word = []
        for cidx, cm in enumerate(case_mask):
            final_word.append(word[cidx].lower()) if cm == 0 else final_word.append(word[cidx])
        final_word = "".join(final_word)
    return final_word


def _spanishfy(tok: str, syl_list:list) -> str:
    """
    Apply Spanish orthographic stress rules to a token. Only applying
    it to the last syllable of polysyllabic words, otherwise gave too many errors.
    """
    reps = {"a": "á", "e": "é", "o": "ó"}
    newtok = None
    for diph in g2s.UNACCENTED_DIPHTHONGS_GL:
        if re.search(rf"{diph}s?$", tok.lower()):
            if not re.search(r"[áéíóú]", tok.lower()):
                newdiph = diph
                for key, value in reps.items():
                    newdiph = re.sub(key, value, newdiph)
                newtok = tok.replace(diph, newdiph)
    if newtok is not None and newtok != tok:
        utils_logger.debug(f"Spanishfied: [{tok}] to [{newtok}] context [{"".join(syl_list)}]")
        return newtok
    return tok


def detokenize(tokens: list, sep=' ') -> str:
    opening_punct = {'¡', '¿', '(', '[', '{', '«', '„'}
    closing_punct = {'!', '?', '.', ')', ']', '}', '»', '...'}

    result = []
    for token in tokens:
        if token in opening_punct:
            # attach to next word, so store as pending prefix
            result.append(token)
        elif token in closing_punct or token in {',', ';', ':'}:
            # attach to previous word (no space before)
            if result:
                result[-1] += token
            else:
                result.append(token)
        else:
            # check if last token was an opening punctuation mark
            if result and result[-1] in opening_punct:
                # merge with previous (opening punct)
                result[-1] = result[-1] + token
            else:
                result.append(token)
    return sep.join(result)


def destress_possessives(txt: str, syllabified=True) -> str:
    """
    Remove stress marks from possessive pronouns in a text.

    Args:
        txt (str): The input text.
        syllabified (bool): Whether the text is syllabified. If True, it assumes
            that the stress mark is prefixed to the syllable.

    Returns:
        str: The text with destressed possessive pronouns.
    """
    if syllabified:
        # In syllabified text, the stress mark is prefixed to the syllable
        txt = re.sub(r"\b['](séus?)\b", r"\1", txt, flags=re.I | re.U)
        txt = re.sub(r"\b[']([nv])-ós-([oa]s?)\b", r"\1-os-\2", txt, flags=re.I | re.U)
    else:
        # In non-syllabified text, the stress mark is on the vowel
        txt = re.sub(r"\b(séus?|[nv]ós[oa]s?)\b", lambda m: destress_word(m.group(0)), txt, flags=re.I | re.U)
    return txt


def offsets_in_line(line_text: str, tokens: list[str]):
    offsets = []
    i = 0
    for tok in tokens:
        start = line_text.find(tok, i)
        if start == -1:
            raise ValueError(f"Token {tok!r} not found in line starting at {i}: {line_text!r}")
        end = start + len(tok)
        offsets.append((start, end))
        i = end
    return offsets


def desambiguate_cal_word(
        calword: Word,
        wordlist: list[Word]
) -> Literal["unstressed", "unknown"]:
    """
    Desambiguate stress for 'cal' (pronoun vs. conjunction) based on features
    of the next Word object in the word list: If no singular number agreement,
    it's a conjunction (unstressed).
    
    Args:
        calword (Word): The 'cal' word to disambiguate.
        wordlist (list[Word]): The list of Word objects containing calword.
    Returns:
        Literal["unstressed", "unknown"]: The disambiguated stress status.
    """
    cal_index = wordlist.index(calword)
    next_word = wordlist[cal_index + 1] if cal_index + 1 < len(wordlist) else ""
    if next_word and next_word.feats is not None:
        if "Number=Plur" in next_word.feats:
            return "unstressed"
        else:
            return "unknown"
    else:
        return "unknown"


def desambiguate_cal_tok(
        caltok: Token,
        toklist: list[Token]
) -> Literal["unstressed", "unknown"]:
    """
    Desambiguate stress for 'cal' (pronoun vs. conjunction) based on features
    of the next Token object in the word list: : If no singular number agreement,
    it's a conjunction (unstressed).

    Args:
        caltok (Word): The 'cal' word to disambiguate.
        toklist (list[Token]): The list of Token objects containing caltok.
    Returns:
        Literal["unstressed", "unknown"]: The disambiguated stress status.
    """
    cal_index = toklist.index(caltok)
    next_tok = toklist[cal_index + 1] if cal_index + 1 < len(toklist) else ""
    if next_tok:
        if isinstance(next_tok.to_dict(), list):
            if any("Number=Plur" in w["feats"] for w in next_tok.to_dict() if w.get("feats") is not None):
                return "unstressed"
            else:
                return "unknown"
        else:
            return "unknown"
        # else:
        #     if next_tok and next_tok.to_dict()[0]["feats"] is not None:
        #         if "Number=Plur" in next_tok.to_dict()[0]["feats"]:
        #             return "unstressed"
        #         else:
        #             return "unknown"
        #     else:
        #         return "unknown"
    else:
        return "unknown"
