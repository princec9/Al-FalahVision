from odoo import api, fields, models


class MaterialRequest(models.Model):
    _name = 'material.request'
    _description = 'Material Request'
    _order = 'id desc'

    name = fields.Char(
        string='Reference', required=True, copy=False,
        readonly=True, default='New',
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    source_company_id = fields.Many2one(
        'res.company', string='Source Company', required=True, readonly=True,
        default=lambda self: self.env.company,
    )
    destination_id = fields.Many2one(
        'res.company', string='Destination', required=True,
    )
    line_ids = fields.One2many(
        'material.request.line', 'request_id', string='Products',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('material.request') or 'New'
            # always the company the user is logged in with
            vals['source_company_id'] = self.env.company.id
            vals['company_id'] = self.env.company.id
        return super().create(vals_list)


class ResCompany(models.Model):
    _inherit = 'res.company'

    @api.model
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        # The destination dropdown must search and read company names outside
        # the user's allowed-company record rule. Keep its normal domain and
        # search term, while leaving all other company lookups unchanged.
        if self.env.context.get('mr_all_companies'):
            self = self.sudo()
        return super().name_search(name=name, domain=domain, operator=operator, limit=limit)

    @api.model
    def _search(self, domain, *args, **kwargs):
        # Destination dropdown: ignore the company record rule
        if self.env.context.get('mr_all_companies'):
            self = self.sudo()
        return super()._search(domain, *args, **kwargs)

    def _check_access(self, operation):
        # allow reading the company name only; write/create/unlink stay restricted
        if operation == 'read' and self.env.context.get('mr_all_companies'):
            return None
        return super()._check_access(operation)


class MaterialRequestLine(models.Model):
    _name = 'material.request.line'
    _description = 'Material Request Line'

    request_id = fields.Many2one(
        'material.request', string='Material Request',
        required=True, ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
    )
    quantity = fields.Float(
        string='Quantity', required=True, default=1.0,
        digits='Product Unit',
    )
