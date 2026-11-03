{
    'name': 'PDC Payment Details',
    'version': '19.0.1.0.0',
    'summary': 'Cheque Number and Cheque Date on payments registered with the PCD / PDC payment method',
    'description': """
When the payment method PDC is selected in the Register Payment
wizard, two extra fields appear: Cheque Number (text) and Cheque Date (date).
They are saved on the payment and shown on the payment form, the Customer /
Vendor Payments lists and the payments search.
""",
    'category': 'Accounting/Accounting',
    'author': 'Broadtech',
    'license': 'LGPL-3',
    'depends': ['account'],
    'data': [
        'views/account_payment_register_views.xml',
        'views/account_payment_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
