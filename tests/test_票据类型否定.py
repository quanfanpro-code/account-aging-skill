import pytest

from src.core.receivable_bills import ReceivableBillEngine


@pytest.mark.parametrize('kind', ['非商业承兑汇票','不是商业承兑','非商承','银行承兑汇票'])
def test_否定类型不能自动按商业承兑连续计龄(kind):
    assert not ReceivableBillEngine._commercial({'票据类型':kind})


def test_明确商业承兑仍可自动识别():
    assert ReceivableBillEngine._commercial({'票据类型':'商业承兑汇票'})
