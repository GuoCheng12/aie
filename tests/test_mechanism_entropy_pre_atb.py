"""
tests/test_mechanism_entropy_pre_atb.py

Unit tests for P5b mechanism_entropy computation.

Tests:
- Label aggregation: mode/tie/unknown behavior
- mechanism_entropy range [0,1]
- M_eff <= 1 -> entropy == 0
- Router uses mechanism_entropy threshold
"""

import json
import numpy as np
import pandas as pd
import pytest

from src.uq.mechanism_label_map import build_mechanism_label_map
from src.uq.compute_mechanism_entropy_pre_atb import (
    compute_softmax_weights,
    compute_entropy,
    compute_mechanism_entropy_for_query,
    compute_all_mechanism_entropies,
)
from src.uq.compute_uq_pre_atb_p5b import compute_router_action_p5b


class TestLabelAggregation:
    """Tests for mechanism label aggregation."""
    
    def test_mode_single_value(self):
        """Single mechanism_id should be kept as label."""
        df = pd.DataFrame({
            'inchikey': ['AAA', 'AAA'],
            'mechanism_id': ['ICT', 'ICT']
        })
        
        result = build_mechanism_label_map(df)
        
        assert len(result) == 1
        assert result.iloc[0]['mechanism_label'] == 'ICT'
        assert result.iloc[0]['label_source'] == 'mode'
        assert result.iloc[0]['is_tied'] == False
    
    def test_mode_majority(self):
        """Mode should be the majority label."""
        df = pd.DataFrame({
            'inchikey': ['AAA', 'AAA', 'AAA'],
            'mechanism_id': ['ICT', 'ICT', 'TICT']
        })
        
        result = build_mechanism_label_map(df)
        
        assert result.iloc[0]['mechanism_label'] == 'ICT'
        assert result.iloc[0]['label_source'] == 'mode'
    
    def test_tie_to_unknown(self):
        """Tie should result in 'unknown'."""
        df = pd.DataFrame({
            'inchikey': ['AAA', 'AAA'],
            'mechanism_id': ['ICT', 'TICT']
        })
        
        result = build_mechanism_label_map(df)
        
        assert result.iloc[0]['mechanism_label'] == 'unknown'
        assert result.iloc[0]['label_source'] == 'tie'
        assert result.iloc[0]['is_tied'] == True
    
    def test_all_missing_to_unknown(self):
        """All missing mechanism_id should result in 'unknown'."""
        df = pd.DataFrame({
            'inchikey': ['AAA', 'AAA'],
            'mechanism_id': [np.nan, np.nan]
        })
        
        result = build_mechanism_label_map(df)
        
        assert result.iloc[0]['mechanism_label'] == 'unknown'
        assert result.iloc[0]['label_source'] == 'all_missing'
        assert result.iloc[0]['n_nonnull'] == 0
    
    def test_partial_missing(self):
        """Partial missing should use mode of non-null values."""
        df = pd.DataFrame({
            'inchikey': ['AAA', 'AAA', 'AAA'],
            'mechanism_id': ['ICT', np.nan, 'ICT']
        })
        
        result = build_mechanism_label_map(df)
        
        assert result.iloc[0]['mechanism_label'] == 'ICT'
        assert result.iloc[0]['n_nonnull'] == 2


class TestSoftmaxWeights:
    """Tests for softmax weight computation."""
    
    def test_softmax_sums_to_one(self):
        """Softmax weights should sum to 1."""
        sims = np.array([0.5, 0.6, 0.7, 0.8])
        weights = compute_softmax_weights(sims, beta=10.0)
        
        assert np.isclose(np.sum(weights), 1.0)
    
    def test_softmax_higher_sim_higher_weight(self):
        """Higher similarity should get higher weight."""
        sims = np.array([0.3, 0.5, 0.7])
        weights = compute_softmax_weights(sims, beta=10.0)
        
        assert weights[2] > weights[1] > weights[0]
    
    def test_softmax_empty_array(self):
        """Empty array should return empty weights."""
        weights = compute_softmax_weights(np.array([]), beta=10.0)
        
        assert len(weights) == 0


class TestMechanismEntropy:
    """Tests for mechanism_entropy computation."""
    
    def test_entropy_single_label(self):
        """All neighbors same label -> entropy = 0."""
        result = compute_mechanism_entropy_for_query(
            neighbor_labels=['ICT', 'ICT', 'ICT'],
            neighbor_sims=np.array([0.8, 0.7, 0.6]),
            beta=10.0
        )
        
        assert result['mechanism_entropy'] == 0.0
        assert result['M_eff'] == 1
    
    def test_entropy_uniform_two_labels(self):
        """Two labels with equal weight -> entropy = 1.0 (max)."""
        # When similarities are equal and labels split evenly
        result = compute_mechanism_entropy_for_query(
            neighbor_labels=['ICT', 'TICT'],
            neighbor_sims=np.array([0.5, 0.5]),
            beta=0.0  # beta=0 -> uniform weights
        )
        
        # With uniform weights and 2 labels, entropy = log(2)/log(2) = 1.0
        assert np.isclose(result['mechanism_entropy'], 1.0, atol=0.01)
    
    def test_entropy_in_range(self):
        """Entropy should be in [0, 1]."""
        result = compute_mechanism_entropy_for_query(
            neighbor_labels=['ICT', 'TICT', 'AIE', 'ICT'],
            neighbor_sims=np.array([0.8, 0.6, 0.5, 0.4]),
            beta=10.0
        )
        
        assert 0 <= result['mechanism_entropy'] <= 1
    
    def test_entropy_tracks_meff(self):
        """M_eff should equal number of distinct labels."""
        result = compute_mechanism_entropy_for_query(
            neighbor_labels=['ICT', 'TICT', 'AIE'],
            neighbor_sims=np.array([0.8, 0.6, 0.4]),
            beta=10.0
        )
        
        assert result['M_eff'] == 3
    
    def test_empty_neighbors(self):
        """Empty neighbors should give NaN entropy."""
        result = compute_mechanism_entropy_for_query(
            neighbor_labels=[],
            neighbor_sims=np.array([]),
            beta=10.0
        )
        
        assert np.isnan(result['mechanism_entropy'])
        assert result['M_eff'] == 0
    
    def test_counts_unknown_neighbors(self):
        """Should count unknown neighbors correctly."""
        result = compute_mechanism_entropy_for_query(
            neighbor_labels=['ICT', 'unknown', 'unknown'],
            neighbor_sims=np.array([0.8, 0.6, 0.5]),
            beta=10.0
        )
        
        assert result['n_unknown_neighbors'] == 2


class TestRouterP5b:
    """Tests for P5b router logic."""
    
    def create_thresholds(self):
        return {
            'cov_low': 0.4,
            'cov_high': 0.7,
            'nov_high': 0.6,
            'mech_ent_high': 0.5
        }
    
    def test_evidence_insufficient_nan_c_sim(self):
        """NaN C_sim should route to Evidence-insufficient."""
        row = pd.Series({
            'C_sim': np.nan,
            'coverage': np.nan,
            'novelty': 0.5,
            'mechanism_entropy': 0.5
        })
        
        action = compute_router_action_p5b(row, self.create_thresholds())
        
        assert action == "Evidence-insufficient"
    
    def test_in_domain_ambiguous_uses_mechanism_entropy(self):
        """High mechanism_entropy should route to In-domain ambiguous."""
        row = pd.Series({
            'C_sim': 0.6,
            'coverage': 0.5,
            'novelty': 0.3,  # Below nov_high
            'mechanism_entropy': 0.7  # Above mech_ent_high
        })
        
        action = compute_router_action_p5b(row, self.create_thresholds())
        
        assert action == "In-domain ambiguous"
    
    def test_known_stable_low_mechanism_entropy(self):
        """Low mechanism_entropy + low novelty should route to Known/Stable."""
        row = pd.Series({
            'C_sim': 0.6,
            'coverage': 0.5,
            'novelty': 0.3,  # Below nov_high
            'mechanism_entropy': 0.3  # Below mech_ent_high
        })
        
        action = compute_router_action_p5b(row, self.create_thresholds())
        
        assert action == "Known/Stable"
    
    def test_novelty_candidate_with_mechanism_entropy(self):
        """High novelty + high mechanism_entropy -> Novelty-candidate."""
        row = pd.Series({
            'C_sim': 0.8,
            'coverage': 0.75,  # Above cov_high
            'novelty': 0.7,   # Above nov_high
            'mechanism_entropy': 0.6  # Above mech_ent_high
        })
        
        action = compute_router_action_p5b(row, self.create_thresholds())
        
        assert action == "Novelty-candidate"
    
    def test_router_does_not_use_aleatoric(self):
        """Router should not use aleatoric column (P5a)."""
        row = pd.Series({
            'C_sim': 0.6,
            'coverage': 0.5,
            'novelty': 0.3,
            'mechanism_entropy': 0.3,  # Below threshold
            'aleatoric': 0.999  # Very high, but should be ignored
        })
        
        action = compute_router_action_p5b(row, self.create_thresholds())
        
        # Should be Known/Stable because mechanism_entropy is low
        assert action == "Known/Stable"


class TestIntegration:
    """Integration tests."""
    
    def test_full_pipeline_smoke(self):
        """Smoke test for mechanism_entropy computation."""
        # Create synthetic label map
        label_map = pd.DataFrame({
            'inchikey': ['A', 'B', 'C', 'D'],
            'mechanism_label': ['ICT', 'TICT', 'AIE', 'ICT']
        })
        
        # Create synthetic neighbors
        neighbors = pd.DataFrame({
            'inchikey': ['A', 'A', 'B', 'B'],
            'neighbor_inchikey': ['B', 'C', 'A', 'D'],
            'rank': [1, 2, 1, 2],
            'tanimoto_sim': [0.8, 0.6, 0.7, 0.5]
        })
        
        # Compute entropies
        result = compute_all_mechanism_entropies(neighbors, label_map, beta=10.0)
        
        assert len(result) == 2  # A and B have neighbors
        assert all(result['mechanism_entropy'] >= 0)
        assert all(result['mechanism_entropy'] <= 1)


class TestMoleculeLevelThreshold:
    """Tests for molecule-level mech_ent_high threshold computation."""
    
    def test_mech_ent_high_uses_unique_inchikeys(self):
        """mech_ent_high should be computed from unique molecules, not records."""
        from src.uq.compute_uq_pre_atb_p5b import compute_thresholds_p5b
        
        # Create scores with duplicate records for same inchikey
        # Molecule A has 3 records (with same mech_ent=0.2)
        # Molecule B has 1 record (mech_ent=0.9)
        # Record-level 80th pctl would be ~0.2 (3 low values)
        # Molecule-level 80th pctl should be ~0.9 (only 2 molecules)
        scores_df = pd.DataFrame({
            'inchikey': ['A', 'A', 'A', 'B'],
            'coverage': [0.5, 0.5, 0.5, 0.6],
            'novelty': [0.4, 0.4, 0.4, 0.5],
            'mechanism_entropy': [0.2, 0.2, 0.2, 0.9]  # A has low, B has high
        })
        
        # Molecule-level mech_ent table (unique inchikeys)
        mech_ent_df = pd.DataFrame({
            'inchikey': ['A', 'B'],
            'mechanism_entropy': [0.2, 0.9]
        })
        
        thresholds = compute_thresholds_p5b(scores_df, mech_ent_df)
        
        # Verify source is molecule_level
        assert thresholds['mech_ent_high_source'] == 'molecule_level'
        
        # At molecule-level with 2 molecules, 80th pctl is between 0.2 and 0.9
        # With pandas quantile(0.8) on [0.2, 0.9]: 0.2 + 0.8*(0.9-0.2) = 0.76
        assert thresholds['mech_ent_high'] > 0.5  # Should not be ~0.2 (record-level bias)
        assert thresholds['n_molecules_for_mech_ent_high'] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

