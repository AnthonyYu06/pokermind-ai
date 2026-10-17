import pytest
from packages.features.equity import encode,deal_family,full_deal_family
from packages.pokermind_core.contracts import Player

def test_features_suit_and_order_invariance():
    h=['As','Kh'];b=['2s','3h','7c','9d','Qs'];swap={'s':'c','c':'d','d':'h','h':'s'}
    permute=lambda cs:[c[0]+swap[c[1]] for c in reversed(cs)]
    assert encode(h,b)==encode(permute(h),permute(b))
    assert len(encode(h,b))==55
    assert deal_family(h,b)==deal_family(permute(h),permute(b))
    with pytest.raises(ValueError): encode(h,b[:4])

def test_full_deal_family_ignores_seat_labels():
    p=[Player(seat=1,stack=1000,cards=['As','Kh']),Player(seat=2,stack=1000,cards=['2d','2h'])]
    board=['3c','5s','6s','8d','Tc']
    assert full_deal_family(p,board)==full_deal_family(list(reversed(p)),list(reversed(board)))
