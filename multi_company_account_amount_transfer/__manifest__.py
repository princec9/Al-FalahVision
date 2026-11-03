# -*- coding: utf-8 -*-
{
    'name': "Multi Company Account's Amount Transfer",

    'summary': """
        This module helps to make multiple journal entries from one screen and for multiple companies.""",

    'description': """
        This module helps to make multiple journal entries from one screen and for multiple companies.
        It allows you to transfer amounts from one company to another and distribute the amount to your other company accounts.
    """,

    'author': "TKL Smart Solutions",
    'website': "https://github.com/tklsmartsolutions",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Accounting',
    'version': '19.0.1.0.0',
    'depends': ['base', 'account', 'mail'],   # you use chatter, so declare mail
    # always loaded
    'data': [
        'security/multi_company_accounts_balance_transfer_groups.xml',
        'security/ir.model.access.csv',
        'data/report_paperformat.xml',
        'data/account_amount_transfer_sequence.xml',
        'views/account_amount_transfer_views.xml',
        'views/account_transfer_report.xml',
        'views/account_amount_transfer_templates.xml',

    ],
    'images': ['static/description/thumbnail.gif'],
    'installable': True,
    'auto_install': False,
    'application': True,
    'price': 55,
    'currency':  'USD',
    'support': 'tklsmartsolutions@gmail.com',
    'license': 'LGPL-3'
}
