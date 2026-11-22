# -*- coding: utf-8 -*-
{
    'name': "Account Reimbursement",
    'summary': "Account Reimbursement",
    'author': "ANAND MC",
    'category': 'Accounting',
    'version': '1.3',
    'depends': ['base','contacts','account','accountant'],
    'data': [
        'security/ir.model.access.csv',
        'views/account_move.xml',
        'views/res_partner.xml',
    ],
    'application': True,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            'reimbursement/static/src/css/*',
        ],
    },

}
