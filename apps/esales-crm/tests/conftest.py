import pytest
from server.db import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / 'crm.sqlite')
    db.save_business('converto', {'name': 'Sample Converto', 'address': 'Macau', 'email': 'billing@example.test'}, 'TEST-2026-', True)
    db.save_merchant({'code': 'SAMPLE', 'name': 'Sample Merchant', 'billing_name': 'Sample Company', 'email': 'merchant@example.test', 'address': 'Macau', 'service_end': '2026-10-22', 'plan': 'Legacy', 'currency': 'MOP'})
    return db


@pytest.fixture
def event():
    return {'env': 'production', 'data': {'code': 'sample-order', 'merchant_code': 'SAMPLE', 'merchant_name': 'Sample', 'merchant_email': 'merchant@example.test', 'currency': 'MOP', 'status': 'completed', 'items_type': 'monthly', 'merchant_subscription_end_date': '2027-10-22', 'coupon_discount': 1400, 'coupon_code': 'MONTHOFF', 'price': 16800, 'payment_price': 15400, 'payment': {'name': 'Bank transfer'}, 'items': [{'system_product_type': 'monthly', 'system_product_name': '系統月費 2000 枚易代幣', 'quantity': 12, 'unit_price': 1400}]}}
