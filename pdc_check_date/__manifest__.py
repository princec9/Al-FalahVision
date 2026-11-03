{
    'name': 'PDC Check Date',
    'version': '19.0.1.0.1',
    'category': 'Accounting',
    'summary': 'Check Date field on payments, shown only when the payment method is PDC',
    'depends': ['account'],
    'data': [
        'views/account_payment_views.xml',
        'views/account_payment_register_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
