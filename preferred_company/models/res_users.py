from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    preferred_company_id = fields.Many2one(
        'res.company',
        string='Preferred Company',
        domain="[('id', 'in', company_ids)]",
        help="This company is automatically set as the active company on login.",
    )

    def _get_session_token_fields(self):
        return super()._get_session_token_fields() | {'preferred_company_id'}

    def _compute_session_token(self, sid):
        # Invalidate session token if preferred company changes
        return super()._compute_session_token(sid)

    def write(self, vals):
        res = super().write(vals)
        if 'preferred_company_id' in vals:
            # When preferred company changes, update the user's default company
            for user in self:
                if user.preferred_company_id:
                    user.sudo().company_id = user.preferred_company_id
        return res
