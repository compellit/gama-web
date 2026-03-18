from collections import OrderedDict
import copy
import gzip
import logging
import pickle
import re
import types

from normalization import editor
from normalization import normconfig as nc
from normalization import normo as nmo


norm_logger = logging.getLogger("main.normalizer")


class Normalizer:
    
    # to cache the vocabulary, so it is not loaded again per instance
    _vocab_cache = None
    last_syll_stress_re = re.compile(r"[áéíóú][^aeiouyáéíóú]*\b", re.I | re.U)
    
    def __init__(self, norm_config: types.ModuleType, edit_costs_o: types.ModuleType=None,
                 lang: str = "gl"):
        self.cfg = norm_config
        self.lang = lang
        assert self.lang in nc.LANGUAGES, f"Language {self.lang} is not supported. Supported languages: {nc.LANGUAGES}"
        self.IVDICO = norm_config.IVDICO if self.lang == "gl" else norm_config.IVDICO_ES
        self.vocab = self._load_vocab()
        from normalization import edcosts as edit_costs
        self.edit_costs = edit_costs_o or edit_costs
        self.edimgr = self._load_editor()
    
    def _load_vocab(self):
        """Load the vocabulary (in-vocabulary words) from the configured file."""
        if Normalizer._vocab_cache is not None:
            norm_logger.info("Loading cached vocabulary")
            return Normalizer._vocab_cache
        else:
            norm_logger.info("Loading vocabulary from: [%s]", self.IVDICO)
            #with gzip.open(self.IVDICO, "rt", encoding="utf8") as f:
            #    ivs = set(line.strip() for line in f if line.strip())
            with open(self.IVDICO, "rb") as f:
                ivs = pickle.load(f)
        return ivs
    
    def _load_editor(self):
        """Prepare the editor with the given edit costs and in-vocabulary words."""
        norm_logger.info("Loading edit costs from: [%s]", self.edit_costs.__file__)
        # Create edit cost matrix
        lev_score_mat = editor.EdScoreMatrix(self.edit_costs)
        lev_score_mat.read_cost_matrix()
        # Creates a nested dictionary with costs hashed by char1 and then char2
        lev_score_mat_hash = lev_score_mat.create_matrix_hash()
        edimgr = editor.EdManager(lev_score_mat_hash, self.vocab)
        edimgr.prep_alphabet()
        return edimgr
    
    def collect_candidates(self, oov: str):
        """Collect candidates for the given OOV term."""

        cands_and_scores = set()        
        # Generate candidates using the editor
        lev_cands_str = self.edimgr.generate_levdist_candidates(oov)
        for lc in lev_cands_str:
            lev_cand = nmo.Candidate(lc, cand_type=nmo.CandType.LEV, levdist=self.edimgr.levdist(lc, oov))
            cands_and_scores.add(lev_cand)
        reg_cands_str = self.edimgr.generate_regex_candidates(oov)
        # this returns dict with cands and "regex score", under a key "cands"
        for rc in reg_cands_str["cands"]:
            reg_cand = nmo.Candidate(rc, cand_type=nmo.CandType.RGX, rgxscore=reg_cands_str["cands"][rc])
            cands_and_scores.add(reg_cand)
        return cands_and_scores
    
    def rank_candidates(self, oov, context, cand_index, cands_and_scores, lm):
        # rank first regex candidates, then lev distance candidates
        #breakpoint()
        ranked_cands = []
        for cas in cands_and_scores:
            cas.score = cas.levdist if cas.cand_type == nmo.CandType.LEV else cas.rgxscore
        for rgxcand in sorted([cd for cd in cands_and_scores if cd.cand_type == nmo.CandType.RGX], key=lambda x: -x.score):
            # score is negative. Doing minus for debugging so that can compare positive numbers (easier by hand)
            levdists = [c.levdist for c in cands_and_scores if c.cand_type == nmo.CandType.LEV]
            if len(levdists) == 0 or -rgxcand.score < -min(levdists):
                ranked_cands.append(rgxcand)
        if len(ranked_cands) == 1:
            return ranked_cands[0]
        scores_uniq = set([c.score for c in cands_and_scores])
        scores_groups = OrderedDict()
        for sco in sorted(scores_uniq):
            scores_groups[sco] = sorted([c for c in cands_and_scores if c.score == sco])
        # in some cases distance is zero even after an accent edit
        if 0 in scores_groups:
            if len(scores_groups[0]) == 1:
                norm_logger.debug(f"0 distance candidate: [{scores_groups[0][0]}]")
                return scores_groups[0][0]
        # if there is a single candidate with score -0.5, return it
        if -0.5 in scores_groups:
            if len(scores_groups[-0.5]) == 1:
                test_seq = copy.deepcopy(context)
                try:
                    test_seq[cand_index] = scores_groups[-0.5][0].form
                except IndexError:
                    logging.debug(f"Warning: IndexError: {cand_index} in context of length {len(context)} for OOV '{oov}'")
                    cand_index = len(test_seq) - 1
                    test_seq[cand_index] = scores_groups[-0.5][0].form
                clc, crc = lm.find_context_for_token(scores_groups[-0.5][0].form, cand_index, test_seq)
                cand_sco = lm.find_logprob_in_context(scores_groups[-0.5][0].form, (clc, crc))
                orig_lc, orig_rc = lm.find_context_for_token(oov, cand_index, [x.replace("-", "") for x in context])
                orig_sco = lm.find_logprob_in_context(oov, (orig_lc, orig_rc))                
                if cand_sco > orig_sco:
                    norm_logger.debug(f"Norm Candidate with score -0.5: [{scores_groups[-0.5][0]}] with score {cand_sco} > original score {orig_sco}")
                    return scores_groups[-0.5][0]
                else:
                    return scores_groups[-0.5][0]
            else:
                # If more than one candidate with score -0.5, check for case matching
                matching_case_cand = self.select_with_case_mask(oov, scores_groups[-0.5])
                if matching_case_cand is not None:
                    norm_logger.debug(f"Norm Matching Case: [{matching_case_cand}] against [{[cnd.form for cnd in scores_groups[-0.5]]}])")
                    return matching_case_cand
                # If one of the candidates inserts a diacritic in the last syllable, return it
                for cand in scores_groups[-0.5]: 
                   if re.search(self.last_syll_stress_re, cand.form):
                       norm_logger.debug(f"Norm Candidate with diacritic: [{cand}]")
                       return cand
        # All other cases to be implemented
        return None
    
    def create_case_mask(self, oov):
        """Create a case signature for the OOV term."""
        case_mask = [1 if c.isupper() else 0 for c in oov]
        return case_mask

    def select_with_case_mask(self, oov, candidates):
        """Select a candidate that matches the case of the OOV term."""
        case_mask_oov = self.create_case_mask(oov)
        candidate_masks = [self.create_case_mask(c.form) for c in candidates]
        matching_cases = sorted([cand for cand, mask in zip(candidates, candidate_masks) if mask == case_mask_oov])
        if len(matching_cases) == 1:
            return matching_cases[0]
        else:
            return None
