{
    'name': 'Cash Report',
    'version': '19.0.0.1.2',
    'category': 'Sales',
    'summary': 'Enter cash report rows and export PDF and XLSX reports',
    'description': """Cash Report
This module allows you to enter report rows and export them to PDF and XLSX.
    """,
    'author': 'Sunray Datalinks',
    'website': 'https://www.sunraydatalinks.com',
    'depends': ['sale_management', 'web', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'views/client_action.xml',
        'views/sales_report_wizard_view.xml',
        'views/report_html_template.xml',
        'reports/report_action.xml',
        'reports/report_template.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sunray_monthly_sales_report/static/src/js/monthly_sales_client.js',
            'sunray_monthly_sales_report/static/src/xml/monthly_sales_client.xml',
        ],
    },
    'images': ['static/description/banner.gif'],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
}
