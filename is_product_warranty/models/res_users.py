from odoo import models, fields, api

class ResUsers(models.Model):
    _inherit = 'res.users'

    # Warranty access level selection
    warranty_group = fields.Selection([
        ('none', 'None'),
        ('user', 'User'),
        ('manager', 'Manager')
    ], string="Warranty Access",
       compute='_compute_warranty_group',
       inverse='_inverse_warranty_group',
       store=True)

    # Compute access group based on current user roles
    @api.depends()
    def _compute_warranty_group(self):
        for user in self:
            if user.has_group('is_product_warranty.group_warranty_manager'):
                user.warranty_group = 'manager'
            elif user.has_group('is_product_warranty.group_warranty_user'):
                user.warranty_group = 'user'
            else:
                user.warranty_group = 'none'

    # Inverse logic to assign groups based on selection
    def _inverse_warranty_group(self):
        group_user = self.env.ref('is_product_warranty.group_warranty_user', raise_if_not_found=False)
        group_manager = self.env.ref('is_product_warranty.group_warranty_manager', raise_if_not_found=False)

        if not group_user or not group_manager:
            return

        for user in self:
            if user.warranty_group == 'manager':
                group_manager.write({'user_ids': [(4, user.id)]})
                group_user.write({'user_ids': [(4, user.id)]})
            elif user.warranty_group == 'user':
                group_manager.write({'user_ids': [(3, user.id)]})
                