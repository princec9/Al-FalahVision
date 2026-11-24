{
    "name": "Intercompany Purchase & Billing",
    "version": "19.0.1.1.0",
    "category": "Purchase",
    "summary": "Automate intercompany vendor bills and customer invoices.",
    "description": """
Intercompany Purchase & Billing

Features:
- Generate intercompany vendor bills from purchase orders.
- Create counterpart customer invoices automatically.
- Support multi-company accounting flows.
- Synchronize invoicing status with purchase orders.
- Maintain document traceability through chatter messages.
- Preserve cross-company audit trail.
- Support cross-company receipt cost recomputation.
- Skip stock COGS lines on generated counterpart customer invoices.
    """,
    "author": "Broadtech It Solutions",
    "website": "https://www.broadtechitsolutions.com",
    "license": "LGPL-3",
    "depends": [
        "purchase_stock",
        "account",
        "stock_account",
        "stock",
    ],
    "data": [
        "views/purchase_order_views.xml",
        "views/account_move_views.xml",
        "views/res_company_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}