-- TEST ONLY cleanup.
-- Removes the local validation covered-call option leg inserted by
-- scripts/insert_test_covered_call_leg.py.
-- Does not touch real position_legs without the exact test tag/group.

DELETE FROM position_legs
WHERE tag = 'test_covered_call'
  AND group_id = 'test_cc_510300';
