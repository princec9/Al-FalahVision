# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Compatibility shim for databases carrying older inherited settings views.
    disable_many2one_quick_create = fields.Boolean(
        string="Disable Many2one Quick Create",
        default=False,
    )
    mcp_session_timeout = fields.Integer(
        string="MCP Session Timeout",
        default=0,
        config_parameter="odoo_ecommerce.mcp_session_timeout",
    )

    # MCP Log Retention (days)
    # Controls how many days MCP logs are kept. 0 = keep forever.
    mcp_log_retention = fields.Integer(
        string="MCP Log Retention (days)",
        default=30,
        config_parameter="odoo_ecommerce.mcp_log_retention",
        help="Number of days to retain MCP logs. Set to 0 to retain indefinitely.",
    )
