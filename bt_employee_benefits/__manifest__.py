{
    'name': 'Employee Benefits',
    'version': '19.0.1.1.0',
    'category': 'Human Resources',
    'summary': 'Manage employee benefits, tickets, and travel requests',
    'description': """
        This module allows employees to request tickets for benefits, reimbursements, and allowances.
        HR can define Benefit Types and Accrual Plans to automate ticket allocation.
    """,
    'author': 'BroadTech IT Solutions Pvt Ltd',
    'depends': ['hr', 'mail'],
    'data': [
        'security/benefit_security.xml',
        'security/ir.model.access.csv',
        'data/benefit_accrual_cron.xml',
        'data/mail_template.xml',
        'views/benefit_type_views.xml',
        'views/benefit_request_views.xml',
        'views/benefit_accrual_plan_views.xml',
        'views/benefit_allocation_views.xml',
        'views/hr_employee_views.xml',
        'views/benefit_menu.xml',
    ],

    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
