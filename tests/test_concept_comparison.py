import pytest

from playground.compare_concept_runs import compare


def test_same_partition_is_independent_of_names_and_no_positive_pairs_is_not_perfect_score():
    first = {'statuses': {'a': 'admit', 'b': 'admit'},
             'groups': {('name-one', 'shared_mechanism'): {'a', 'b'}},
             'shared': {('a', 'b', 'shared_mechanism')}, 'boundaries': {('a','b'): 'member'}}
    second = {**first, 'groups': {('totally-different-name', 'shared_mechanism'): {'a','b'}}}
    assert compare(first, second)['shared_pair_jaccard'] == 1
    other = {**first, 'groups': {}, 'shared': set()}
    assert compare(first, other)['shared_pair_jaccard'] == 0
    assert compare(other, other)['shared_pair_jaccard'] is None
    with pytest.raises(ValueError, match='same selections'):
        compare(first, {**first, 'statuses': {'a':'admit'}})
