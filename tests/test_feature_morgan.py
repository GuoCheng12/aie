from src.structure.feature_morgan import compute_feature_morgan_count, compute_morgan_count, count_tanimoto


def test_feature_morgan_count_is_stable_and_nonempty():
    smiles = "Oc1ccccc1N"
    fp1 = compute_feature_morgan_count(smiles)
    fp2 = compute_feature_morgan_count(smiles)
    assert fp1
    assert fp1 == fp2


def test_count_tanimoto_behaves_for_identical_and_different_molecules():
    same_a = compute_morgan_count("Oc1ccccc1N")
    same_b = compute_morgan_count("Oc1ccccc1N")
    other = compute_morgan_count("CCCC")
    assert count_tanimoto(same_a, same_b) == 1.0
    assert 0.0 <= count_tanimoto(same_a, other) < 1.0
