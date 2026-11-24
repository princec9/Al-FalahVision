{
    'name': 'HR Intercompany Salary Allocation',
    'version': '19.0.1.2.0',
    'category': 'Human Resources/Payroll',
    'summary': 'Multi-company employee salary allocation and intercompany invoicing',
    'description': """
        This module enables salary cost allocation across multiple companies.
        
        Features:
        - Define contribution percentages per employee per company
        - Automatic intercompany invoice generation on payslip confirmation
        - Vendor bill auto-creation in target companies
        - Full audit trail and reversal handling
        - Contribution summary reports
    """,
    'author': 'Your Company',
    'website': 'https://yourcompany.com',
    'license': 'OPL-1',
    'depends': [
        'hr_payroll',
        'hr_work_entry_enterprise',
        'account',
        'account_accountant',
        'base_setup',
        'hr',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/hr_intercompany_salary_security.xml',

        'data/hr_intercompany_salary_data.xml',
        'data/ir_sequence_data.xml',
        'views/employee_company_contribution_views.xml',
        'views/hr_employee_views.xml',
        'views/hr_payslip_views.xml',
        'views/account_move_views.xml',
        "views/material_request_views.xml",
        'views/res_config_settings_views.xml',
        'views/hr_intercompany_salary_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [],
    },
    'demo': [],
    'installable': True,
    'auto_install': False,
    'application': False,
}
