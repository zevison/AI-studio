from core.hot_radar import validate_product_match


def fixture():
    return {'product_match':{'verdict':'suitable','reason_cn':'同为车用轮胎充气场景',
        'verified_claims_used':['160 PSI'],'transferable_elements_cn':['开头展示胎压表']}}, {'facts':[{'text':'160 PSI','status':'verified'},{'text':'10秒充满','status':'unverified'}]}


def test_real_claim_binding_is_required():
    document,product=fixture()
    assert validate_product_match(document,product)==[]
    document['product_match']['verified_claims_used']=['10秒充满']
    assert any('未绑定' in x for x in validate_product_match(document,product))


def test_low_fit_cannot_be_promoted_to_usable_script():
    document,product=fixture();document['product_match']['verdict']='unsuitable'
    assert any('匹配尚未通过' in x for x in validate_product_match(document,product))
    assert validate_product_match({},product)
